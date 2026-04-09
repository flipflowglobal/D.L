//! dex.rs — On-chain DEX price queries via alloy-rs.
//!
//! Queries Uniswap V3 (3 fee tiers) and SushiSwap V2 concurrently
//! using tokio::join!.  All calls are eth_call (read-only, no gas).
//!
//! Mainnet contract addresses:
//!   Uniswap V3 Quoter V1 : 0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6
//!   SushiSwap Router V2  : 0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F
//!   WETH                 : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
//!   USDC                 : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use alloy::network::Ethereum;
use alloy::primitives::aliases::U24;
use alloy::primitives::{Address, U256};
use alloy::providers::{Provider, RootProvider};
use alloy::sol;
use tracing::{debug, warn};

use crate::config::OracleConfig;
use crate::error::{OracleError, OracleResult};

// ── Contract ABIs (generated at compile time via sol! macro) ─────────────────

sol! {
    /// Uniswap V3 Quoter V1 — quoteExactInputSingle
    #[sol(rpc)]
    interface IUniswapV3Quoter {
        function quoteExactInputSingle(
            address tokenIn,
            address tokenOut,
            uint24  fee,
            uint256 amountIn,
            uint256 sqrtPriceLimitX96
        ) external returns (uint256 amountOut);
    }
}

sol! {
    /// SushiSwap V2 Router — getAmountsOut
    #[sol(rpc)]
    interface ISushiRouter {
        function getAmountsOut(
            uint256          amountIn,
            address[] memory path
        ) external view returns (uint256[] memory amounts);
    }
}

// ── Mainnet addresses ─────────────────────────────────────────────────────────

const UNISWAP_QUOTER: &str = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6";
const SUSHI_ROUTER:   &str = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F";
const WETH:           &str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2";
const USDC:           &str = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

// Uniswap V3 fee tiers (raw u32 → converted to U24 at call site)
const FEE_LOW:    u32 = 500;    // 0.05 %
const FEE_MEDIUM: u32 = 3000;  // 0.30 %
const FEE_HIGH:   u32 = 10000; // 1.00 %

// 1 WETH = 1e18 wei
const ONE_ETH_WEI: u128 = 1_000_000_000_000_000_000;
// USDC decimals
const USDC_DECIMALS: f64 = 1_000_000.0;

/// Concrete provider type.
/// In alloy 1.x, RootProvider<N> erases the transport; N is the Network.
pub type HttpProvider = RootProvider<Ethereum>;

// ── DexOracle ─────────────────────────────────────────────────────────────────

pub struct DexOracle {
    provider:        Option<Arc<HttpProvider>>,
    uni_quoter_addr: Address,
    sushi_addr:      Address,
    weth_addr:       Address,
    usdc_addr:       Address,
    config:          Arc<OracleConfig>,
}

impl DexOracle {
    /// Build a new DexOracle.  If no RPC_URL is configured, runs in
    /// simulation mode (returns synthetic prices with ±1 % noise).
    pub async fn new(config: Arc<OracleConfig>) -> Self {
        let provider: Option<Arc<HttpProvider>> = if let Some(rpc_url) = &config.rpc_url {
            match rpc_url.parse::<url::Url>() {
                Ok(url) => {
                    // RootProvider::new_http — zero-cost, synchronous, no filler overhead
                    let p = HttpProvider::new_http(url);
                    // Test connectivity with timeout
                    match tokio::time::timeout(
                        Duration::from_millis(config.rpc_timeout_ms),
                        p.get_block_number(),
                    ).await {
                        Ok(Ok(bn)) => {
                            tracing::info!("Connected to RPC, block #{}", bn);
                            Some(Arc::new(p))
                        }
                        Ok(Err(e)) => {
                            warn!("RPC connect test failed: {} — simulation mode", e);
                            None
                        }
                        Err(_) => {
                            warn!("RPC connect timed out — simulation mode");
                            None
                        }
                    }
                }
                Err(e) => {
                    warn!("Invalid RPC URL '{}': {} — simulation mode", rpc_url, e);
                    None
                }
            }
        } else {
            None
        };

        Self {
            provider,
            uni_quoter_addr: UNISWAP_QUOTER.parse().expect("bad quoter addr"),
            sushi_addr:      SUSHI_ROUTER.parse().expect("bad sushi addr"),
            weth_addr:       WETH.parse().expect("bad weth addr"),
            usdc_addr:       USDC.parse().expect("bad usdc addr"),
            config,
        }
    }

    pub fn is_connected(&self) -> bool {
        self.provider.is_some()
    }

    // ── Uniswap V3 ───────────────────────────────────────────────────────────

    /// Quote WETH → USDC price for a single fee tier via eth_call.
    async fn uni_quote_fee(&self, fee: u32) -> OracleResult<f64> {
        let provider = self.provider.as_ref().ok_or(OracleError::NoPriceData)?;

        let quoter = IUniswapV3Quoter::new(self.uni_quoter_addr, provider.as_ref());

        // fee: u32 → U24 (uint24); all tier values (500/3000/10000) fit in 24 bits
        let fee_u24 = U24::try_from(fee)
            .map_err(|_| OracleError::Rpc(format!("fee {} overflows uint24", fee)))?;

        let call = quoter.quoteExactInputSingle(
            self.weth_addr,
            self.usdc_addr,
            fee_u24,
            U256::from(ONE_ETH_WEI),
            U256::ZERO,
        );

        // alloy 1.x: single-return sol! functions return the value directly
        let amount_out: U256 = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            call.call(),
        )
        .await
        .map_err(|_| OracleError::Rpc(format!("Timeout on fee={}", fee)))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        // Convert from U256 to f64; USDC has 6 decimals
        let price = u128::try_from(amount_out)
            .map(|v| v as f64 / USDC_DECIMALS)
            .map_err(|_| OracleError::Decode("U256 overflow on amountOut".into()))?;

        debug!("Uniswap fee={} price=${:.2}", fee, price);
        Ok(price)
    }

    /// Fetch the best Uniswap V3 ETH price across ALL fee tiers concurrently.
    ///
    /// All 3 fee-tier calls are launched simultaneously via tokio::join!.
    /// Wall-clock latency = max(single_call_latency) instead of 3×.
    pub async fn best_uniswap_price(&self) -> OracleResult<f64> {
        let (r_low, r_med, r_high) = tokio::join!(
            self.uni_quote_fee(FEE_LOW),
            self.uni_quote_fee(FEE_MEDIUM),
            self.uni_quote_fee(FEE_HIGH),
        );

        let prices: Vec<f64> = [r_low, r_med, r_high]
            .into_iter()
            .filter_map(|r| r.ok())
            .filter(|&p| p > 0.0)
            .collect();

        prices
            .into_iter()
            .reduce(f64::max)
            .ok_or(OracleError::NoPriceData)
    }

    /// Return all three fee-tier prices (for diagnostics / the /prices/uniswap endpoint).
    pub async fn all_uniswap_prices(&self) -> HashMap<String, f64> {
        let (r_low, r_med, r_high) = tokio::join!(
            self.uni_quote_fee(FEE_LOW),
            self.uni_quote_fee(FEE_MEDIUM),
            self.uni_quote_fee(FEE_HIGH),
        );

        let mut map = HashMap::new();
        if let Ok(p) = r_low  { map.insert("uniswap_v3_500".into(),   p); }
        if let Ok(p) = r_med  { map.insert("uniswap_v3_3000".into(),  p); }
        if let Ok(p) = r_high { map.insert("uniswap_v3_10000".into(), p); }
        map
    }

    // ── SushiSwap V2 ─────────────────────────────────────────────────────────

    /// Fetch ETH/USDC price from SushiSwap V2 via getAmountsOut.
    pub async fn sushiswap_price(&self) -> OracleResult<f64> {
        let provider = self.provider.as_ref().ok_or(OracleError::NoPriceData)?;

        let router = ISushiRouter::new(self.sushi_addr, provider.as_ref());

        let path: Vec<Address> = vec![self.weth_addr, self.usdc_addr];

        let call = router.getAmountsOut(U256::from(ONE_ETH_WEI), path);

        // alloy 1.x: multi-element array return → Vec<U256> directly
        let amounts: Vec<U256> = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            call.call(),
        )
        .await
        .map_err(|_| OracleError::Rpc("SushiSwap timeout".into()))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        if amounts.len() < 2 {
            return Err(OracleError::Decode("getAmountsOut returned < 2 values".into()));
        }

        let price = u128::try_from(amounts[1])
            .map(|v| v as f64 / USDC_DECIMALS)
            .map_err(|_| OracleError::Decode("U256 overflow on amounts[1]".into()))?;

        debug!("SushiSwap price=${:.2}", price);
        Ok(price)
    }

    // ── CoinGecko fallback ────────────────────────────────────────────────────

    pub async fn coingecko_price(&self) -> OracleResult<f64> {
        let url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd";

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .map_err(OracleError::Network)?;

        let resp: serde_json::Value = client
            .get(url)
            .send()
            .await
            .map_err(OracleError::Network)?
            .json()
            .await
            .map_err(OracleError::Network)?;

        resp["ethereum"]["usd"]
            .as_f64()
            .ok_or(OracleError::Decode("CoinGecko response missing price".into()))
    }

    // ── Combined fetch (Uniswap + SushiSwap in parallel) ─────────────────────

    /// Fetch ALL prices concurrently:
    ///   - Uniswap V3 (3 fee tiers simultaneously)
    ///   - SushiSwap V2
    ///
    /// Total concurrent RPC calls: 4 (all running via tokio::join!).
    /// Wall-clock latency = max(slowest_single_call).
    pub async fn fetch_all_prices(&self) -> OracleResult<HashMap<String, f64>> {
        if !self.is_connected() {
            return Ok(self.simulated_prices().await);
        }

        let (uni_result, sushi_result) = tokio::join!(
            self.best_uniswap_price(),
            self.sushiswap_price(),
        );

        let mut prices = HashMap::new();

        match uni_result {
            Ok(p)  => { prices.insert("uniswap_v3".to_string(), p); }
            Err(e) => { warn!("Uniswap price failed: {}", e); }
        }
        match sushi_result {
            Ok(p)  => { prices.insert("sushiswap".to_string(), p); }
            Err(e) => { warn!("SushiSwap price failed: {}", e); }
        }

        // If both on-chain sources failed, fall back to CoinGecko
        if prices.is_empty() {
            warn!("Both DEX sources failed — falling back to CoinGecko");
            match self.coingecko_price().await {
                Ok(p)  => { prices.insert("coingecko".to_string(), p); }
                Err(e) => {
                    warn!("CoinGecko also failed: {}", e);
                    prices.insert("fallback".to_string(), self.config.fallback_price);
                }
            }
        }

        Ok(prices)
    }

    /// Synthetic prices for simulation mode (no RPC needed).
    async fn simulated_prices(&self) -> HashMap<String, f64> {
        let base = self.coingecko_price().await
            .unwrap_or(self.config.fallback_price);

        // Deterministic noise using subsecond timestamp — no rand crate needed
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .subsec_nanos() as f64;

        let noise_a = ((nanos % 1000.0) / 1000.0 - 0.5) * 0.02;  // ±1 %
        let noise_b = ((nanos % 997.0)  / 997.0  - 0.5) * 0.02;  // ±1 %

        let mut m = HashMap::new();
        m.insert("uniswap_v3".into(), base * (1.0 + noise_a));
        m.insert("sushiswap".into(),  base * (1.0 + noise_b));
        m
    }

    // ── Arbitrage evaluation ──────────────────────────────────────────────────

    /// Evaluate whether the current prices contain an arbitrage opportunity.
    pub fn evaluate_arbitrage(
        &self,
        prices: &HashMap<String, f64>,
    ) -> Option<serde_json::Value> {
        if prices.len() < 2 {
            return None;
        }

        let min_dex = prices.iter().min_by(|a, b| a.1.partial_cmp(b.1).unwrap())?;
        let max_dex = prices.iter().max_by(|a, b| a.1.partial_cmp(b.1).unwrap())?;

        let spread = (max_dex.1 - min_dex.1) / min_dex.1;

        if spread >= self.config.spread_threshold {
            let gross_profit_pct = spread - 0.006; // deduct 0.3% fee each leg
            Some(serde_json::json!({
                "buy_on":         min_dex.0,
                "buy_price":      (min_dex.1 * 10000.0).round() / 10000.0,
                "sell_on":        max_dex.0,
                "sell_price":     (max_dex.1 * 10000.0).round() / 10000.0,
                "spread_pct":     (spread * 1_000_000.0).round() / 10000.0,
                "est_profit_pct": (gross_profit_pct * 1_000_000.0).round() / 10000.0,
            }))
        } else {
            None
        }
    }
}

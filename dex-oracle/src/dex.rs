//! dex.rs — On-chain DEX price queries via alloy-rs.
//!
//! Queries all five DEXes supported by NexusFlashReceiver concurrently
//! using tokio::join!.  All calls are eth_call (read-only, no gas).
//!
//! DEX sources (in priority order for arbitrage evaluation):
//!   Uniswap V3 Quoter V1  : 0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6 (mainnet)
//!   SushiSwap Router V2   : 0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F (mainnet)
//!   Curve TriCrypto2      : 0xD51a44d3FaE010294C616388b506AcdA1bfAAE46 (mainnet)
//!   Balancer V2 Vault     : 0xBA12222222228d8Ba445958a75a0704d566BF2C8 (mainnet)
//!   Camelot V2 Router     : configurable via CAMELOT_ROUTER_ADDR (Arbitrum)

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use alloy::network::Ethereum;
use alloy::primitives::aliases::U24;
use alloy::primitives::{Address, FixedBytes, U256};
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
    /// SushiSwap V2 Router — getAmountsOut (also used for Camelot V2)
    #[sol(rpc)]
    interface ISushiRouter {
        function getAmountsOut(
            uint256          amountIn,
            address[] memory path
        ) external view returns (uint256[] memory amounts);
    }
}

sol! {
    /// Curve TriCrypto / crypto-swap pools — get_dy (view price, no execution)
    /// Coin indices for TriCrypto2 (mainnet): 0=USDT, 1=WBTC, 2=WETH
    #[sol(rpc)]
    interface ICurveTricrypto {
        function get_dy(uint256 i, uint256 j, uint256 dx) external view returns (uint256 dy);
    }
}

sol! {
    /// Balancer V2 Vault — getPoolTokens (returns token balances for spot price)
    #[sol(rpc)]
    interface IBalancerVaultQuery {
        function getPoolTokens(bytes32 poolId) external view returns (
            address[] memory tokens,
            uint256[] memory balances,
            uint256 lastChangeBlock
        );
    }
}

// ── Mainnet addresses ─────────────────────────────────────────────────────────

const UNISWAP_QUOTER: &str = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6";
const SUSHI_ROUTER:   &str = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F";
const WETH:           &str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2";
const USDC:           &str = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

// Curve TriCrypto2 on mainnet: coins = [USDT(0), WBTC(1), WETH(2)]
// get_dy(i=2, j=0, dx=1e18) → USDT per 1 WETH  (USDT ≈ USDC, both 6 dec)
const CURVE_TRICRYPTO2:    &str = "0xD51a44d3FaE010294C616388b506AcdA1bfAAE46";
const CURVE_WETH_IDX:      u64  = 2;
const CURVE_STABLEOUT_IDX: u64  = 0;

// Balancer V2 Vault (immutable across mainnet)
const BALANCER_VAULT: &str = "0xBA12222222228d8Ba445958a75a0704d566BF2C8";
// WETH/USDC 50/50 weighted pool on mainnet
const BALANCER_WETH_USDC_POOL_ID: &str =
    "0x96646936b91d6b9d7d0c47c496afbf3d6ec7b6f5000200000000000000000019";

// Uniswap V3 fee tiers (raw u32 → converted to U24 at call site)
const FEE_LOW:    u32 = 500;    // 0.05 %
const FEE_MEDIUM: u32 = 3000;  // 0.30 %
const FEE_HIGH:   u32 = 10000; // 1.00 %

// 1 WETH = 1e18 wei
const ONE_ETH_WEI: u128 = 1_000_000_000_000_000_000;
// USDC / USDT decimals (both 6)
const STABLE_DECIMALS: f64 = 1_000_000.0;

/// Concrete provider type.
pub type HttpProvider = RootProvider<Ethereum>;

// ── DexOracle ─────────────────────────────────────────────────────────────────

pub struct DexOracle {
    provider:            Option<Arc<HttpProvider>>,
    uni_quoter_addr:     Address,
    sushi_addr:          Address,
    weth_addr:           Address,
    usdc_addr:           Address,
    // ── Extended DEX support ─────────────────────────────────────────────────
    curve_pool_addr:     Option<Address>,
    balancer_vault_addr: Address,
    balancer_pool_id:    FixedBytes<32>,
    camelot_addr:        Option<Address>,
    camelot_weth:        Option<Address>,
    camelot_usdc:        Option<Address>,
    config:              Arc<OracleConfig>,
}

impl DexOracle {
    /// Build a new DexOracle.  If no RPC_URL is configured, runs in
    /// simulation mode (returns synthetic prices with ±1 % noise).
    pub async fn new(config: Arc<OracleConfig>) -> Self {
        let provider: Option<Arc<HttpProvider>> = if let Some(rpc_url) = &config.rpc_url {
            match rpc_url.parse::<url::Url>() {
                Ok(url) => {
                    let p = HttpProvider::new_http(url);
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

        // ── Parse optional extended DEX addresses ─────────────────────────────

        // Curve pool — default to mainnet TriCrypto2, disable if set to empty
        let curve_pool_addr: Option<Address> = config
            .curve_pool_addr
            .as_deref()
            .unwrap_or(CURVE_TRICRYPTO2)
            .parse()
            .map_err(|e| {
                warn!("Invalid CURVE_POOL_ADDR: {} — Curve disabled", e);
            })
            .ok();

        // Balancer Vault
        let balancer_vault_addr: Address = config
            .balancer_vault_addr
            .as_deref()
            .unwrap_or(BALANCER_VAULT)
            .parse()
            .unwrap_or_else(|_| {
                warn!("Invalid BALANCER_VAULT_ADDR — using default");
                BALANCER_VAULT.parse().expect("hard-coded balancer vault addr valid")
            });

        // Balancer pool ID
        let balancer_pool_id: FixedBytes<32> = config
            .balancer_pool_id
            .as_deref()
            .unwrap_or(BALANCER_WETH_USDC_POOL_ID)
            .parse()
            .unwrap_or_else(|_| {
                warn!("Invalid BALANCER_POOL_ID — using default");
                BALANCER_WETH_USDC_POOL_ID
                    .parse()
                    .expect("hard-coded balancer pool id valid")
            });

        // Camelot router (optional — only active if env var is set)
        let camelot_addr: Option<Address> = config
            .camelot_router_addr
            .as_deref()
            .and_then(|s| s.parse().ok());

        let camelot_weth: Option<Address> = config
            .camelot_weth_addr
            .as_deref()
            .and_then(|s| s.parse().ok());

        let camelot_usdc: Option<Address> = config
            .camelot_usdc_addr
            .as_deref()
            .and_then(|s| s.parse().ok());

        if camelot_addr.is_some() {
            tracing::info!("Camelot V2 pricing enabled");
        }

        Self {
            provider,
            uni_quoter_addr: UNISWAP_QUOTER.parse().expect("bad quoter addr"),
            sushi_addr:      SUSHI_ROUTER.parse().expect("bad sushi addr"),
            weth_addr:       WETH.parse().expect("bad weth addr"),
            usdc_addr:       USDC.parse().expect("bad usdc addr"),
            curve_pool_addr,
            balancer_vault_addr,
            balancer_pool_id,
            camelot_addr,
            camelot_weth,
            camelot_usdc,
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

        let fee_u24 = U24::try_from(fee)
            .map_err(|_| OracleError::Rpc(format!("fee {} overflows uint24", fee)))?;

        let call = quoter.quoteExactInputSingle(
            self.weth_addr,
            self.usdc_addr,
            fee_u24,
            U256::from(ONE_ETH_WEI),
            U256::ZERO,
        );

        let amount_out: U256 = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            call.call(),
        )
        .await
        .map_err(|_| OracleError::Rpc(format!("Timeout on fee={}", fee)))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        let price = u128::try_from(amount_out)
            .map(|v| v as f64 / STABLE_DECIMALS)
            .map_err(|_| OracleError::Decode("U256 overflow on amountOut".into()))?;

        debug!("Uniswap fee={} price=${:.2}", fee, price);
        Ok(price)
    }

    /// Fetch the best Uniswap V3 ETH price across ALL fee tiers concurrently.
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

    /// Return all three fee-tier prices (for /prices/uniswap endpoint).
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
        self.amm_v2_price(self.sushi_addr, self.weth_addr, self.usdc_addr, "SushiSwap").await
    }

    // ── Curve TriCrypto ───────────────────────────────────────────────────────

    /// Fetch ETH/USD price from Curve TriCrypto via get_dy (view, no execution).
    ///
    /// Uses the TriCrypto2 pool (USDT/WBTC/WETH) on mainnet by default.
    /// get_dy(i=2, j=0, dx=1e18) → USDT per 1 WETH (USDT ≈ USDC, 6 decimals).
    /// Returns None (NoPriceData) if Curve is disabled (CURVE_POOL_ADDR not set
    /// to a valid address).
    pub async fn curve_price(&self) -> OracleResult<f64> {
        let pool_addr = self.curve_pool_addr.ok_or(OracleError::NoPriceData)?;
        let provider  = self.provider.as_ref().ok_or(OracleError::NoPriceData)?;

        let pool = ICurveTricrypto::new(pool_addr, provider.as_ref());

        let dy: U256 = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            pool.get_dy(
                U256::from(CURVE_WETH_IDX),
                U256::from(CURVE_STABLEOUT_IDX),
                U256::from(ONE_ETH_WEI),
            ).call(),
        )
        .await
        .map_err(|_| OracleError::Rpc("Curve get_dy timeout".into()))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        let price = u128::try_from(dy)
            .map(|v| v as f64 / STABLE_DECIMALS)
            .map_err(|_| OracleError::Decode("Curve dy U256 overflow".into()))?;

        if price <= 0.0 {
            return Err(OracleError::NoPriceData);
        }

        debug!("Curve TriCrypto price=${:.2}", price);
        Ok(price)
    }

    // ── Balancer V2 ───────────────────────────────────────────────────────────

    /// Fetch WETH/USDC spot price from Balancer V2 via getPoolTokens.
    ///
    /// Calls `getPoolTokens(poolId)` on the Balancer Vault to read token
    /// balances, then computes spot price for a 50/50 weighted pool:
    ///   spot = (usdc_balance / 1e6) / (weth_balance / 1e18)
    ///
    /// This is a read-only eth_call — no swap is executed.
    pub async fn balancer_price(&self) -> OracleResult<f64> {
        let provider = self.provider.as_ref().ok_or(OracleError::NoPriceData)?;

        let vault = IBalancerVaultQuery::new(self.balancer_vault_addr, provider.as_ref());

        let ret = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            vault.getPoolTokens(self.balancer_pool_id).call(),
        )
        .await
        .map_err(|_| OracleError::Rpc("Balancer getPoolTokens timeout".into()))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        let tokens   = ret.tokens;
        let balances = ret.balances;

        if tokens.len() != balances.len() || tokens.is_empty() {
            return Err(OracleError::Decode("Balancer: tokens/balances length mismatch".into()));
        }

        // Locate WETH and USDC in the token list
        let weth_pos = tokens.iter().position(|t| *t == self.weth_addr)
            .ok_or_else(|| OracleError::Decode("WETH not found in Balancer pool".into()))?;
        let usdc_pos = tokens.iter().position(|t| *t == self.usdc_addr)
            .ok_or_else(|| OracleError::Decode("USDC not found in Balancer pool".into()))?;

        let weth_balance = u128::try_from(balances[weth_pos])
            .map_err(|_| OracleError::Decode("WETH balance U256 overflow".into()))? as f64;
        let usdc_balance = u128::try_from(balances[usdc_pos])
            .map_err(|_| OracleError::Decode("USDC balance U256 overflow".into()))? as f64;

        if weth_balance == 0.0 {
            return Err(OracleError::NoPriceData);
        }

        // Spot price for 50/50 pool:
        //   price_usdc = (usdc_balance / 1e6) / (weth_balance / 1e18)
        //              = usdc_balance * 1e12 / weth_balance
        let price = (usdc_balance * 1e12) / weth_balance;

        if price <= 0.0 {
            return Err(OracleError::NoPriceData);
        }

        debug!("Balancer WETH/USDC spot price=${:.2}", price);
        Ok(price)
    }

    // ── Camelot V2 ───────────────────────────────────────────────────────────

    /// Fetch ETH/USDC price from Camelot V2 (Arbitrum AMM fork).
    ///
    /// Camelot uses the same AMM V2 interface as SushiSwap V2.
    /// Only active if CAMELOT_ROUTER_ADDR is configured.
    /// Returns NoPriceData if Camelot is not configured (mainnet default).
    pub async fn camelot_price(&self) -> OracleResult<f64> {
        let router_addr = self.camelot_addr.ok_or(OracleError::NoPriceData)?;
        // Use chain-specific WETH/USDC or fall back to mainnet addresses
        let weth = self.camelot_weth.unwrap_or(self.weth_addr);
        let usdc = self.camelot_usdc.unwrap_or(self.usdc_addr);
        self.amm_v2_price(router_addr, weth, usdc, "Camelot").await
    }

    // ── AMM V2 generic helper ─────────────────────────────────────────────────

    /// Internal: fetch ETH/USDC price from any AMM V2-compatible router.
    async fn amm_v2_price(
        &self,
        router_addr: Address,
        weth:        Address,
        usdc:        Address,
        label:       &str,
    ) -> OracleResult<f64> {
        let provider = self.provider.as_ref().ok_or(OracleError::NoPriceData)?;

        let router = ISushiRouter::new(router_addr, provider.as_ref());
        let path: Vec<Address> = vec![weth, usdc];
        let call = router.getAmountsOut(U256::from(ONE_ETH_WEI), path);

        let amounts: Vec<U256> = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            call.call(),
        )
        .await
        .map_err(|_| OracleError::Rpc(format!("{} getAmountsOut timeout", label)))?
        .map_err(|e| OracleError::Rpc(e.to_string()))?;

        if amounts.len() < 2 {
            return Err(OracleError::Decode(format!(
                "{} getAmountsOut returned < 2 values",
                label
            )));
        }

        let price = u128::try_from(amounts[1])
            .map(|v| v as f64 / STABLE_DECIMALS)
            .map_err(|_| OracleError::Decode(format!("{} amounts[1] U256 overflow", label)))?;

        debug!("{} price=${:.2}", label, price);
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

    // ── Combined fetch (all 5 DEX sources in parallel) ────────────────────────

    /// Fetch ALL prices concurrently across all configured DEX sources:
    ///   - Uniswap V3 (3 fee tiers internally parallel)
    ///   - SushiSwap V2
    ///   - Curve TriCrypto (if CURVE_POOL_ADDR configured or mainnet default)
    ///   - Balancer V2 (mainnet default pool, configurable)
    ///   - Camelot V2 (only if CAMELOT_ROUTER_ADDR is set)
    ///
    /// Total concurrent RPC calls: up to 7 (all via tokio::join!).
    /// Wall-clock latency = max(slowest_single_call).
    pub async fn fetch_all_prices(&self) -> OracleResult<HashMap<String, f64>> {
        if !self.is_connected() {
            return Ok(self.simulated_prices().await);
        }

        let (uni_result, sushi_result, curve_result, balancer_result, camelot_result) =
            tokio::join!(
                self.best_uniswap_price(),
                self.sushiswap_price(),
                self.curve_price(),
                self.balancer_price(),
                self.camelot_price(),
            );

        let mut prices = HashMap::new();

        macro_rules! insert_price {
            ($result:expr, $key:expr) => {
                match $result {
                    Ok(p)  => { prices.insert($key.to_string(), p); }
                    Err(OracleError::NoPriceData) => {} // silently skip disabled sources
                    Err(e) => { warn!("{} price failed: {}", $key, e); }
                }
            };
        }

        insert_price!(uni_result,     "uniswap_v3");
        insert_price!(sushi_result,   "sushiswap");
        insert_price!(curve_result,   "curve");
        insert_price!(balancer_result,"balancer");
        insert_price!(camelot_result, "camelot");

        // If ALL on-chain sources failed, fall back to CoinGecko
        if prices.is_empty() {
            warn!("All DEX sources failed — falling back to CoinGecko");
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

        let noise = |salt: f64| ((nanos % salt) / salt - 0.5) * 0.02;

        let mut m = HashMap::new();
        m.insert("uniswap_v3".into(), base * (1.0 + noise(1000.0)));
        m.insert("sushiswap".into(),  base * (1.0 + noise(997.0)));
        m.insert("curve".into(),      base * (1.0 + noise(991.0)));
        m.insert("balancer".into(),   base * (1.0 + noise(983.0)));
        // Camelot only simulated if configured
        if self.camelot_addr.is_some() {
            m.insert("camelot".into(), base * (1.0 + noise(977.0)));
        }
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


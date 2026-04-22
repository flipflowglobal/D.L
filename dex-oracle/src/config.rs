//! config.rs — Oracle configuration loaded from environment variables.

use std::env;

/// Full runtime configuration for the DEX oracle.
#[derive(Debug, Clone)]
pub struct OracleConfig {
    /// Ethereum RPC HTTP endpoint (e.g. Alchemy / Infura)
    pub rpc_url: Option<String>,

    /// Local bind address for the HTTP API
    pub bind_addr: String,

    /// Price cache TTL in seconds (default 2.0)
    pub cache_ttl_secs: f64,

    /// Minimum spread % to flag an arbitrage opportunity (default 0.3 %)
    pub spread_threshold: f64,

    /// Fallback ETH/USD price when all fetches fail
    pub fallback_price: f64,

    /// Request timeout for RPC calls in milliseconds (default 8000)
    pub rpc_timeout_ms: u64,

    /// Whether to enable simulation mode when no RPC is available
    pub simulation_mode: bool,

    // ── Extended DEX configuration ────────────────────────────────────────────

    /// Curve TriCrypto pool address.
    /// Defaults to mainnet TriCrypto2 (USDT/WBTC/WETH):
    ///   0xD51a44d3FaE010294C616388b506AcdA1bfAAE46
    /// Set CURVE_POOL_ADDR to override (e.g. for a different chain or pool).
    /// Set to empty string to disable Curve pricing.
    pub curve_pool_addr: Option<String>,

    /// Balancer V2 Vault address.
    /// Defaults to mainnet: 0xBA12222222228d8Ba445958a75a0704d566BF2C8
    pub balancer_vault_addr: Option<String>,

    /// Balancer WETH/USDC pool ID (bytes32 hex, 0x-prefixed, 66 chars).
    /// Defaults to mainnet WETH/USDC 50/50 pool.
    /// Set BALANCER_POOL_ID to use a different pool (e.g. on a different chain).
    pub balancer_pool_id: Option<String>,

    /// Camelot V2 router address.  Camelot is Arbitrum-native; leave unset on
    /// mainnet.  Example (Arbitrum One): 0xc873fEcbd354f5A56E00E710B90EF4201db2448d
    /// Set CAMELOT_ROUTER_ADDR to enable Camelot pricing.
    pub camelot_router_addr: Option<String>,

    /// WETH address on the Camelot chain.
    /// Arbitrum default: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
    pub camelot_weth_addr: Option<String>,

    /// USDC address on the Camelot chain.
    /// Arbitrum USDC.e default: 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8
    pub camelot_usdc_addr: Option<String>,
}

impl OracleConfig {
    pub fn from_env() -> Self {
        let rpc_url = env::var("RPC_URL")
            .or_else(|_| env::var("ETH_RPC"))
            .ok()
            .filter(|s| !s.is_empty());

        let simulation_mode = rpc_url.is_none();

        Self {
            rpc_url,
            bind_addr: env::var("DEX_ORACLE_ADDR")
                .unwrap_or_else(|_| "127.0.0.1:9001".to_string()),
            cache_ttl_secs: env::var("DEX_ORACLE_TTL")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(2.0),
            spread_threshold: env::var("ARB_SPREAD_THRESHOLD")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.003),
            fallback_price: env::var("FALLBACK_ETH_PRICE")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(2000.0),
            rpc_timeout_ms: env::var("RPC_TIMEOUT_MS")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(8000),
            simulation_mode,
            curve_pool_addr: env::var("CURVE_POOL_ADDR")
                .ok()
                .filter(|s| !s.is_empty()),
            balancer_vault_addr: env::var("BALANCER_VAULT_ADDR")
                .ok()
                .filter(|s| !s.is_empty()),
            balancer_pool_id: env::var("BALANCER_POOL_ID")
                .ok()
                .filter(|s| !s.is_empty()),
            camelot_router_addr: env::var("CAMELOT_ROUTER_ADDR")
                .ok()
                .filter(|s| !s.is_empty()),
            camelot_weth_addr: env::var("CAMELOT_WETH_ADDR")
                .ok()
                .filter(|s| !s.is_empty()),
            camelot_usdc_addr: env::var("CAMELOT_USDC_ADDR")
                .ok()
                .filter(|s| !s.is_empty()),
        }
    }
}

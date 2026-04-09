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
        }
    }
}

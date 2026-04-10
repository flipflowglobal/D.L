//! config.rs — TX engine configuration loaded from environment variables.

use std::env;

/// Runtime configuration for the TX engine.
#[derive(Debug, Clone)]
pub struct TxConfig {
    /// Ethereum RPC endpoint (required for live broadcast)
    pub rpc_url: Option<String>,

    /// Local bind address for the HTTP API
    pub bind_addr: String,

    /// Private key in hex (0x-prefixed or bare 64 hex chars)
    /// WARNING: In production, use a hardware wallet or KMS.
    pub private_key: Option<String>,

    /// Profit/recipient wallet (EIP-55 checksummed)
    pub profit_wallet: Option<String>,

    /// Maximum gas price willing to pay (gwei).  Rejects tx if base fee > this.
    pub max_gas_gwei: u64,

    /// Priority fee tip per gas (gwei)
    pub priority_fee_gwei: u64,

    /// RPC call timeout in milliseconds
    pub rpc_timeout_ms: u64,

    /// Dry-run mode — sign but never broadcast (default true for safety)
    pub dry_run: bool,

    /// Chain ID (1=mainnet, 11155111=Sepolia)
    pub chain_id: u64,
}

impl TxConfig {
    pub fn from_env() -> Self {
        let rpc_url = env::var("RPC_URL")
            .or_else(|_| env::var("ETH_RPC"))
            .ok()
            .filter(|s| !s.is_empty());

        let dry_run = env::var("DRY_RUN")
            .unwrap_or_else(|_| "true".into())
            .to_lowercase()
            != "false";

        Self {
            rpc_url,
            bind_addr: env::var("TX_ENGINE_ADDR")
                .unwrap_or_else(|_| "127.0.0.1:9002".into()),
            private_key: env::var("PRIVATE_KEY").ok().filter(|s| !s.is_empty()),
            profit_wallet: env::var("PROFIT_WALLET")
                .or_else(|_| env::var("WALLET_ADDRESS"))
                .ok()
                .filter(|s| !s.is_empty()),
            max_gas_gwei: env::var("MAX_GAS_GWEI")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(100),
            priority_fee_gwei: env::var("PRIORITY_FEE_GWEI")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(2),
            rpc_timeout_ms: env::var("RPC_TIMEOUT_MS")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(8000),
            dry_run,
            chain_id: env::var("CHAIN_ID")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(1), // mainnet
        }
    }
}

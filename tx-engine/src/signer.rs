//! signer.rs — EIP-1559 transaction building, signing, and broadcasting.
//!
//! Uses alloy-rs EthereumWallet (secp256k1) for key management.
//! All signing is done in-process — private key never leaves the process.
//!
//! EIP-1559 fee structure:
//!   maxFeePerGas         = baseFee * 2 + priorityFee  (2× headroom)
//!   maxPriorityFeePerGas = configured tip (default 2 gwei)

use std::sync::Arc;
use std::time::Duration;

use alloy::consensus::TxEnvelope;
use alloy::eips::eip2718::Encodable2718;
use alloy::network::{Ethereum, EthereumWallet, NetworkWallet, TransactionBuilder};
use alloy::primitives::{Address, Bytes, U256};
use alloy::providers::{Provider, RootProvider};
use alloy::rpc::types::{BlockNumberOrTag, TransactionRequest};
use alloy::signers::local::PrivateKeySigner;
use tracing::{info, warn};

use crate::config::TxConfig;
use crate::error::{TxError, TxResult};

// 1 gwei = 1e9 wei
const GWEI: u128 = 1_000_000_000;

/// Signed transaction receipt summary returned to callers.
#[derive(Debug, Clone, serde::Serialize)]
pub struct TxReceipt {
    pub tx_hash:   String,
    pub from:      String,
    pub to:        String,
    pub value_eth: f64,
    pub gas_used:  Option<u64>,
    pub status:    String,  // "success" | "reverted" | "pending" | "dry_run"
    pub dry_run:   bool,
}

/// Core transaction engine — wallet + provider, signing and broadcasting.
pub struct TxEngine {
    provider: Option<Arc<RootProvider<Ethereum>>>,
    wallet:   Option<EthereumWallet>,
    from:     Option<Address>,
    config:   Arc<TxConfig>,
}

impl TxEngine {
    pub async fn new(config: Arc<TxConfig>) -> Self {
        // Parse private key
        let (wallet, from): (Option<EthereumWallet>, Option<Address>) =
            if let Some(pk) = &config.private_key {
                let pk_hex = pk.strip_prefix("0x").unwrap_or(pk);
                match pk_hex.parse::<PrivateKeySigner>() {
                    Ok(signer) => {
                        let addr = signer.address();
                        info!("Wallet loaded: {:?}", addr);
                        (Some(EthereumWallet::from(signer)), Some(addr))
                    }
                    Err(e) => {
                        warn!("Failed to parse PRIVATE_KEY: {} — no wallet", e);
                        (None, None)
                    }
                }
            } else {
                warn!("PRIVATE_KEY not set — signing disabled");
                (None, None)
            };

        // Build bare HTTP provider (no fillers needed for read-only + raw broadcast)
        let provider: Option<Arc<RootProvider<Ethereum>>> =
            if let Some(rpc_url) = &config.rpc_url {
                match rpc_url.parse::<url::Url>() {
                    Ok(url) => {
                        let p = RootProvider::new_http(url);
                        match tokio::time::timeout(
                            Duration::from_millis(config.rpc_timeout_ms),
                            p.get_block_number(),
                        ).await {
                            Ok(Ok(bn)) => {
                                info!("TX engine connected to RPC, block #{}", bn);
                                Some(Arc::new(p))
                            }
                            Ok(Err(e)) => {
                                warn!("RPC connect failed: {} — dry-run only", e);
                                None
                            }
                            Err(_) => {
                                warn!("RPC connect timed out — dry-run only");
                                None
                            }
                        }
                    }
                    Err(e) => {
                        warn!("Invalid RPC URL: {} — dry-run only", e);
                        None
                    }
                }
            } else {
                None
            };

        Self { provider, wallet, from, config }
    }

    pub fn is_connected(&self) -> bool { self.provider.is_some() }
    pub fn has_wallet(&self)  -> bool  { self.wallet.is_some() }
    pub fn is_dry_run(&self)  -> bool  {
        self.config.dry_run || !self.is_connected() || !self.has_wallet()
    }

    pub fn wallet_address(&self) -> Option<String> {
        self.from.map(|a| format!("{:?}", a))
    }

    // ── Fee estimation ────────────────────────────────────────────────────────

    /// Fetch current base fee from the latest block and compute EIP-1559 fee caps.
    /// Returns `(max_fee_per_gas, max_priority_fee_per_gas)` in wei.
    async fn eip1559_fees(&self) -> TxResult<(u128, u128)> {
        let provider = self.provider.as_ref().ok_or(TxError::Rpc("No provider".into()))?;

        // alloy 1.x: get_block_by_number(BlockNumberOrTag) — single argument
        let block = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            provider.get_block_by_number(BlockNumberOrTag::Latest),
        )
        .await
        .map_err(|_| TxError::Rpc("Timeout fetching latest block".into()))?
        .map_err(|e| TxError::Rpc(e.to_string()))?
        .ok_or_else(|| TxError::Rpc("Latest block not found".into()))?;

        let base_fee = block
            .header
            .base_fee_per_gas
            .ok_or_else(|| TxError::Rpc("No EIP-1559 base fee in block header".into()))?
            as u128;

        let priority_fee = self.config.priority_fee_gwei as u128 * GWEI;
        let max_fee      = base_fee * 2 + priority_fee; // 2× headroom

        let hard_cap = self.config.max_gas_gwei as u128 * GWEI;
        if max_fee > hard_cap {
            return Err(TxError::Rpc(format!(
                "Computed maxFeePerGas {:.1} gwei exceeds hard cap {} gwei",
                max_fee as f64 / GWEI as f64,
                self.config.max_gas_gwei,
            )));
        }

        Ok((max_fee, priority_fee))
    }

    // ── Core send ─────────────────────────────────────────────────────────────

    /// Internal: sign an EIP-1559 transaction request and broadcast it.
    /// Returns `TxReceipt`. Caller must check `is_dry_run()` first.
    async fn sign_and_send(&self, tx: TransactionRequest) -> TxResult<TxReceipt> {
        let wallet   = self.wallet.as_ref().ok_or(TxError::Wallet("No wallet".into()))?;
        let provider = self.provider.as_ref().ok_or(TxError::Rpc("No provider".into()))?;
        let from     = self.from.ok_or(TxError::Wallet("No wallet address".into()))?;

        let to = tx.to.clone().map(|t| format!("{:?}", t)).unwrap_or_default();
        let value_eth = {
            let wei: u128 = tx.value.map(|v| u128::try_from(v).unwrap_or(0)).unwrap_or(0);
            wei as f64 / 1e18
        };

        // Sign the request → TxEnvelope (EIP-2718 typed transaction)
        // Fully qualified to resolve EthereumWallet: NetworkWallet<Ethereum>
        let signed: TxEnvelope =
            NetworkWallet::<Ethereum>::sign_request(wallet, tx)
                .await
                .map_err(|e| TxError::Sign(e.to_string()))?;

        // EIP-2718 encode
        let encoded = signed.encoded_2718();

        // Broadcast
        info!("Broadcasting {:.6} ETH → {} ({}B encoded)", value_eth, to, encoded.len());

        let pending = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            provider.send_raw_transaction(&encoded),
        )
        .await
        .map_err(|_| TxError::Rpc("Broadcast timed out".into()))?
        .map_err(|e| TxError::Rpc(e.to_string()))?;

        let tx_hash = format!("{:?}", pending.tx_hash());
        info!("TX submitted: {}", tx_hash);

        // Wait up to 60s for receipt
        match tokio::time::timeout(Duration::from_secs(60), pending.get_receipt()).await {
            Ok(Ok(receipt)) => {
                let gas_used = receipt.gas_used;
                let status   = if receipt.status() { "success" } else { "reverted" };
                info!("TX {} — {} (gas={})", tx_hash, status, gas_used);
                Ok(TxReceipt {
                    tx_hash,
                    from:      format!("{:?}", from),
                    to,
                    value_eth,
                    gas_used:  Some(gas_used),
                    status:    status.into(),
                    dry_run:   false,
                })
            }
            Ok(Err(e)) => Err(TxError::Rpc(format!("Receipt error: {}", e))),
            Err(_) => {
                warn!("TX {} still pending after 60s — returning hash only", tx_hash);
                Ok(TxReceipt {
                    tx_hash,
                    from:      format!("{:?}", from),
                    to,
                    value_eth,
                    gas_used:  None,
                    status:    "pending".into(),
                    dry_run:   false,
                })
            }
        }
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /// Send ETH from the loaded wallet to `to_addr`.
    pub async fn send_eth(
        &self,
        to_addr:   Address,
        value_wei: U256,
        data:      Option<Bytes>,
    ) -> TxResult<TxReceipt> {
        let from = self.from.ok_or(TxError::Wallet("No private key loaded".into()))?;

        let value_eth = {
            let wei: u128 = u128::try_from(value_wei).unwrap_or(u128::MAX);
            wei as f64 / 1e18
        };

        if self.is_dry_run() {
            info!("[DRY RUN] Would send {:.6} ETH → {:?}", value_eth, to_addr);
            return Ok(TxReceipt {
                tx_hash:   "0x0000000000000000000000000000000000000000000000000000000000000000".into(),
                from:      format!("{:?}", from),
                to:        format!("{:?}", to_addr),
                value_eth,
                gas_used:  None,
                status:    "dry_run".into(),
                dry_run:   true,
            });
        }

        let provider = self.provider.as_ref().ok_or(TxError::Rpc("No provider".into()))?;
        let (max_fee, priority_fee) = self.eip1559_fees().await?;

        // Get nonce
        let nonce = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            provider.get_transaction_count(from),
        )
        .await
        .map_err(|_| TxError::Rpc("Timeout getting nonce".into()))?
        .map_err(|e| TxError::Rpc(e.to_string()))?;

        // Build request for gas estimation
        let mut tx = TransactionRequest::default()
            .with_from(from)
            .with_to(to_addr)
            .with_value(value_wei)
            .with_nonce(nonce)
            .with_chain_id(self.config.chain_id)
            .with_max_fee_per_gas(max_fee)
            .with_max_priority_fee_per_gas(priority_fee);

        if let Some(d) = &data {
            tx = tx.with_input(d.clone());
        }

        // Estimate gas (takes by value — clone first for estimation)
        let gas_est = tokio::time::timeout(
            Duration::from_millis(self.config.rpc_timeout_ms),
            provider.estimate_gas(tx.clone()),
        )
        .await
        .map_err(|_| TxError::Rpc("Gas estimation timed out".into()))?
        .map_err(|e| TxError::Rpc(format!("Gas estimate failed: {}", e)))?;

        tx = tx.with_gas_limit(gas_est * 12 / 10); // 20% headroom

        self.sign_and_send(tx).await
    }

    /// Send a contract call with arbitrary ABI-encoded calldata.
    pub async fn send_contract_call(
        &self,
        contract:  Address,
        calldata:  Bytes,
        value_wei: U256,
    ) -> TxResult<TxReceipt> {
        self.send_eth(contract, value_wei, Some(calldata)).await
    }

    // ── Gas price query ───────────────────────────────────────────────────────

    /// Return current EIP-1559 fee estimates.
    pub async fn gas_estimate(&self) -> TxResult<serde_json::Value> {
        if !self.is_connected() {
            return Ok(serde_json::json!({
                "error": "not connected",
                "max_gas_gwei_cap": self.config.max_gas_gwei,
            }));
        }
        let (max_fee, priority) = self.eip1559_fees().await?;
        Ok(serde_json::json!({
            "max_fee_per_gas_gwei":      max_fee  as f64 / GWEI as f64,
            "priority_fee_per_gas_gwei": priority as f64 / GWEI as f64,
            "configured_cap_gwei":       self.config.max_gas_gwei,
        }))
    }

    // ── Flash loan initiation ─────────────────────────────────────────────────

    /// Broadcast a `NexusFlashReceiver.initiate(...)` transaction.
    ///
    /// `calldata` is the fully ABI-encoded selector + arguments produced by
    /// `flash::encode_initiate_calldata`.  The flash loan itself carries no
    /// ETH value; gas estimation is performed automatically.
    pub async fn initiate_flash_loan(
        &self,
        contract:  alloy::primitives::Address,
        calldata:  alloy::primitives::Bytes,
    ) -> TxResult<crate::signer::TxReceipt> {
        self.send_eth(contract, alloy::primitives::U256::ZERO, Some(calldata)).await
    }
}

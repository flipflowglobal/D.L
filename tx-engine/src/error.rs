//! error.rs — Unified error types for the TX engine.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum TxError {
    #[error("RPC error: {0}")]
    Rpc(String),

    #[error("Signing error: {0}")]
    Sign(String),

    #[error("Wallet error: {0}")]
    Wallet(String),

    #[error("Encode error: {0}")]
    Encode(String),

    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Pending tx not found: {0}")]
    NotFound(String),
}

pub type TxResult<T> = Result<T, TxError>;

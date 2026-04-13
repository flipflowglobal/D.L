//! error.rs — Unified error types for the DEX oracle.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum OracleError {
    #[error("RPC call failed: {0}")]
    Rpc(String),

    #[error("ABI encoding error: {0}")]
    Abi(String),

    #[error("Price decode failed: {0}")]
    Decode(String),

    #[error("Network error: {0}")]
    Network(#[from] reqwest::Error),

    #[error("No price data available")]
    NoPriceData,

    #[error("Configuration error: {0}")]
    Config(String),
}

pub type OracleResult<T> = Result<T, OracleError>;

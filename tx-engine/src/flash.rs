//! flash.rs — ABI encoding for NexusFlashReceiver.initiate().
//!
//! Provides helpers to encode the `initiate(asset, amount, steps, minProfit)`
//! calldata so that tx-engine can broadcast flash-loan arbitrage transactions
//! without requiring the Python caller to handle ABI encoding.
//!
//! SwapStep struct mirrors the Solidity definition in NexusFlashReceiver.sol:
//!
//! ```solidity
//! struct SwapStep {
//!     address tokenIn;
//!     address tokenOut;
//!     address router;
//!     uint256 amountOutMin;
//!     uint256 deadline;
//!     uint8   dex;            // DEX_UNI_V3=0, DEX_SUSHI_V2=1, DEX_CURVE=2, ...
//!     bytes32 balancerPoolId; // Balancer pool id (ignored for other DEXes)
//!     int128  curveI;         // Curve token-in index  (ignored for other DEXes)
//!     int128  curveJ;         // Curve token-out index (ignored for other DEXes)
//! }
//! ```
//!
//! Python callers may either:
//!   (a) Pass raw pre-encoded `steps_hex` (Python web3.py ABI-encodes SwapStep[])
//!   (b) Pass structured `steps` JSON array and let this module encode them

use alloy::primitives::{Address, Bytes, FixedBytes, U256};
use alloy::sol;
use alloy::sol_types::SolCall;
use serde::Deserialize;
use std::str::FromStr;

use crate::error::{TxError, TxResult};

// ── ABI definitions ───────────────────────────────────────────────────────────

sol! {
    /// SwapStep struct — must exactly match NexusFlashReceiver.sol
    struct SwapStep {
        address tokenIn;
        address tokenOut;
        address router;
        uint256 amountOutMin;
        uint256 deadline;
        uint8   dex;
        bytes32 balancerPoolId;
        int128  curveI;
        int128  curveJ;
    }

    /// NexusFlashReceiver — initiate function selector + calldata encoding
    interface INexusFlashReceiver {
        function initiate(
            address asset,
            uint256 amount,
            bytes calldata steps,
            uint256 minProfit
        ) external;
    }
}

// ── DEX constants (mirrors Solidity DEX_* constants) ─────────────────────────
// Referenced by callers building SwapStep JSON — kept for documentation clarity.

#[allow(dead_code)]
pub const DEX_UNI_V3:   u8 = 0;
#[allow(dead_code)]
pub const DEX_SUSHI_V2: u8 = 1;
#[allow(dead_code)]
pub const DEX_CURVE:    u8 = 2;
#[allow(dead_code)]
pub const DEX_BALANCER: u8 = 3;
#[allow(dead_code)]
pub const DEX_CAMELOT:  u8 = 4;

// ── JSON request types ────────────────────────────────────────────────────────

/// JSON representation of a single SwapStep (from the HTTP request body).
#[derive(Debug, Deserialize)]
pub struct SwapStepJson {
    pub token_in:         String,
    pub token_out:        String,
    pub router:           String,
    pub amount_out_min:   String,   // decimal string to avoid JS precision loss
    pub deadline:         u64,
    pub dex:              u8,
    /// Balancer pool ID as a 0x-prefixed 32-byte hex (ignored for non-Balancer)
    pub balancer_pool_id: Option<String>,
    /// Curve token-in index (ignored for non-Curve)
    pub curve_i:          Option<i64>,
    /// Curve token-out index (ignored for non-Curve)
    pub curve_j:          Option<i64>,
}

/// Full request body for POST /flash/initiate.
#[derive(Debug, Deserialize)]
pub struct FlashInitRequest {
    /// NexusFlashReceiver contract address
    pub contract: String,
    /// Flash-loan asset (e.g. WETH or USDC address)
    pub asset: String,
    /// Flash-loan amount in wei (decimal string)
    pub amount_wei: String,
    /// Minimum profit required in wei (decimal string)
    pub min_profit_wei: String,
    /// Swap route — either structured steps OR raw pre-encoded hex bytes
    #[serde(flatten)]
    pub steps_input: StepsInput,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum StepsInput {
    /// Structured steps — this module encodes them into ABI bytes
    Structured { steps: Vec<SwapStepJson> },
    /// Raw ABI-encoded bytes (0x-prefixed hex) — Python encodes externally
    Raw { steps_hex: String },
}

// ── Encoding helpers ──────────────────────────────────────────────────────────

/// Convert `SwapStepJson` → alloy `SwapStep` (with validation).
fn parse_step(s: &SwapStepJson) -> TxResult<SwapStep> {
    let token_in: Address = s.token_in.parse()
        .map_err(|_| TxError::Encode(format!("invalid token_in: {}", s.token_in)))?;
    let token_out: Address = s.token_out.parse()
        .map_err(|_| TxError::Encode(format!("invalid token_out: {}", s.token_out)))?;
    let router: Address = s.router.parse()
        .map_err(|_| TxError::Encode(format!("invalid router: {}", s.router)))?;

    let amount_out_min: U256 = U256::from_str(&s.amount_out_min)
        .map_err(|_| TxError::Encode(format!("invalid amount_out_min: {}", s.amount_out_min)))?;

    let balancer_pool_id: FixedBytes<32> = s.balancer_pool_id
        .as_deref()
        .unwrap_or("0x0000000000000000000000000000000000000000000000000000000000000000")
        .parse()
        .map_err(|_| TxError::Encode("invalid balancer_pool_id (must be 32-byte hex)".into()))?;

    // sol! maps Solidity int128 → Rust i128.  Curve indices fit easily in i64.
    let curve_i: i128 = s.curve_i.unwrap_or(0) as i128;
    let curve_j: i128 = s.curve_j.unwrap_or(0) as i128;

    Ok(SwapStep {
        tokenIn:        token_in,
        tokenOut:       token_out,
        router,
        amountOutMin:   amount_out_min,
        deadline:       U256::from(s.deadline),
        dex:            s.dex,
        balancerPoolId: balancer_pool_id,
        curveI:         curve_i,
        curveJ:         curve_j,
    })
}

/// Encode structured SwapStep[] into ABI bytes (equivalent to `abi.encode(steps)`).
fn encode_steps(steps: &[SwapStepJson]) -> TxResult<Vec<u8>> {
    use alloy::sol_types::SolValue;
    let parsed: Vec<SwapStep> = steps.iter().map(parse_step).collect::<TxResult<_>>()?;
    Ok(parsed.abi_encode())
}

/// Decode pre-encoded steps_hex string to raw bytes.
fn decode_steps_hex(hex: &str) -> TxResult<Vec<u8>> {
    let stripped = hex.strip_prefix("0x").unwrap_or(hex);
    hex::decode(stripped).map_err(|_| TxError::Encode("invalid hex in steps_hex".into()))
}

/// Encode the complete `initiate(asset, amount, steps, minProfit)` calldata.
///
/// Returns the 4-byte selector + ABI-encoded arguments as `Vec<u8>`.
pub fn encode_initiate_calldata(req: &FlashInitRequest) -> TxResult<(Address, Vec<u8>)> {
    let contract: Address = req.contract.parse()
        .map_err(|_| TxError::Encode(format!("invalid contract address: {}", req.contract)))?;

    let asset: Address = req.asset.parse()
        .map_err(|_| TxError::Encode(format!("invalid asset address: {}", req.asset)))?;

    let amount: U256 = U256::from_str(&req.amount_wei)
        .map_err(|_| TxError::Encode(format!("invalid amount_wei: {}", req.amount_wei)))?;

    let min_profit: U256 = U256::from_str(&req.min_profit_wei)
        .map_err(|_| TxError::Encode(format!("invalid min_profit_wei: {}", req.min_profit_wei)))?;

    let steps_bytes: Vec<u8> = match &req.steps_input {
        StepsInput::Structured { steps } => encode_steps(steps)?,
        StepsInput::Raw { steps_hex }    => decode_steps_hex(steps_hex)?,
    };

    let calldata = INexusFlashReceiver::initiateCall {
        asset,
        amount,
        steps: Bytes::from(steps_bytes),
        minProfit: min_profit,
    }
    .abi_encode();

    Ok((contract, calldata))
}

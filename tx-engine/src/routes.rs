//! routes.rs — Axum HTTP route handlers for the TX engine.
//!
//! All state-mutating operations require valid JSON bodies.
//! Private key is NEVER exposed through any endpoint.

use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
};
use serde::Deserialize;
use serde_json::{json, Value};

use alloy::primitives::{Address, U256, Bytes};
use std::str::FromStr;

use crate::AppState;
use crate::flash::{FlashInitRequest, encode_initiate_calldata};

// ── POST /tx/send ─────────────────────────────────────────────────────────────

/// Send ETH to an address.
///
/// Request body:
/// ```json
/// { "to": "0x...", "value_eth": 0.01, "data": "0x..." }
/// ```
#[derive(Deserialize)]
pub struct SendEthRequest {
    pub to:        String,
    pub value_eth: f64,
    pub data:      Option<String>,  // optional hex-encoded calldata
}

pub async fn send_eth(
    State(s): State<AppState>,
    Json(req): Json<SendEthRequest>,
) -> (StatusCode, Json<Value>) {
    let to_addr = match Address::from_str(&req.to) {
        Ok(a) => a,
        Err(_) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("invalid 'to' address: {}", req.to)})),
        ),
    };

    if req.value_eth < 0.0 {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "value_eth must be >= 0"})));
    }

    // Convert ETH to wei (U256)
    let wei_f = req.value_eth * 1e18;
    let value_wei = U256::from(wei_f as u128);

    // Parse optional calldata
    let data: Option<Bytes> = if let Some(hex_str) = &req.data {
        let stripped = hex_str.strip_prefix("0x").unwrap_or(hex_str);
        match hex::decode(stripped) {
            Ok(b) => Some(Bytes::from(b)),
            Err(_) => return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "invalid hex in 'data'"})),
            ),
        }
    } else {
        None
    };

    match s.engine.send_eth(to_addr, value_wei, data).await {
        Ok(receipt) => (StatusCode::OK, Json(serde_json::to_value(receipt).unwrap())),
        Err(e)      => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))),
    }
}

// ── POST /tx/contract ─────────────────────────────────────────────────────────

/// Call a contract with arbitrary ABI-encoded calldata.
///
/// Request body:
/// ```json
/// { "contract": "0x...", "calldata": "0xABCD...", "value_eth": 0.0 }
/// ```
#[derive(Deserialize)]
pub struct ContractCallRequest {
    pub contract:  String,
    pub calldata:  String,
    pub value_eth: Option<f64>,
}

pub async fn contract_call(
    State(s): State<AppState>,
    Json(req): Json<ContractCallRequest>,
) -> (StatusCode, Json<Value>) {
    let contract = match Address::from_str(&req.contract) {
        Ok(a) => a,
        Err(_) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("invalid 'contract' address: {}", req.contract)})),
        ),
    };

    let stripped  = req.calldata.strip_prefix("0x").unwrap_or(&req.calldata);
    let calldata  = match hex::decode(stripped) {
        Ok(b)  => Bytes::from(b),
        Err(_) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid hex in 'calldata'"})),
        ),
    };

    let value_eth = req.value_eth.unwrap_or(0.0);
    let value_wei = U256::from((value_eth * 1e18) as u128);

    match s.engine.send_contract_call(contract, calldata, value_wei).await {
        Ok(receipt) => (StatusCode::OK, Json(serde_json::to_value(receipt).unwrap())),
        Err(e)      => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))),
    }
}

// ── GET /gas ──────────────────────────────────────────────────────────────────

pub async fn gas_price(State(s): State<AppState>) -> (StatusCode, Json<Value>) {
    match s.engine.gas_estimate().await {
        Ok(v)  => (StatusCode::OK, Json(v)),
        Err(e) => (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error": e.to_string()}))),
    }
}

// ── GET /health ───────────────────────────────────────────────────────────────

pub async fn health(State(s): State<AppState>) -> Json<Value> {
    let uptime = s.started.elapsed().as_secs();
    Json(json!({
        "status":       "ok",
        "uptime_secs":  uptime,
        "rpc_connected": s.engine.is_connected(),
        "has_wallet":   s.engine.has_wallet(),
        "dry_run":      s.engine.is_dry_run(),
        "wallet":       s.engine.wallet_address(),
        "chain_id":     s.config.chain_id,
        "mode":         if s.engine.is_dry_run() { "dry_run" } else { "live" },
    }))
}

// ── GET /config ───────────────────────────────────────────────────────────────

pub async fn get_config(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "bind_addr":          s.config.bind_addr,
        "chain_id":           s.config.chain_id,
        "max_gas_gwei":       s.config.max_gas_gwei,
        "priority_fee_gwei":  s.config.priority_fee_gwei,
        "rpc_timeout_ms":     s.config.rpc_timeout_ms,
        "dry_run":            s.config.dry_run,
        "rpc_configured":     s.config.rpc_url.is_some(),
        "wallet_configured":  s.config.private_key.is_some(),
        "profit_wallet":      s.config.profit_wallet,
    }))
}

// ── POST /flash/initiate ──────────────────────────────────────────────────────

/// Initiate a flash-loan arbitrage via NexusFlashReceiver.initiate().
///
/// Request body (structured steps):
/// ```json
/// {
///   "contract":       "0x<NexusFlashReceiver>",
///   "asset":          "0x<token_to_borrow>",
///   "amount_wei":     "1000000000000000000",
///   "min_profit_wei": "1000000",
///   "steps": [
///     {
///       "token_in":       "0xC02...",
///       "token_out":      "0xA0b...",
///       "router":         "0xb27...",
///       "amount_out_min": "2400000000",
///       "deadline":       1234567890,
///       "dex":            0
///     }
///   ]
/// }
/// ```
///
/// Alternatively, pass `"steps_hex": "0x..."` with pre-ABI-encoded bytes.
pub async fn flash_initiate(
    State(s): State<AppState>,
    Json(req): Json<FlashInitRequest>,
) -> (StatusCode, Json<Value>) {
    // Encode calldata from the request
    let (contract, calldata) = match encode_initiate_calldata(&req) {
        Ok(v)  => v,
        Err(e) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": e.to_string()})),
        ),
    };

    // Delegate to the signer (handles dry-run, fee estimation, broadcast)
    match s.engine.initiate_flash_loan(contract, Bytes::from(calldata)).await {
        Ok(receipt) => (StatusCode::OK, Json(serde_json::to_value(receipt).unwrap())),
        Err(e)      => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))),
    }
}

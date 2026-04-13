//! routes.rs — Axum HTTP route handlers for the DEX oracle.

use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
};
use serde_json::{json, Value};

use crate::AppState;

// ── GET /prices ───────────────────────────────────────────────────────────────

/// Return the current best ETH/USD prices from all DEX sources.
///
/// Response example:
/// ```json
/// {
///   "uniswap_v3":  2451.83,
///   "sushiswap":   2449.12,
///   "arbitrage": {
///     "buy_on":         "sushiswap",
///     "buy_price":      2449.12,
///     "sell_on":        "uniswap_v3",
///     "sell_price":     2451.83,
///     "spread_pct":     0.1107,
///     "est_profit_pct": -0.4893
///   },
///   "source": "live",
///   "cache_hit": true
/// }
/// ```
pub async fn get_prices(State(s): State<AppState>) -> (StatusCode, Json<Value>) {
    // Try cache first (< 1 µs)
    let cached = s.cache.get_all_fresh().await;
    let (prices, cache_hit) = if !cached.is_empty() {
        (cached, true)
    } else {
        // Cache miss — fetch from chain (300–600 ms)
        match s.oracle.fetch_all_prices().await {
            Ok(p) => {
                s.cache.update(p.clone());
                (p, false)
            }
            Err(e) => {
                return (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(json!({"error": e.to_string()})),
                );
            }
        }
    };

    let arb = s.oracle.evaluate_arbitrage(&prices);
    let source = if s.oracle.is_connected() { "live" } else { "simulation" };

    let mut resp = json!({
        "source":    source,
        "cache_hit": cache_hit,
    });

    // Merge prices into response
    if let Value::Object(ref mut map) = resp {
        for (dex, price) in &prices {
            map.insert(dex.clone(), json!(price));
        }
        if let Some(arb_data) = arb {
            map.insert("arbitrage".into(), arb_data);
        }
    }

    (StatusCode::OK, Json(resp))
}

// ── GET /prices/uniswap ───────────────────────────────────────────────────────

/// Return individual Uniswap V3 fee-tier prices.
///
/// Response: {"uniswap_v3_500": 2451.0, "uniswap_v3_3000": 2450.9, ...}
pub async fn get_uniswap_prices(State(s): State<AppState>) -> (StatusCode, Json<Value>) {
    match s.oracle.all_uniswap_prices().await {
        m if !m.is_empty() => {
            let best = m.values().cloned().fold(f64::NEG_INFINITY, f64::max);
            (StatusCode::OK, Json(json!({
                "fee_tiers": m,
                "best":      best,
                "source":    if s.oracle.is_connected() { "live" } else { "simulation" },
            })))
        }
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": "No Uniswap V3 price available"})),
        ),
    }
}

// ── GET /prices/sushiswap ─────────────────────────────────────────────────────

pub async fn get_sushiswap_price(State(s): State<AppState>) -> (StatusCode, Json<Value>) {
    match s.oracle.sushiswap_price().await {
        Ok(price) => (StatusCode::OK, Json(json!({
            "sushiswap": price,
            "source":    if s.oracle.is_connected() { "live" } else { "simulation" },
        }))),
        Err(e) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": e.to_string()})),
        ),
    }
}

// ── GET /health ───────────────────────────────────────────────────────────────

pub async fn health(State(s): State<AppState>) -> Json<Value> {
    let uptime = s.started.elapsed().as_secs();
    let cached = s.cache.get_all_fresh().await;

    Json(json!({
        "status":      "ok",
        "uptime_secs": uptime,
        "rpc_connected": s.oracle.is_connected(),
        "cached_dexes":  cached.keys().collect::<Vec<_>>(),
        "mode":        if s.oracle.is_connected() { "live" } else { "simulation" },
    }))
}

// ── GET /cache/stats ──────────────────────────────────────────────────────────

pub async fn cache_stats(State(s): State<AppState>) -> Json<Value> {
    Json(s.cache.stats())
}

// ── POST /cache/invalidate ────────────────────────────────────────────────────

pub async fn invalidate_cache(State(s): State<AppState>) -> Json<Value> {
    s.cache.invalidate().await;
    Json(json!({"invalidated": true}))
}

// ── GET /config ───────────────────────────────────────────────────────────────

pub async fn get_config(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "bind_addr":       s.config.bind_addr,
        "cache_ttl_secs":  s.config.cache_ttl_secs,
        "spread_threshold": s.config.spread_threshold,
        "fallback_price":  s.config.fallback_price,
        "rpc_timeout_ms":  s.config.rpc_timeout_ms,
        "simulation_mode": s.config.simulation_mode,
        "rpc_configured":  s.config.rpc_url.is_some(),
    }))
}

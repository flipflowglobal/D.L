//! main.rs — AUREON TX Engine
//!
//! A minimal Axum HTTP sidecar for EIP-1559 transaction signing and broadcasting.
//! The Python trading bot calls this service instead of doing signing itself,
//! keeping the hot-path Python code free of cryptography overhead.
//!
//! Endpoints:
//!   POST /tx/send       — Send ETH (or ETH + calldata)
//!   POST /tx/contract   — ABI-encoded contract call
//!   GET  /gas           — Current EIP-1559 fee estimate
//!   GET  /health        — Health + wallet status
//!   GET  /config        — Runtime configuration (no secrets)

mod config;
mod error;
mod flash;
mod routes;
mod signer;

use std::sync::Arc;
use std::time::Instant;

use axum::{Router, routing::get, routing::post};
use tokio::net::TcpListener;
use axum::http::HeaderValue;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::EnvFilter;

use crate::config::TxConfig;
use crate::signer::TxEngine;

// ── Shared application state ──────────────────────────────────────────────────

#[derive(Clone)]
pub struct AppState {
    pub engine:  Arc<TxEngine>,
    pub config:  Arc<TxConfig>,
    pub started: Arc<Instant>,
}

// ── Main ──────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Logging
    tracing_subscriber::fmt::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("tx_engine=info,warn")),
        )
        .with_target(false)
        .compact()
        .init();

    // Load .env if present
    let _ = dotenv::dotenv();

    // Configuration
    let config = Arc::new(TxConfig::from_env());

    info!("TX Engine starting on {}", config.bind_addr);
    info!("Chain ID: {} | DRY_RUN: {}", config.chain_id, config.dry_run);
    info!("RPC: {}", config.rpc_url.as_deref().unwrap_or("NOT SET"));
    info!("Wallet: {}", if config.private_key.is_some() { "configured" } else { "NOT SET" });
    info!("Profit wallet: {}", config.profit_wallet.as_deref().unwrap_or("NOT SET"));

    // Build engine
    let engine = Arc::new(TxEngine::new(config.clone()).await);

    let state = AppState {
        engine,
        config: config.clone(),
        started: Arc::new(Instant::now()),
    };

    // CORS — restrict to localhost only (tx-engine is a local sidecar,
    // it should never accept requests from external origins)
    let cors = CorsLayer::new()
        .allow_origin([
            "http://127.0.0.1".parse::<HeaderValue>().unwrap(),
            "http://localhost".parse::<HeaderValue>().unwrap(),
            "http://127.0.0.1:8010".parse::<HeaderValue>().unwrap(),
            "http://localhost:8010".parse::<HeaderValue>().unwrap(),
        ])
        .allow_methods(Any)
        .allow_headers(Any);

    // Router
    let app = Router::new()
        .route("/tx/send",         post(routes::send_eth))
        .route("/tx/contract",     post(routes::contract_call))
        .route("/flash/initiate",  post(routes::flash_initiate))
        .route("/gas",             get(routes::gas_price))
        .route("/health",          get(routes::health))
        .route("/config",          get(routes::get_config))
        .layer(TraceLayer::new_for_http())
        .layer(cors)
        .with_state(state);

    let listener = TcpListener::bind(&config.bind_addr).await?;
    info!("Listening on http://{}", config.bind_addr);

    axum::serve(listener, app).await?;
    Ok(())
}

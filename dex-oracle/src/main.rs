/*!
dex-oracle — AUREON DEX Price Oracle Sidecar
=============================================

A high-performance Rust binary that:
  1. Queries Uniswap V3 (all 3 fee tiers) AND SushiSwap SIMULTANEOUSLY
     using tokio::join! — 4 concurrent eth_call RPC requests.
  2. Caches results for a configurable TTL (default 2 s).
  3. Exposes a lightweight Axum HTTP API on localhost:9001.
  4. Python callers hit http://localhost:9001/prices for <1 ms local latency
     instead of making their own 400–1500 ms RPC calls.

Latency profile:
  Sequential Python (before): ~1200–1800 ms
  This sidecar (after)       :  ~300– 600 ms  (wall-clock: slowest RPC call)
  Python reading local cache :   ~0.1– 1.0 ms

API:
  GET /prices          — current DEX prices (JSON)
  GET /health          — {"status":"ok","uptime_secs":N}
  GET /cache/stats     — cache hit/miss counters
  POST /cache/invalidate — force next fetch to refresh
  GET /config          — active configuration
*/

mod config;
mod dex;
mod cache;
mod routes;
mod error;

use std::sync::Arc;
use std::time::Instant;

use axum::{Router, routing::get, routing::post};
use tokio::net::TcpListener;
use tower_http::cors::{CorsLayer, Any};
use tower_http::trace::TraceLayer;
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;

use crate::cache::PriceCache;
use crate::config::OracleConfig;
use crate::dex::DexOracle;

/// Shared application state — cheap to clone (all behind Arc).
#[derive(Clone)]
pub struct AppState {
    pub oracle:  Arc<DexOracle>,
    pub cache:   Arc<PriceCache>,
    pub config:  Arc<OracleConfig>,
    pub started: Arc<Instant>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // ── Logging ───────────────────────────────────────────────────────────────
    tracing_subscriber::fmt::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("dex_oracle=info,warn")),
        )
        .with_target(false)
        .compact()
        .init();

    // ── Load .env ─────────────────────────────────────────────────────────────
    let _ = dotenv::dotenv();

    // ── Configuration ─────────────────────────────────────────────────────────
    let cfg = Arc::new(OracleConfig::from_env());
    info!("DEX Oracle starting on {}", cfg.bind_addr);
    info!("RPC URL: {}", cfg.rpc_url.as_deref().unwrap_or("NOT SET — simulation mode"));
    info!("Cache TTL: {}s", cfg.cache_ttl_secs);

    // ── DEX Oracle engine ─────────────────────────────────────────────────────
    let oracle = Arc::new(DexOracle::new(cfg.clone()).await);

    // ── Price cache ───────────────────────────────────────────────────────────
    let cache = Arc::new(PriceCache::new(cfg.cache_ttl_secs));

    // ── Shared state ──────────────────────────────────────────────────────────
    let state = AppState {
        oracle:  oracle.clone(),
        cache:   cache.clone(),
        config:  cfg.clone(),
        started: Arc::new(Instant::now()),
    };

    // ── Background cache warmer ───────────────────────────────────────────────
    // Pre-fetch prices every (ttl/2) seconds so callers always get a fresh
    // cached value without waiting for an RPC call themselves.
    {
        let state_bg = state.clone();
        tokio::spawn(async move {
            let interval = std::time::Duration::from_secs_f64(
                state_bg.config.cache_ttl_secs / 2.0
            );
            loop {
                match state_bg.oracle.fetch_all_prices().await {
                    Ok(prices) => {
                        state_bg.cache.update(prices);
                        tracing::debug!("Cache warmed");
                    }
                    Err(e) => {
                        warn!("Cache warm failed: {}", e);
                    }
                }
                tokio::time::sleep(interval).await;
            }
        });
    }

    // ── Axum router ───────────────────────────────────────────────────────────
    let app = Router::new()
        .route("/prices",           get(routes::get_prices))
        .route("/prices/uniswap",   get(routes::get_uniswap_prices))
        .route("/prices/sushiswap", get(routes::get_sushiswap_price))
        .route("/health",           get(routes::health))
        .route("/cache/stats",      get(routes::cache_stats))
        .route("/cache/invalidate", post(routes::invalidate_cache))
        .route("/config",           get(routes::get_config))
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    // ── Bind and serve ────────────────────────────────────────────────────────
    let listener = TcpListener::bind(&cfg.bind_addr).await?;
    info!("Listening on http://{}", cfg.bind_addr);
    axum::serve(listener, app).await?;

    Ok(())
}

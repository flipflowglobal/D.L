//! cache.rs — Thread-safe TTL price cache backed by tokio RwLock.
//!
//! The cache stores the last known prices for each DEX and
//! expires them after `ttl_secs` seconds.  Background warmer
//! in main.rs refreshes it before expiry so callers almost
//! always hit a warm cache (< 1 µs response vs ~500 ms RPC call).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use tokio::sync::RwLock;

/// A single price entry with its fetch timestamp.
#[derive(Debug, Clone)]
struct Entry {
    price:      f64,
    fetched_at: Instant,
}

/// Telemetry counters (lock-free atomic).
struct Counters {
    hits:   AtomicU64,
    misses: AtomicU64,
    writes: AtomicU64,
}

/// Thread-safe TTL price cache.
pub struct PriceCache {
    store:   RwLock<HashMap<String, Entry>>,
    ttl:     Duration,
    counters: Counters,
}

impl PriceCache {
    pub fn new(ttl_secs: f64) -> Self {
        Self {
            store:   RwLock::new(HashMap::new()),
            ttl:     Duration::from_secs_f64(ttl_secs),
            counters: Counters {
                hits:   AtomicU64::new(0),
                misses: AtomicU64::new(0),
                writes: AtomicU64::new(0),
            },
        }
    }

    /// Get a cached price by DEX name.  Returns None if absent or expired.
    pub async fn get(&self, dex: &str) -> Option<f64> {
        let store = self.store.read().await;
        if let Some(entry) = store.get(dex) {
            if entry.fetched_at.elapsed() < self.ttl {
                self.counters.hits.fetch_add(1, Ordering::Relaxed);
                return Some(entry.price);
            }
        }
        self.counters.misses.fetch_add(1, Ordering::Relaxed);
        None
    }

    /// Get all cached prices that are still within TTL.
    pub async fn get_all_fresh(&self) -> HashMap<String, f64> {
        let store = self.store.read().await;
        store
            .iter()
            .filter(|(_, e)| e.fetched_at.elapsed() < self.ttl)
            .map(|(k, e)| (k.clone(), e.price))
            .collect()
    }

    /// Write a full set of prices (replaces stale entries).
    pub fn update(&self, prices: HashMap<String, f64>) {
        // Use try_write for non-blocking update from background task
        if let Ok(mut store) = self.store.try_write() {
            let now = Instant::now();
            for (dex, price) in prices {
                if price > 0.0 {
                    store.insert(dex, Entry { price, fetched_at: now });
                }
            }
            self.counters.writes.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Write a single price entry.
    pub async fn set(&self, dex: &str, price: f64) {
        if price > 0.0 {
            let mut store = self.store.write().await;
            store.insert(dex.to_string(), Entry {
                price,
                fetched_at: Instant::now(),
            });
            self.counters.writes.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Evict all entries, forcing a full refresh on next read.
    pub async fn invalidate(&self) {
        let mut store = self.store.write().await;
        store.clear();
    }

    /// Return telemetry counters.
    pub fn stats(&self) -> serde_json::Value {
        let hits   = self.counters.hits.load(Ordering::Relaxed);
        let misses = self.counters.misses.load(Ordering::Relaxed);
        let total  = hits + misses;
        let ratio  = if total > 0 { hits as f64 / total as f64 } else { 0.0 };
        serde_json::json!({
            "hits":      hits,
            "misses":    misses,
            "writes":    self.counters.writes.load(Ordering::Relaxed),
            "hit_ratio": (ratio * 10000.0).round() / 10000.0,
            "ttl_secs":  self.ttl.as_secs_f64(),
        })
    }
}

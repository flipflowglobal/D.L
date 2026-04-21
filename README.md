# AUREON — Autonomous DeFi Trading & Agent Platform

> Production-grade autonomous DeFi trading system — multi-strategy, multi-chain, self-healing.
> Supports paper trading, live on-chain swaps, flash loan arbitrage, multi-agent swarms, and a full REST API.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Build System](#build-system)
5. [Wallet Setup](#wallet-setup)
6. [Configuration](#configuration)
7. [Running the Trading Bot](#running-the-trading-bot)
8. [Running the API Server](#running-the-api-server)
9. [Watchdog Self-Healing System](#watchdog-self-healing-system)
10. [Multi-Agent Swarm](#multi-agent-swarm)
11. [API Reference](#api-reference)
12. [Running DL_SYSTEM](#running-dl_system)
13. [Smart Contracts](#smart-contracts)
14. [Rust Sidecars](#rust-sidecars)
15. [Docker Deployment](#docker-deployment)
16. [Module Reference](#module-reference)
17. [Security Checklist](#security-checklist)
18. [Troubleshooting](#troubleshooting)
19. [Project Status](#project-status)

---

## Architecture Overview

```
AUREON/
├── main.py                        # FastAPI server — 40+ REST endpoints
├── trade.py                       # CLI trading bot (paper / live / flash)
├── config.py                      # Environment variable loader
├── compiler.py                    # Solidity compiler & contract deployer
├── build.py                       # Parallel build orchestrator
│
├── engine/                        # Core trading engine
│   ├── market_data.py             # ETH/USD price feed (CoinGecko, cached)
│   ├── portfolio.py               # Balance tracking, P&L, trade log (Cython)
│   ├── risk_manager.py            # Position limits, daily caps (Cython)
│   ├── price_cache.py             # TTL-based price cache singleton
│   ├── arbitrage/
│   │   └── arbitrage_scanner.py   # Cross-DEX spread detector
│   ├── dex/
│   │   ├── uniswap_v3.py          # On-chain Uniswap V3 quoter
│   │   ├── sushiswap.py           # SushiSwap router
│   │   └── liquidity_monitor.py   # DEX liquidity feed
│   ├── execution/
│   │   ├── executor.py            # Paper trade executor
│   │   ├── swap_executor.py       # Live Uniswap V3 / EIP-1559 swaps
│   │   └── web3_executor.py       # Raw ETH transfer executor
│   └── strategies/
│       └── mean_reversion.py      # Statistical signal generator (Cython)
│
├── intelligence/                  # Multi-agent AI system
│   ├── trading_agent.py           # Agent definitions, registry, lifecycle
│   ├── autonomy.py                # Autonomous trading loop core
│   ├── memory.py                  # Async SQLite agent memory
│   ├── swarm.py                   # Swarm coordinator & consensus engine
│   └── alchemy_client.py          # Alchemy RPC enhanced client
│
├── nexus_arb/                     # Advanced algorithms
│   ├── bellman_ford.py            # Negative-cycle DEX arbitrage
│   ├── ppo_agent.py               # PPO actor-critic RL policy
│   ├── cma_es.py                  # CMA-ES parameter optimiser
│   ├── thompson_sampling.py       # Multi-armed bandit DEX router
│   └── kalman_filter.py           # Unscented Kalman Filter (price)
│
├── watchdog/                      # Self-healing monitoring system
│   ├── kernel.py                  # WatchdogKernel orchestrator
│   ├── event_bus.py               # Typed async event bus
│   ├── dashboard.py               # Health dashboard (REST endpoints)
│   ├── registry.py                # Agent registry & discovery
│   ├── agents/
│   │   ├── base.py                # WatchdogAgent base class
│   │   ├── file_agent.py          # File integrity monitor
│   │   ├── process_agent.py       # Process uptime & CPU monitor
│   │   ├── service_agent.py       # HTTP health checks (sidecars)
│   │   ├── db_agent.py            # SQLite integrity monitor
│   │   ├── resource_agent.py      # CPU / memory / disk monitor
│   │   └── trade_agent.py         # Trading loop liveness monitor
│   ├── healing/
│   │   └── actions.py             # HealingStrategy gate-keeper
│   └── mind/                      # SharedMind cross-agent consensus
│       ├── shard.py               # Per-agent memory shard (vector clock)
│       ├── core.py                # MindCore synchronisation hub
│       ├── consensus.py           # Quorum-based heal proposals
│       └── sync.py                # SyncBridge & SharedMind façade
│
├── vault/                         # Wallet storage (git-ignored)
│   ├── wallet.json                # Encrypted private key
│   └── wallet_config.py           # Web3 connection + signing
│
├── DL_SYSTEM/                     # Quest & airdrop automation
│   ├── main.py                    # Orchestrator (10-min cycles)
│   ├── core/
│   │   ├── orchestrator.py        # Task scheduling
│   │   ├── state_manager.py       # Persistent state (file-locked)
│   │   ├── logger.py              # Timezone-aware UTC logging
│   │   └── integrity.py           # File integrity verification
│   └── agents/
│       ├── galxe_agent.py         # Galxe quest automation
│       └── layer3_agent.py        # Layer3 task automation
│
├── contracts/                     # Solidity smart contracts
│   ├── FlashLoanArbitrage.sol     # Flash loan arbitrage executor
│   ├── NexusFlashReceiver.sol     # Aave V3 flash receiver
│   └── interfaces/                # DEX / lending protocol interfaces
│
├── dex-oracle/                    # Rust sidecar — DEX price oracle
│   └── src/main.rs                # Axum HTTP API on port 9001
│
├── tx-engine/                     # Rust sidecar — TX signing engine
│   └── src/main.rs                # Axum HTTP API on port 9002
│
├── build/                         # Compiled artifacts
│   ├── cython/                    # .so Cython extensions
│   ├── solidity/                  # ABI + bytecode
│   └── report.json                # Build timings & status
│
├── scripts/
│   ├── lint_alignment.py          # AI safety scanner
│   └── merkle_lint.py             # Lint result Merkle aggregator
│
├── tests/                         # pytest suite
│   ├── conftest.py                # Network mocking fixtures
│   ├── test_watchdog.py           # Watchdog integration tests (44 tests)
│   └── test_mainnet.py            # Mainnet integration tests (skipped offline)
│
├── .github/
│   └── workflows/
│       ├── ci.yml                 # CI: lint + test + docker build
│       ├── ci-lint.yml            # Lint gate: pyflakes + clippy + solhint
│       ├── system.yml             # System validation + integrity check
│       ├── python-package.yml     # Multi-version Python build
│       ├── copilot-setup-steps.yml # Copilot AI agent setup
│       └── security.yml           # CodeQL + dependency audit
│
├── Dockerfile                     # Multi-stage: base → deps → test → api/bot
├── build.py                       # Build orchestrator (parallel)
├── setup_cython.py                # Cython compiler configuration
├── compiler.py                    # Solidity compiler + deployer
├── requirements.txt               # Python dependencies
└── pytest.ini                     # Test configuration
```

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10 | 3.11 |
| Rust + Cargo | 1.70 | latest stable |
| Node.js | 18 | 20 (for solhint) |
| RPC endpoint | Any Ethereum JSON-RPC | Alchemy / Infura |
| ETH wallet | Any EOA | Hardware wallet address |

---

## Installation

```bash
# 1. Clone
git clone https://github.com/flipflowglobal/D.L.git
cd D.L

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate        # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Build compiled extensions (Cython + Rust + Solidity)
python build.py

# 5. Copy and edit environment config
cp .env.example .env
nano .env
```

---

## Build System

AUREON uses a parallel async build orchestrator (`build.py`) that compiles four pipelines simultaneously.

### Run Full Build

```bash
python build.py
```

Output — `build/report.json`:
```json
{
  "wall_clock_seconds": 1.66,
  "pipelines": { "cython": "ok", "rust_dex": "ok", "rust_tx": "ok", "solidity": "ok" }
}
```

### Selective Builds

```bash
python build.py --cython     # Cython .so extensions only
python build.py --rust       # Both Rust sidecars only
python build.py --sol        # Solidity contracts only
python build.py --clean      # Remove all build artifacts
```

### What Gets Built

| Pipeline | Source | Output | Time |
|----------|--------|--------|------|
| **Cython** | `engine/portfolio.pyx`, `risk_manager.pyx`, `strategies/mean_reversion.pyx` | `build/cython/*.so` | ~1.5s |
| **Rust dex-oracle** | `dex-oracle/src/` | `dex-oracle/target/release/dex-oracle` (6.9 MB) | ~30s cold / cached |
| **Rust tx-engine** | `tx-engine/src/` | `tx-engine/target/release/tx-engine` (6.6 MB) | ~30s cold / cached |
| **Solidity** | `contracts/FlashLoanArbitrage.sol` | `build/solidity/*.abi`, `*.bin` | ~0.3s |

### Cython Optimisation Flags

All Cython extensions compile with maximum optimisation:
- `-O3 -march=native -ffast-math -funroll-loops` (Linux/macOS)
- `/O2 /fp:fast /arch:AVX2` (Windows)
- `-O2 -ffast-math` (Android/ARM64 Termux)

---

## Wallet Setup

```bash
python setup_wallet.py
```

Follow the prompts to generate a new wallet or import an existing private key. The encrypted wallet is saved to `vault/wallet.json` (git-ignored).

**Never commit your private key or wallet.json.**

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
# ── Required ──────────────────────────────────────────────────────────────────
RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS

# ── Optional: Alchemy API key (alternative to full RPC_URL) ───────────────────
ALCHEMY_API_KEY=YOUR_KEY

# ── Trading Parameters ────────────────────────────────────────────────────────
TRADE_SIZE_ETH=0.05          # ETH per trade
SCAN_INTERVAL=30             # seconds between scans
MIN_PROFIT_USD=2.0           # minimum profitable spread
GAS_BUDGET_USD=5.0           # max gas cost per trade
INITIAL_USD=10000            # paper trading starting balance
MAX_DAILY_TRADES=20          # hard daily cap
MAX_POSITION_USD=2000        # max single position size

# ── Blockchain ────────────────────────────────────────────────────────────────
CHAIN_ID=1                   # 1=Ethereum, 42161=Arbitrum, 137=Polygon
DRY_RUN=true                 # paper trading mode (set false for live)

# ── Quest Automation (DL_SYSTEM) ──────────────────────────────────────────────
GALXE_EMAIL=your@email.com
GALXE_PASSWORD=yourpassword
LAYER3_EMAIL=your@email.com
LAYER3_PASSWORD=yourpassword
```

---

## Running the Trading Bot

### Paper Trading (safe, no real funds)

```bash
python trade.py
```

### Live Trading (real ETH — use with caution)

```bash
DRY_RUN=false python trade.py --live
```

### Flash Loan Arbitrage

```bash
python trade.py --flash
```

### Trading Strategies

| Flag | Strategy | Algorithm |
|------|----------|-----------|
| `--strategy arb` | Cross-DEX arbitrage | Bellman-Ford negative cycle |
| `--strategy ppo` | RL policy | PPO actor-critic |
| `--strategy mean_reversion` | Mean reversion | CMA-ES optimisation |
| `--strategy flash_loan` | Flash loan arb | Thompson Sampling bandit |
| `--strategy adaptive` | Adaptive | UKF + Thompson Sampling |

---

## Running the API Server

```bash
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

The server starts on **port 8010**. Interactive docs available at:
- **Swagger UI:** `http://localhost:8010/docs`
- **ReDoc:** `http://localhost:8010/redoc`

---

## Watchdog Self-Healing System

AUREON includes a built-in self-healing watchdog that monitors all system components and automatically recovers from failures.

### 6 Monitoring Agents

| Agent | Monitors | Poll Interval | Healing Action |
|-------|----------|---------------|----------------|
| **FileAgent** | vault/, database, log files | 15s | `git checkout` restore |
| **ProcessAgent** | Process uptime, CPU usage | 10s | Process restart |
| **ServiceAgent** | HTTP health checks (sidecars) | 10s | Sidecar restart |
| **DatabaseAgent** | SQLite integrity & locks | 60s | VACUUM + recreate |
| **ResourceAgent** | CPU, memory, disk | 30s | Cache eviction |
| **TradeLoopAgent** | Trading loop liveness | 20s | Loop restart |

### SharedMind Consensus

Before any heal executes, the **SharedMind** consensus engine runs a quorum vote:

1. **Gate 1 — HealingStrategy**: 30s cooldown, max 10 attempts per 10-minute window
2. **Gate 2 — ConsensusEngine**: Conflict guard (blocks concurrent heals on same subsystem) + 51% peer quorum vote
3. **Gate 3** — `agent.heal()` executes

```
Event arrives → HealingStrategy → ConsensusEngine → agent.heal()
                (cooldown/cap)    (quorum voting)    (actual fix)
```

### Dashboard Endpoints

```
GET  /watchdog/health              Full system snapshot
GET  /watchdog/agents              All monitoring agents
GET  /watchdog/agents/{id}         Single agent status
GET  /watchdog/events?n=100        Last N events
GET  /watchdog/heals               Healing history
GET  /watchdog/mind                SharedMind global state
GET  /watchdog/mind/timeline       Event timeline
GET  /watchdog/mind/shards/{id}    Agent shard data
POST /watchdog/heal/{id}           Manual heal trigger
```

---

## Multi-Agent Swarm

Create and coordinate hundreds of trading agents simultaneously:

```bash
# Create 5 agents with different strategies
curl -X POST http://localhost:8010/agents/batch -d '{
  "count": 5,
  "strategy": "arb",
  "min_profit_usd": 3.0
}'

# Start all agents
curl -X POST http://localhost:8010/swarm/start

# Get consensus signal
curl http://localhost:8010/swarm/consensus

# Get swarm-wide metrics
curl http://localhost:8010/swarm/metrics
```

---

## API Reference

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | System identity & version |
| `GET` | `/health` | Full health check + watchdog status |
| `GET` | `/status` | Agent loop state + watchdog online |
| `GET` | `/strategies` | Available trading strategies |
| `GET` | `/chains` | Supported blockchains |
| `GET` | `/tokens` | Supported tokens |

### Agent Lifecycle

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/agents` | Create a new trading agent |
| `GET` | `/agents` | List all agents |
| `GET` | `/agents/{id}` | Get agent details |
| `PATCH` | `/agents/{id}` | Update config (profit, interval, dry_run) |
| `DELETE` | `/agents/{id}` | Remove agent |
| `POST` | `/agents/{id}/start` | Start agent trading loop |
| `POST` | `/agents/{id}/stop` | Stop agent |
| `POST` | `/agents/{id}/reset` | Reset to idle |
| `GET` | `/agents/{id}/performance` | P&L, ROI, metrics |
| `POST` | `/agents/batch` | Create up to 10 agents |

### Swarm

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/swarm/consensus` | Aggregate signal across all agents |
| `GET` | `/swarm/metrics` | Swarm-wide P&L and agent counts |
| `POST` | `/swarm/start` | Start all idle agents |
| `POST` | `/swarm/stop` | Stop all running agents |

### Memory & Registry

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/memory/{agent_id}` | List all memory entries |
| `GET` | `/memory/{agent_id}/{key}` | Read single entry |
| `DELETE` | `/memory/{agent_id}/{key}` | Delete entry |
| `DELETE` | `/memory/{agent_id}` | Clear all agent memory |
| `POST` | `/registry/save` | Persist agent snapshots |
| `POST` | `/registry/load` | Restore agents from snapshot |

### Wallet

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/wallet/generate` | Generate fresh Ethereum wallet |

### Legacy

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/aureon/start` | Start legacy trading loop |
| `POST` | `/aureon/stop` | Stop legacy trading loop |

---

## Running DL_SYSTEM

DL_SYSTEM automates Web3 quests and airdrops on Galxe and Layer3:

```bash
python DL_SYSTEM/main.py
```

Runs in a background thread with 10-minute cycles. Requires `GALXE_EMAIL`, `GALXE_PASSWORD`, `LAYER3_EMAIL`, `LAYER3_PASSWORD` in `.env`.

---

## Smart Contracts

### Compile

```bash
python build.py --sol
# Output: build/solidity/FlashLoanArbitrage.abi + FlashLoanArbitrage.bin
```

### Deploy

```bash
python compiler.py --deploy-only
```

Requires `RPC_URL`, `PRIVATE_KEY`, and `PROFIT_WALLET` in `.env`.

### Contracts

| Contract | Purpose |
|----------|---------|
| `FlashLoanArbitrage.sol` | Multi-DEX flash loan arbitrage executor (Aave V3 → Uniswap/SushiSwap/Curve/Balancer) |
| `NexusFlashReceiver.sol` | Alternative Aave V3 flash loan receiver |

### Supported Flash Loan Pools

| Network | Pool Address |
|---------|-------------|
| Ethereum Mainnet | `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` |
| Sepolia Testnet | `0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951` |

---

## Rust Sidecars

Two high-performance Rust services run alongside the Python core:

### dex-oracle (Port 9001)

Real-time parallel DEX price feeds:

```bash
./dex-oracle/target/release/dex-oracle
# GET http://localhost:9001/price/ETH
# GET http://localhost:9001/prices
```

Built with: `tokio`, `axum`, `alloy` (Ethereum), `reqwest`

### tx-engine (Port 9002)

EIP-1559 transaction signing and broadcasting:

```bash
./tx-engine/target/release/tx-engine
# POST http://localhost:9002/sign
# POST http://localhost:9002/broadcast
```

Built with: `tokio`, `axum`, `alloy` (with signers), `serde_json`

### Build Rust Sidecars

```bash
python build.py --rust
# or manually:
cd dex-oracle && cargo build --release
cd tx-engine  && cargo build --release
```

Both compile with: `opt-level=3`, fat LTO, single codegen unit, `panic=abort`, stripped binaries.

---

## Docker Deployment

### Build Images

```bash
# API server
docker build --target api -t aureon-api .

# Trading bot
docker build --target bot -t aureon-bot .
```

### Run API Server

```bash
docker run -d \
  --env-file .env \
  -p 8010:8010 \
  --name aureon-api \
  aureon-api
```

### Run Trading Bot

```bash
docker run -d \
  --env-file .env \
  -e TRADE_MODE=paper \
  --name aureon-bot \
  aureon-bot
```

### Docker Stages

| Stage | Base | Purpose |
|-------|------|---------|
| `base` | python:3.11-slim | System deps (gcc, libssl-dev, libffi-dev) |
| `deps` | base | `pip install -r requirements.txt` (cached) |
| `test` | deps | `pytest --tb=short -q` |
| `api` | deps | FastAPI server, port 8010, healthcheck |
| `bot` | deps | Trading daemon (paper or live) |

---

## Module Reference

### engine/

| Module | Purpose |
|--------|---------|
| `market_data.py` | ETH/USD from CoinGecko with TTL cache |
| `portfolio.py` | Balance, P&L, trade history (Cython optimised) |
| `risk_manager.py` | Daily caps, position sizing (Cython optimised) |
| `price_cache.py` | Thread-safe TTL price cache singleton |
| `arbitrage/arbitrage_scanner.py` | Cross-DEX spread detection |
| `dex/uniswap_v3.py` | On-chain Uniswap V3 quoter |
| `dex/sushiswap.py` | SushiSwap router interface |
| `dex/liquidity_monitor.py` | DEX liquidity price aggregator |
| `execution/executor.py` | Paper trade simulator |
| `execution/swap_executor.py` | Live Uniswap V3 EIP-1559 swaps |
| `execution/web3_executor.py` | Raw ETH transfer executor |
| `strategies/mean_reversion.py` | Statistical signal generator (Cython) |

### intelligence/

| Module | Purpose |
|--------|---------|
| `trading_agent.py` | Agent definitions, multi-strategy registry |
| `autonomy.py` | Autonomous trading loop core |
| `memory.py` | Async SQLite key-value agent memory |
| `swarm.py` | Multi-agent coordinator & consensus |
| `alchemy_client.py` | Enhanced Alchemy RPC client |

### nexus_arb/

| Module | Algorithm |
|--------|-----------|
| `bellman_ford.py` | Negative-cycle detection for multi-hop arb |
| `ppo_agent.py` | Proximal Policy Optimisation (actor-critic RL) |
| `cma_es.py` | Covariance Matrix Adaptation Evolution Strategy |
| `thompson_sampling.py` | Thompson Sampling multi-armed bandit |
| `kalman_filter.py` | Unscented Kalman Filter for price smoothing |

---

## Security Checklist

- [ ] `vault/wallet.json` is in `.gitignore` (never commit private keys)
- [ ] `chmod 600 .env vault/wallet.json` — restrict file permissions
- [ ] Use a **dedicated trading wallet** — never use your main wallet
- [ ] Test on **Sepolia testnet** before mainnet
- [ ] Set `DRY_RUN=true` until profitable in paper mode
- [ ] Set conservative `MAX_DAILY_TRADES` and `MAX_POSITION_USD`
- [ ] Rotate API keys regularly (Alchemy, Infura)
- [ ] Run `python scripts/lint_alignment.py` before every deploy
- [ ] Never expose `PRIVATE_KEY` in logs, API responses, or error messages

---

## Troubleshooting

### Import errors on startup

```bash
python build.py        # rebuild Cython extensions
pip install -r requirements.txt --upgrade
```

### RPC connection refused

Check `RPC_URL` in `.env`. Test with:
```bash
python -c "from vault.wallet_config import w3; print(w3.is_connected())"
```

### Watchdog not starting

```bash
pip install psutil aiosqlite
```

### Rust sidecar build fails

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
python build.py --rust
```

### Solidity compilation fails

```bash
python -c "import solcx; solcx.install_solc('0.8.20')"
python build.py --sol
```

### Tests failing offline

```bash
AUREON_ENV=test pytest tests/ -q    # skips mainnet/integration tests
```

---

## Project Status

| Component | Status | Notes |
|-----------|--------|-------|
| FastAPI server | Production | 40+ endpoints, v3.0 |
| Paper trading | Production | Full simulation with P&L tracking |
| Live trading | Beta | Mainnet tested, use with caution |
| Flash loan arb | Beta | Aave V3 + Uniswap V3 / Curve / Balancer |
| Multi-agent swarm | Production | Swarm coordination + consensus |
| Watchdog self-healing | Production | 6 agents, SharedMind consensus, 44 tests |
| Cython extensions | Production | portfolio, risk_manager, mean_reversion |
| Rust dex-oracle | Production | Compiled, 6.9 MB binary |
| Rust tx-engine | Production | Compiled, 6.6 MB binary |
| Smart contracts | Beta | Compiled ABI + bytecode, deploy-ready |
| DL_SYSTEM quests | Beta | Galxe + Layer3 automation |
| CI/CD pipelines | Production | pyflakes, clippy, solhint, CodeQL |
| Docker build | Production | Multi-stage, healthcheck |
| Test suite | Production | 44 watchdog tests, offline-safe |

---

## License

Proprietary — © Darcel King. All rights reserved.
Unauthorised use, reproduction, or distribution is strictly prohibited.

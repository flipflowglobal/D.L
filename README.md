# AUREON — On-The-DL Trading System

Autonomous DeFi trading bot for Ethereum mainnet.
Supports paper trading, live on-chain swaps via Uniswap V3, cross-DEX arbitrage scanning, and a FastAPI cognitive agent server.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Wallet Setup](#wallet-setup)
5. [Configuration (.env)](#configuration-env)
6. [Running the Trading Bot](#running-the-trading-bot)
7. [Running the API Server](#running-the-api-server)
8. [Running DL\_SYSTEM](#running-dl_system)
9. [Module Reference](#module-reference)
10. [Security Checklist](#security-checklist)
11. [Troubleshooting](#troubleshooting)
12. [Project Status](#project-status)

---

## Architecture Overview

```
AUREON/
├── trade.py                      # Main trading bot  (paper + live)
├── setup_wallet.py               # One-time wallet setup
├── main.py                       # FastAPI cognitive agent server
│
├── engine/
│   ├── market_data.py            # ETH/USD price via CoinGecko
│   ├── portfolio.py              # Balance tracking, P&L, trade log
│   ├── risk_manager.py           # Daily trade limits
│   ├── dex/
│   │   ├── uniswap_v3.py         # On-chain Uniswap V3 price quotes
│   │   ├── sushiswap.py          # On-chain SushiSwap price quotes
│   │   └── liquidity_monitor.py  # DEX liquidity price feed
│   ├── arbitrage/
│   │   └── arbitrage_scanner.py  # Cross-DEX spread detector
│   ├── strategies/
│   │   └── mean_reversion.py     # BUY / SELL / HOLD signal generator
│   └── execution/
│       ├── executor.py           # Paper trade executor
│       ├── swap_executor.py      # Live Uniswap V3 swap executor
│       └── web3_executor.py      # Raw ETH transfer executor
│
├── vault/                        # Wallet storage (git-ignored)
│   ├── wallet.json               # Address + private key  (chmod 600)
│   └── trade_log.json            # Trade history
│
├── intelligence/
│   ├── memory.py                 # Async SQLite key-value store
│   └── autonomy.py               # Agent loop (integrates trading engine)
│
└── DL_SYSTEM/                    # Quest / airdrop automation
    ├── main.py
    ├── core/   (orchestrator, state, logger, config)
    ├── agents/ (task_agent, web_agent_v2)
    └── integrations/ (galxe, layer3)
```

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| pip | latest | `pip install --upgrade pip` |
| RPC endpoint | — | Free Alchemy or Infura account |
| ETH wallet | — | Created by `setup_wallet.py` or imported |
| ETH balance | ≥ 0.05 ETH | Gas fees + trade capital for live mode |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/flipflowglobal/D.L.git
cd D.L

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

# 3. Install all dependencies
pip install -r requirements.txt

# 4. (Optional) Install Playwright browsers — only needed for DL_SYSTEM quest tasks
playwright install chromium
```

---

## Wallet Setup

Run **once** before starting the bot:

```bash
python setup_wallet.py
```

```
  ╔══════════════════════════════════════╗
  ║      AUREON  —  Wallet Setup          ║
  ╚══════════════════════════════════════╝

  [1] Generate a new wallet
  [2] Import an existing private key

  Choice [1/2]:
```

**Option 1 — Generate new wallet**
Creates a fresh Ethereum address. Back up the private key shown on screen — it is never stored anywhere else.

**Option 2 — Import existing wallet** (MetaMask / Trust Wallet / Ledger export)
- MetaMask: Settings → Security & Privacy → Reveal Secret / Export Private Key
- Trust Wallet: Settings → Wallets → select wallet → Export private key

The script saves to `vault/wallet.json` (chmod 600) and automatically patches `WALLET_ADDRESS` and `PRIVATE_KEY` in `.env`.

---

## Configuration (.env)

```bash
nano .env    # or any text editor
```

### Required

```env
# Ethereum mainnet RPC — get a free key at alchemy.com or infura.io
RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY

# Filled automatically by setup_wallet.py
WALLET_ADDRESS=0xYourAddress
PRIVATE_KEY=your64hexcharacterprivatekey
```

**Get a free RPC key:**
1. Create account at [alchemy.com](https://www.alchemy.com) (free)
2. New App → Ethereum → Mainnet
3. Copy the **HTTPS** URL → paste as `RPC_URL`

### Trading parameters (optional — defaults shown)

```env
SCAN_INTERVAL=30          # seconds between cycles
TRADE_SIZE_ETH=0.05       # ETH amount per trade
MIN_PROFIT_USD=2.0        # minimum arbitrage profit to execute (USD)
GAS_BUDGET_USD=5.0        # max gas cost allowed per transaction (USD)
INITIAL_USD=10000         # starting paper-portfolio value for P&L
STRATEGY_WINDOW=12        # mean-reversion price lookback (cycles)
STRATEGY_THRESHOLD=0.015  # % deviation from mean to trigger a signal
MAX_DAILY_TRADES=20       # risk guard: max trades per day
MAX_POSITION_USD=2000     # risk guard: max single-position size (USD)
```

### DL_SYSTEM quest credentials (optional)

```env
GALXE_EMAIL=your@email.com
GALXE_PASSWORD=yourpassword
LAYER3_EMAIL=your@email.com
LAYER3_PASSWORD=yourpassword
```

---

## Running the Trading Bot

### Step 1 — Paper trading (recommended first)

No wallet funding needed. Uses live prices but **never sends transactions**.

```bash
python trade.py
```

```
  ╔══════════════════════════════════════════════════╗
  ║  AUREON Trading Bot  —  PAPER TRADING            ║
  ╚══════════════════════════════════════════════════╝
  Wallet  : 0xYourAddress
  Interval: 30s  |  Trade size: 0.05 ETH
  Min profit: $2.0  |  Gas budget: $5.0

  ── Cycle    1  [2025-04-01 12:00:00 UTC] ──────────────
  ETH/USD  : $3,412.50
  SIGNAL   : HOLD
  PORTFOLIO: $10,000.00 USD  0.0000 ETH  P&L: $+0.00

  ── Cycle    2  [2025-04-01 12:00:30 UTC] ──────────────
  ETH/USD  : $3,406.10
  ARB OPP  : buy sushiswap @ $3,402.80 → sell uniswap_v3 @ $3,412.50
             | spread 0.285% | est $1.36
  PORTFOLIO: $10,000.00 USD  0.0000 ETH  P&L: $+0.00
```

Press `Ctrl+C` to stop. Trade log saved to `vault/trade_log.json`.

### Step 2 — Live mainnet trading

> **Warning:** Real ETH will be spent. Start with a small `TRADE_SIZE_ETH` (e.g. `0.01`).

```bash
python trade.py --live
```

You will be prompted to type `YES` before any transaction is sent:

```
  *** LIVE MODE — real funds will be used ***
  Type YES to confirm: YES
```

**What the bot does each cycle:**
1. Fetches ETH/USD price from CoinGecko
2. Queries Uniswap V3 and SushiSwap for on-chain prices
3. If spread ≥ 0.3% and estimated profit ≥ `MIN_PROFIT_USD` → executes arbitrage swap
4. Otherwise applies mean-reversion signal (BUY / SELL / HOLD)
5. Enforces gas budget and daily trade limits before every transaction

### Running in the background

```bash
nohup python trade.py > trade.log 2>&1 &
echo $! > trade.pid

tail -f trade.log       # watch live output
kill $(cat trade.pid)   # stop
```

---

## Running the API Server

The FastAPI server lets you control the trading agent via HTTP and monitor memory.

```bash
uvicorn main:app --host 0.0.0.0 --port 8010
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | System info |
| `GET` | `/health` | Health check → `{"health":"ok"}` |
| `GET` | `/status` | Agent running state |
| `POST` | `/aureon/start?agent_id=AUREON` | Start autonomous trading agent |
| `POST` | `/aureon/stop` | Stop agent gracefully |
| `GET` | `/memory/{agent_id}/{key}` | Read a memory value |

### Quick start via curl

```bash
# Start server in background
nohup uvicorn main:app --host 0.0.0.0 --port 8010 > aureon.log 2>&1 &

# Launch agent
curl -X POST "http://localhost:8010/aureon/start?agent_id=AUREON"

# Check if running
curl http://localhost:8010/status

# Read last trade action from agent memory
curl http://localhost:8010/memory/AUREON/last_result

# Stop agent
curl -X POST http://localhost:8010/aureon/stop
```

### Using the full daemon script

```bash
bash aureon_daemon.sh   # installs deps, starts server, runs health check
```

---

## Running DL\_SYSTEM

DL_SYSTEM automates on-chain quest tasks on Galxe and Layer3 to earn rewards.

### 1. Configure credentials in `.env`

```env
GALXE_EMAIL=your@email.com
GALXE_PASSWORD=yourpassword
LAYER3_EMAIL=your@email.com
LAYER3_PASSWORD=yourpassword
```

### 2. Add tasks to state file

Create `DL_SYSTEM/data/state.json`:

```json
{
  "tasks": [
    {
      "id": "galxe-daily-1",
      "name": "Galxe Daily Quest",
      "type": "galxe"
    },
    {
      "id": "layer3-daily-1",
      "name": "Layer3 Daily Quest",
      "type": "layer3"
    }
  ]
}
```

### 3. Run

```bash
python DL_SYSTEM/main.py
```

Runs every 10 minutes. Logs written to `DL_SYSTEM/logs/logs.json`.

---

## Module Reference

### `setup_wallet.py`

```bash
python setup_wallet.py    # interactive: generate or import wallet
```

Saves to `vault/wallet.json`, patches `.env`.

---

### `engine/market_data.py`

```python
from engine.market_data import MarketData
price = MarketData().get_price()   # float — live ETH/USD from CoinGecko
```

---

### `engine/portfolio.py`

```python
from engine.portfolio import Portfolio

p = Portfolio(initial_usd=10000.0)
p.buy(price=3400.0, amount=0.05)
p.sell(price=3450.0, amount=0.05)
p.log_trade("BUY", 3400.0, 0.05, tx_hash="0xabc...")

print(p.summary())
# {'balance_usd': 9830.0, 'balance_eth': 0.05,
#  'pnl_usd': +2.50, 'pnl_pct': 0.025, 'trade_count': 1}

p.save_trade_log()   # → vault/trade_log.json
```

---

### `engine/arbitrage/arbitrage_scanner.py`

```python
from engine.arbitrage.arbitrage_scanner import ArbitrageScanner

# On-chain mode (reads live DEX prices)
arb = ArbitrageScanner(rpc_url="https://eth-mainnet.g.alchemy.com/v2/KEY")

# Simulation mode (no RPC needed — uses CoinGecko + noise)
arb = ArbitrageScanner()

opportunities = arb.scan()
if opportunities:
    opp = opportunities[0]
    # opp = {
    #   'buy_on': 'sushiswap',    'buy_price': 3402.80,
    #   'sell_on': 'uniswap_v3',  'sell_price': 3412.50,
    #   'spread_pct': 0.285,      'est_profit_pct': -0.315
    # }
```

---

### `engine/execution/swap_executor.py`

```python
from vault.wallet_config import WalletConfig
from engine.execution.swap_executor import SwapExecutor

wallet   = WalletConfig(private_key="0x...", rpc_url="https://...")
executor = SwapExecutor(wallet, rpc_url)

# Sell ETH → USDC on Uniswap V3 (live mainnet)
tx_hash = executor.swap_eth_to_usdc(
    amount_eth=0.05,
    slippage=0.005,           # 0.5 % max slippage
    expected_usdc=170.13      # quote from UniswapV3.get_best_eth_price()
)

# Buy ETH ← USDC
tx_hash = executor.swap_usdc_to_eth(amount_usdc=170.0, slippage=0.005)

# Estimate gas cost in USD before trading
gas_usd = executor.estimate_gas_usd()
```

---

### `engine/dex/uniswap_v3.py`

```python
from engine.dex.uniswap_v3 import UniswapV3

uni   = UniswapV3(rpc_url="https://...")
price = uni.get_best_eth_price()   # best price across all fee tiers
price = uni.get_eth_price_usdc(fee=500)   # specific 0.05% pool
```

---

### `engine/dex/sushiswap.py`

```python
from engine.dex.sushiswap import SushiSwap

sushi = SushiSwap(rpc_url="https://...")
price = sushi.get_eth_price_usdc()
```

---

### `intelligence/memory.py`

```python
import asyncio
from intelligence.memory import memory

async def main():
    await memory.init_db()
    await memory.store("agent1", "last_price", "3412.50")
    val = await memory.retrieve("agent1", "last_price")   # → "3412.50"

asyncio.run(main())
```

---

### `vault/wallet_config.py`

```python
from vault.wallet_config import WalletConfig

wallet = WalletConfig(private_key="0x...", rpc_url="https://...")
print(wallet.address)          # 0xYourAddress
print(wallet.is_connected())   # True / False
```

---

## Security Checklist

Before trading with real funds:

- [ ] `vault/wallet.json` has `chmod 600` — verify with `ls -la vault/`
- [ ] `.env` is listed in `.gitignore` — verify with `git check-ignore -v .env`
- [ ] Private key has **not** been committed to git — check `git log --all -p | grep PRIVATE_KEY`
- [ ] `RPC_URL` points to **Ethereum mainnet** — not a testnet
- [ ] `TRADE_SIZE_ETH` is set small (start with `0.01`)
- [ ] `GAS_BUDGET_USD` prevents runaway gas spend
- [ ] `MAX_DAILY_TRADES` caps total exposure per day
- [ ] Tested in paper mode for at least a few hours with no errors
- [ ] Wallet has enough ETH for `TRADE_SIZE_ETH` + gas (min ~0.01 ETH for gas)

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'web3'`**
```bash
pip install -r requirements.txt
```

**`Web3 connection failed`**
- Verify `RPC_URL` in `.env` is a valid Alchemy/Infura HTTPS endpoint
- Test: `python -c "from web3 import Web3; w=Web3(Web3.HTTPProvider('YOUR_RPC')); print(w.is_connected())"`

**`PRIVATE_KEY not set` or `vault/wallet.json not found`**
```bash
python setup_wallet.py
```

**Uniswap/SushiSwap prices return `None`**
- Confirm RPC supports `eth_call` (all Alchemy/Infura tiers do)
- The system automatically falls back to CoinGecko if on-chain quotes fail

**`No module named 'agents'` in DL_SYSTEM**
```bash
# Run from project root, not from inside DL_SYSTEM/
python DL_SYSTEM/main.py    # correct
```

**Port 8010 already in use**
```bash
lsof -ti:8010 | xargs kill -9
uvicorn main:app --host 0.0.0.0 --port 8010
```

---

## Project Status

| Component | Status |
|---|---|
| Wallet setup (generate / import / patch .env) | Complete |
| Market data — CoinGecko live price | Complete |
| On-chain price quotes — Uniswap V3 Quoter | Complete |
| On-chain price quotes — SushiSwap Router | Complete |
| Cross-DEX arbitrage scanner (live + simulation) | Complete |
| Mean-reversion strategy | Complete |
| Risk manager (trade limits + gas budget) | Complete |
| Paper trading engine | Complete |
| Live swap execution — Uniswap V3 SwapRouter | Complete |
| Portfolio tracking + P&L + trade log | Complete |
| FastAPI cognitive agent server | Complete |
| Async SQLite memory | Complete |
| Autonomous agent loop (API-controlled) | Complete |
| DL_SYSTEM quest automation (Galxe, Layer3) | Complete |
| Flash loan arbitrage | Framework only — requires deployed smart contract |

---

## License

See [LICENSE](LICENSE).

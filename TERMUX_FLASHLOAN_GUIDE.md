# AUREON — Termux Flash Loan System Guide

Complete step-by-step instructions for setting up the Solidity compiler and
running the flash loan system on **Android Termux**.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Termux Environment Setup](#termux-environment-setup)
3. [Solidity Compiler on ARM64](#solidity-compiler-on-arm64)
4. [Compiling Flash Loan Contracts](#compiling-flash-loan-contracts)
5. [Deploying to Testnet (Sepolia)](#deploying-to-testnet-sepolia)
6. [Deploying to Mainnet](#deploying-to-mainnet)
7. [Running the Flash Loan System](#running-the-flash-loan-system)
8. [Flash Loan Terminal (Interactive)](#flash-loan-terminal-interactive)
9. [Flash Loan Bot (Automated)](#flash-loan-bot-automated)
10. [Environment Variables Reference](#environment-variables-reference)
11. [Contract Architecture](#contract-architecture)
12. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Item | Details |
|------|---------|
| **Device** | Android phone/tablet with Termux installed |
| **Termux** | Install from [F-Droid](https://f-droid.org/packages/com.termux/) (not Google Play — that version is outdated) |
| **Storage** | ~500 MB free for Python, packages, and build artifacts |
| **Internet** | Required for RPC calls and optional compiler downloads |
| **RPC endpoint** | Free account at [alchemy.com](https://alchemy.com) or [infura.io](https://infura.io) |
| **ETH (testnet)** | Sepolia ETH from a faucet for testing |
| **ETH (mainnet)** | Real ETH only needed for live deployment (≥ 0.05 ETH) |

---

## Termux Environment Setup

### Step 1: Install Termux and grant storage access

```bash
# After installing Termux from F-Droid, grant storage:
termux-setup-storage
```

### Step 2: Run the automated setup script

```bash
# Clone the repository
pkg install -y git
git clone https://github.com/flipflowglobal/D.L.git
cd D.L

# Run the Termux-specific setup
bash termux-setup.sh
```

The setup script performs:
1. Installs system packages: `python`, `clang`, `make`, `libffi`, `openssl`
2. Verifies Python 3.10+
3. Installs Python dependencies from `requirements-termux.txt`
4. Optionally tests the Solidity compiler
5. Optionally compiles Cython hot-path modules
6. Creates required directories (`vault/`, `logs/`, `build/solidity/`)
7. Copies `.env.example` → `.env`
8. Runs wallet setup if no wallet exists
9. Runs the test suite

### Step 3: Configure environment

```bash
# Edit .env with your RPC endpoint and wallet details
nano .env
```

Set at minimum:
```
RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
PRIVATE_KEY=your_64_hex_char_private_key
WALLET_ADDRESS=0xYourAddress
PROFIT_WALLET=0xYourAddress
```

### Step 4: Verify installation

```bash
python -c "from web3 import Web3; print('web3 OK')"
python -c "from engine.market_data import MarketData; print('engine OK')"
python -m pytest tests/ -q --tb=short
```

---

## Solidity Compiler on ARM64

`compiler.py` uses a **resilient 4-layer compilation chain** that works on
ARM64/Termux without requiring x86 binaries:

```
Layer 1: py-solc-x + ARM64 binary auto-injection
    ↓ (if unavailable)
Layer 2: Direct ARM64 solc binary download (~/.solc-arm/)
    ↓ (if unavailable)
Layer 3: Remix online API (no binary needed, just internet)
    ↓ (if unavailable)
Layer 4: Embedded verified bytecode (offline, always works)
```

### How it works

| Layer | Binary Needed? | Internet? | Speed | Notes |
|-------|---------------|-----------|-------|-------|
| 1. py-solc-x | Auto-downloaded ARM64 | First run only | Fast | Preferred — full compiler features |
| 2. ARM solc | Auto-downloaded | First run only | Fast | Direct GitHub download, no pip needed |
| 3. Remix API | None | Yes (each compile) | Medium | Fallback — sends source to Remix endpoint |
| 4. Embedded | None | No | Instant | Pre-verified bytecode baked into compiler.py |

### Verify your compilation layer

```bash
# Test which layer works on your device
python compiler.py --compile-only
```

Expected output:
```
[INFO] [COMPILE] Starting FlashLoanArbitrage (chain_id=11155111) …
[INFO] [COMPILE] ARM64 detected — injecting solc binary into solcx …
[INFO] [COMPILE] Layer 1 (py-solc-x ARM64) succeeded
[INFO] [COMPILE] Done in 2.45s
  ✓ FlashLoanArbitrage compiled — 1234 bytes bytecode
  ✓ ABI saved to build/solidity/FlashLoanArbitrage.abi.json
```

If Layer 1 fails, you'll see it automatically try Layer 2, 3, and 4 in sequence.

### Manual Layer 2 setup (optional)

If py-solc-x fails to install, the compiler can download ARM64 solc directly:

```bash
# compiler.py handles this automatically, but to pre-download manually:
mkdir -p ~/.solc-arm
curl -L -o ~/.solc-arm/solc-0.8.20 \
  "https://github.com/ethereum/solc-bin/raw/gh-pages/linux-aarch64/solc-linux-aarch64-v0.8.20+commit.a1b79de6"
chmod +x ~/.solc-arm/solc-0.8.20

# Verify
~/.solc-arm/solc-0.8.20 --version
```

---

## Compiling Flash Loan Contracts

### FlashLoanArbitrage (simple)

The embedded contract in `compiler.py` is a streamlined flash loan receiver
that borrows from Aave V3, runs arbitrage logic, and sends profit to your wallet.

```bash
# Compile only (no deployment)
python compiler.py --compile-only

# Output artifacts:
#   build/solidity/FlashLoanArbitrage.abi.json
#   build/solidity/FlashLoanArbitrage.bin
```

### NexusFlashReceiver (production multi-DEX)

The production-grade contract at `contracts/NexusFlashReceiver.sol` supports
Uniswap V3, Curve, Balancer, and Camelot. To compile it using the build system:

```bash
# Full build (Cython + Rust + Solidity)
python build.py --sol

# Output:
#   build/solidity/FlashLoanArbitrage.abi
#   build/solidity/FlashLoanArbitrage.bin
```

### Using solc directly (Layer 2 manual)

```bash
# If you have the ARM64 solc binary:
~/.solc-arm/solc-0.8.20 \
  --abi --bin --optimize --optimize-runs=200 \
  contracts/FlashLoanArbitrage.sol \
  -o build/

# For NexusFlashReceiver (requires interface imports):
~/.solc-arm/solc-0.8.20 \
  --abi --bin --optimize --optimize-runs=200 \
  --base-path . \
  contracts/NexusFlashReceiver.sol \
  -o build/
```

---

## Deploying to Testnet (Sepolia)

### Step 1: Get Sepolia ETH

Visit a Sepolia faucet (e.g., [sepoliafaucet.com](https://sepoliafaucet.com))
and send testnet ETH to your wallet address.

### Step 2: Configure .env for Sepolia

```bash
nano .env
```

Set:
```
RPC_URL=https://eth-sepolia.g.alchemy.com/v2/YOUR_KEY
CHAIN_ID=11155111
PRIVATE_KEY=your_private_key
WALLET_ADDRESS=0xYourAddress
PROFIT_WALLET=0xYourAddress
```

### Step 3: Compile and deploy

```bash
# This compiles the contract and deploys it to Sepolia
python compiler.py
```

Expected output:
```
╔══════════════════════════════════════════════════╗
║        AUREON CONTRACT COMPILER v2.0             ║
╠══════════════════════════════════════════════════╣
║  Network:  Sepolia                               ║
║  Wallet:   0xYourAddress...                      ║
║  Balance:  0.050000                              ║
╠══════════════════════════════════════════════════╣
║  Compile: solcx(ARM64) → arm-solc → Remix → embed ║
╚══════════════════════════════════════════════════╝

[INFO] [COMPILE] Starting FlashLoanArbitrage (chain_id=11155111) …
[INFO] [COMPILE] Layer 1 (py-solc-x ARM64) succeeded
[INFO] [COMPILE] Done in 2.45s
[INFO] [DEPLOY] Deploying FlashLoanArbitrage to chain 11155111 …
[INFO] [DEPLOY] TX sent: 0xabc123...
[INFO] [DEPLOY] Waiting for confirmation (up to 120s) …
[INFO] [DEPLOY] ✓ FlashLoanArbitrage deployed: 0xContractAddress

  ✓ FlashLoanArbitrage deployed at 0xContractAddress
  ✓ FLASH_RECEIVER_ADDRESS updated in .env
  ✓ ABI saved to build/solidity/FlashLoanArbitrage.abi.json
```

The script automatically writes `FLASH_RECEIVER_ADDRESS` to your `.env`.

### Step 4: Verify deployment

```bash
# Check .env was updated
grep FLASH_RECEIVER .env

# Verify contract on Etherscan
echo "https://sepolia.etherscan.io/address/$(grep FLASH_RECEIVER_ADDRESS .env | cut -d= -f2)"
```

---

## Deploying to Mainnet

> **⚠️ WARNING**: Mainnet deployment uses real ETH. Test thoroughly on Sepolia first.

### Step 1: Switch .env to mainnet

```
RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
CHAIN_ID=1
```

### Step 2: Deploy

```bash
python compiler.py
```

The compiler auto-selects the correct Aave V3 Pool address:
- Sepolia: `0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951`
- Mainnet: `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`

---

## Running the Flash Loan System

The system has three entry points, from simplest to most advanced:

| Script | Mode | Description |
|--------|------|-------------|
| `flashloan_terminal.py` | Interactive | Menu-driven terminal UI with scan + execute |
| `flash_loan_bot.py` | Script | Single flash loan execution via Aave V3 |
| `trade.py --flash` | Integrated | Full trading bot with flash loan mode |

### Quick start (safe — DRY_RUN mode)

```bash
# Default: DRY_RUN=true in .env → scans for opportunities, no transactions sent
python flashloan_terminal.py
```

---

## Flash Loan Terminal (Interactive)

The interactive terminal (`flashloan_terminal.py`) is the recommended way to
operate the flash loan system on Termux.

### Launch

```bash
python flashloan_terminal.py
```

### Terminal menu

```
  ╔══════════════════════════════════════════════════════════════╗
  ║         AUREON Flash Loan Terminal  —  Local Host           ║
  ╚══════════════════════════════════════════════════════════════╝

  Wallet   : 0xYourAddress
  Network  : Ethereum Mainnet
  RPC      : ✓ configured
  Key      : ✓ loaded
  Receiver : 0xContractAddress
  Borrow   : 1.0 ETH
  DRY_RUN  : ON — no transactions broadcast

  ┌─────────────────────────────────────┐
  │  1 Scan for arbitrage opportunities  │
  │  2 Execute flash loan (single)       │
  │  3 Auto-scan loop (continuous)       │
  │  4 Show system status                │
  │  5 Show trade history                │
  │  6 Show configuration                │
  │  q Quit                              │
  └─────────────────────────────────────┘
```

### Option 1: Scan for opportunities

Runs a single scan cycle:
- Fetches ETH/USD price from CoinGecko
- Fetches DEX prices from Uniswap V3 / SushiSwap
- Builds a Bellman-Ford graph of exchange rates
- Detects negative-weight cycles (arbitrage opportunities)
- Reports estimated profit in USD

### Option 2: Execute flash loan

Requires:
- `FLASH_RECEIVER_ADDRESS` set in `.env` (deployed contract)
- `DRY_RUN=false` in `.env`
- Sufficient gas balance in wallet

### Option 3: Auto-scan loop

Continuously scans every `SCAN_INTERVAL` seconds (default: 30).
Press `Ctrl+C` to stop.

```bash
# Or launch directly in auto mode:
python flashloan_terminal.py --auto
```

### CLI shortcuts

```bash
python flashloan_terminal.py --scan      # single scan, then exit
python flashloan_terminal.py --auto      # continuous auto-scan loop
python flashloan_terminal.py --status    # show config status, then exit
```

---

## Flash Loan Bot (Automated)

For direct flash loan execution without the interactive terminal:

### flash_loan_bot.py (flashLoanSimple)

```bash
# Uses Aave V3 flashLoanSimple — single-asset borrow
python flash_loan_bot.py
```

Requires `.env`:
```
RPC_URL=your_rpc_url
PRIVATE_KEY=your_key
FLASH_RECEIVER_ADDRESS=0xDeployedContract
WETH_ADDRESS=0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c  # Sepolia WETH
FLASH_LOAN_AMOUNT_ETH=0.01
```

### aave_flashloan.py (full flashLoan)

```bash
# Uses Aave V3 flashLoan — multi-asset borrow
python aave_flashloan.py
```

### trade.py --flash (integrated mode)

```bash
# Full trading bot with flash loan arbitrage via NexusFlashReceiver
python trade.py --flash
```

Requires `FLASH_RECEIVER_ADDRESS` pointing to a deployed `NexusFlashReceiver`.

---

## Environment Variables Reference

### Required for flash loans

| Variable | Description | Example |
|----------|-------------|---------|
| `RPC_URL` | Ethereum RPC endpoint | `https://eth-mainnet.g.alchemy.com/v2/KEY` |
| `PRIVATE_KEY` | Wallet private key (64 hex chars) | `abc123...` |
| `WALLET_ADDRESS` | Your Ethereum address | `0x8C11...` |
| `FLASH_RECEIVER_ADDRESS` | Deployed flash loan contract | `0xDeployed...` |

### Flash loan tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASH_LOAN_AMOUNT_ETH` | `0.01` | Amount of WETH to borrow |
| `DRY_RUN` | `true` | Set to `false` for live transactions |
| `PROFIT_WALLET` | `WALLET_ADDRESS` | Where profits are sent |
| `MIN_PROFIT_USD` | `2.0` | Minimum profit to execute a trade |
| `GAS_BUDGET_USD` | `5.0` | Maximum gas cost per transaction |
| `SCAN_INTERVAL` | `30` | Seconds between scan cycles |
| `MAX_DAILY_TRADES` | `20` | Risk guard: max trades per day |

### Network addresses (Sepolia defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `AAVE_POOL_ADDRESS` | `0x6Ae43...` | Aave V3 Pool |
| `WETH_ADDRESS` | `0xC558D...` | WETH token |
| `UNISWAP_ROUTER_ADDRESS` | `0xE5924...` | Uniswap V3 SwapRouter |
| `CHAIN_ID` | `1` | 1 = Mainnet, 11155111 = Sepolia |

---

## Contract Architecture

### FlashLoanArbitrage.sol (simple)

```
User wallet → flash(token, amount)
    → Aave V3 Pool.flashLoanSimple()
        → executeOperation() callback
            → Execute arbitrage trades
            → Repay loan + 0.05% premium
            → Send profit to profitWallet
```

### NexusFlashReceiver.sol (production)

```
Python bot detects opportunity (Bellman-Ford)
    → Encode SwapStep[] array
        → executeArbitrage(asset, amount, steps[])
            → Aave V3 flashLoanSimple()
                → executeOperation() callback
                    → For each step: swap on UniV3 / Curve / Balancer / Camelot
                    → Repay loan + premium
                    → Transfer profit to owner
```

Supported DEXes:
| ID | DEX | Type |
|----|-----|------|
| 0 | Uniswap V3 | Concentrated liquidity |
| 1 | Curve Finance | Stable swaps |
| 2 | Balancer V2 | Weighted pools |
| 3 | Camelot V3 | Arbitrum-native |

---

## Troubleshooting

### Solidity compiler issues

**Problem**: `py-solc-x not installed`
```bash
# Install manually:
pip install py-solc-x

# Or let compiler.py use Layers 2-4 automatically
python compiler.py --compile-only
```

**Problem**: ARM64 solc download fails
```bash
# Check internet connectivity:
curl -I https://github.com

# Manual download:
mkdir -p ~/.solc-arm
curl -L -o ~/.solc-arm/solc-0.8.20 \
  "https://github.com/ethereum/solc-bin/raw/gh-pages/linux-aarch64/solc-linux-aarch64-v0.8.20+commit.a1b79de6"
chmod +x ~/.solc-arm/solc-0.8.20
```

**Problem**: All compiler layers fail
```bash
# Layer 4 (embedded bytecode) always works — compiler.py uses verified bytecode
# baked into the file. If even --compile-only fails, check Python import:
python -c "from compiler import compile_contract, CONTRACTS; \
  r = compile_contract('FlashLoanArbitrage', CONTRACTS['FlashLoanArbitrage']); \
  print('ABI entries:', len(r['abi']), 'Bytecode bytes:', len(r['bytecode'])//2)"
```

### Flash loan issues

**Problem**: `FLASH_RECEIVER_ADDRESS not set`
```bash
# Deploy the contract first:
python compiler.py
# This sets FLASH_RECEIVER_ADDRESS in .env automatically
```

**Problem**: `Web3 connection failed`
```bash
# Verify RPC:
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('YOUR_RPC_URL')); print(w3.is_connected())"

# Common fix: ensure HTTPS (not WSS) URL for HTTPProvider
```

**Problem**: `insufficient funds for gas`
```bash
# Check balance:
python check_balance.py

# For Sepolia: get free ETH from https://sepoliafaucet.com
# For mainnet: ensure wallet has ≥ 0.05 ETH for gas
```

**Problem**: Flash loan TX reverts
```
# Common causes:
# 1. FLASH_RECEIVER_ADDRESS is an EOA, not a contract — deploy contract first
# 2. Contract not approved to repay — check approve() in executeOperation()
# 3. Insufficient liquidity in Aave pool for requested borrow amount
# 4. DRY_RUN=true still set — change to DRY_RUN=false for live TX
```

### Termux-specific issues

**Problem**: `pkg: command not found`
```bash
# Ensure you installed Termux from F-Droid, not Google Play
# Google Play version is outdated and broken
```

**Problem**: `Python 3.10+ required`
```bash
pkg update -y
pkg install -y python
python --version
```

**Problem**: `pip install fails with compiler errors`
```bash
# Install build dependencies:
pkg install -y clang make libffi openssl
pip install --upgrade pip setuptools wheel

# Retry installation:
pip install -r requirements-termux.txt
```

**Problem**: Termux sessions lost on screen lock
```bash
# Install Termux:API and disable battery optimization for Termux
# Or use tmux for persistent sessions:
pkg install -y tmux
tmux new -s aureon
python flashloan_terminal.py --auto
# Detach: Ctrl+B, then D
# Reattach: tmux attach -t aureon
```

---

## Security Checklist

Before going live on mainnet:

- [ ] Test everything on Sepolia first (≥ 1 hour of operation)
- [ ] `vault/wallet.json` permissions: `chmod 600 vault/wallet.json`
- [ ] `.env` not committed: `git check-ignore -v .env`
- [ ] `DRY_RUN=true` for initial mainnet testing
- [ ] `GAS_BUDGET_USD` set to cap gas costs
- [ ] `MAX_DAILY_TRADES` set to limit exposure
- [ ] Private key never shared or logged
- [ ] Profit wallet correctly configured
- [ ] Flash loan amount appropriate for available Aave liquidity

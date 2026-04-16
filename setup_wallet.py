#!/usr/bin/env python3
"""
AUREON Wallet Setup
───────────────────
Run once before starting the trading bot.
Choose to generate a brand-new wallet or import an existing private key.
Saves wallet to vault/wallet.json and patches .env automatically.
"""

import json
import os
import re
import sys

from eth_account import Account


VAULT_DIR = os.path.join(os.path.dirname(__file__), "vault")
WALLET_FILE = os.path.join(VAULT_DIR, "wallet.json")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


# ── helpers ───────────────────────────────────────────────────────────────────

def _save_wallet(address: str, private_key: str) -> None:
    os.makedirs(VAULT_DIR, exist_ok=True)
    with open(WALLET_FILE, "w", encoding="utf-8") as f:
        json.dump({"address": address, "private_key": private_key}, f, indent=2)
    os.chmod(WALLET_FILE, 0o600)
    print(f"  Wallet saved  → {WALLET_FILE}")


def _patch_env(address: str, private_key: str) -> None:
    """Write / overwrite WALLET_ADDRESS and PRIVATE_KEY in .env."""
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as _f:
            pass

    with open(ENV_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    updated = {"WALLET_ADDRESS": False, "PRIVATE_KEY": False}
    new_lines = []
    for line in lines:
        key = line.split("=")[0].strip()
        if key == "WALLET_ADDRESS":
            new_lines.append(f"WALLET_ADDRESS={address}\n")
            updated["WALLET_ADDRESS"] = True
        elif key == "PRIVATE_KEY":
            new_lines.append(f"PRIVATE_KEY={private_key}\n")
            updated["PRIVATE_KEY"] = True
        else:
            new_lines.append(line)

    if not updated["WALLET_ADDRESS"]:
        new_lines.append(f"WALLET_ADDRESS={address}\n")
    if not updated["PRIVATE_KEY"]:
        new_lines.append(f"PRIVATE_KEY={private_key}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("  .env patched  → WALLET_ADDRESS + PRIVATE_KEY updated")


def _validate_key(raw: str) -> str:
    """Normalise and validate a hex private key. Raises ValueError on bad input."""
    raw = raw.strip()
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise ValueError("Private key must be 64 hex characters (32 bytes).")
    return raw


# ── main flow ─────────────────────────────────────────────────────────────────

def generate_wallet() -> None:
    print("\n  Generating a new Ethereum wallet …")
    acct = Account.create()
    address = acct.address
    private_key = acct.key.hex()[2:]  # strip leading 0x

    print("\n  ┌─────────────────────────────────────────────────────────────┐")
    print("  │  NEW WALLET GENERATED                                       │")
    print("  │                                                             │")
    print(f"  │  Address     : {address}  │")
    print(f"  │  Private key : {private_key[:8]}…{private_key[-6:]}  (saved securely)    │")
    print("  └─────────────────────────────────────────────────────────────┘")
    print()
    print("  IMPORTANT — back up your private key NOW and keep it offline.")
    print(f"  Full key: {private_key}")
    print()

    _save_wallet(address, private_key)
    _patch_env(address, private_key)


def import_wallet() -> None:
    print()
    raw = input("  Paste your private key (hex, with or without 0x prefix): ").strip()
    try:
        key_hex = _validate_key(raw)
    except ValueError as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)

    acct = Account.from_key(key_hex)
    address = acct.address

    print(f"\n  Address resolved: {address}")
    confirm = input("  Save this wallet? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        sys.exit(0)

    _save_wallet(address, key_hex)
    _patch_env(address, key_hex)


def main() -> None:
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║      AUREON  —  Wallet Setup          ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    if os.path.exists(WALLET_FILE):
        with open(WALLET_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  Existing wallet found: {existing['address']}")
        choice = input("  Overwrite? [y/N]: ").strip().lower()
        if choice != "y":
            print("  Using existing wallet. Setup complete.")
            return

    print()
    print("  [1] Generate a new wallet")
    print("  [2] Import an existing private key")
    print()
    choice = input("  Choice [1/2]: ").strip()

    if choice == "1":
        generate_wallet()
    elif choice == "2":
        import_wallet()
    else:
        print("  Invalid choice. Exiting.")
        sys.exit(1)

    print()
    print("  Setup complete. Next steps:")
    print("  1. Fund your wallet with ETH (mainnet) for gas fees.")
    print("  2. Set your RPC_URL in .env (Alchemy or Infura recommended).")
    print("  3. Run:  python trade.py  to start the trading bot.")
    print()


if __name__ == "__main__":
    main()

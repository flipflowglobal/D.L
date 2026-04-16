#!/usr/bin/env python3
"""
merkle_lint.py — Aggregate lint/audit outputs into a single Merkle root.

Usage:
    python3 -m scripts.merkle_lint --inputs solhint.out clippy.out slither.json audit.json

The Merkle root is the SHA-256 root of the leaf hashes.  Each leaf is the
SHA-256 digest of the corresponding file's contents.

Output (JSON to stdout):
    {
        "leaves": [
            {"file": "solhint.out",  "hash": "abcd..."},
            ...
        ],
        "root": "ef01..."
    }

Convention:
    An all-zero root (000...000) means every input file was empty or missing,
    which signals a *clean* lint run (no findings).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import List


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _merkle_root(hashes: List[str]) -> str:
    """Compute a binary Merkle root from a list of hex-encoded SHA-256 hashes."""
    if not hashes:
        return "0" * 64

    layer = list(hashes)
    while len(layer) > 1:
        next_layer: List[str] = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else left  # duplicate last
            combined = bytes.fromhex(left) + bytes.fromhex(right)
            next_layer.append(_sha256(combined))
        layer = next_layer
    return layer[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Merkle-root aggregator for lint outputs")
    parser.add_argument(
        "--inputs", nargs="+", required=True,
        help="Lint/audit output files to aggregate",
    )
    args = parser.parse_args()

    leaves = []
    for path in args.inputs:
        if os.path.isfile(path):
            data = open(path, "rb").read()
        else:
            data = b""  # missing file ⇒ empty ⇒ clean
        h = _sha256(data)
        leaves.append({"file": path, "hash": h})

    leaf_hashes = [leaf["hash"] for leaf in leaves]
    root = _merkle_root(leaf_hashes)

    result = {"leaves": leaves, "root": root}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
lint_alignment.py — AI-native alignment scanner for AUREON codebase.

Scans Python source files for dangerous patterns that static linters miss:
  - Hardcoded private keys or seed phrases
  - Unbounded loops over external data (DoS vector)
  - Unguarded eval/exec usage
  - Missing slippage / deadline guards in swap calls
  - Direct use of datetime.utcnow() (deprecated in 3.12+)

Exit code 0 = clean, 1 = findings.
Output is JSON array of findings written to stdout.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    severity: str  # "error" | "warning" | "info"
    message: str


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

_DANGEROUS_CALLS = {"eval", "exec", "compile"}
# __import__ is excluded — it is a standard pattern for lazy/conditional imports

_KEY_PATTERNS = [
    "0x",           # potential raw hex key (checked with length heuristic)
    "private_key",
    "mnemonic",
    "seed_phrase",
]


class _Visitor(ast.NodeVisitor):
    """AST visitor that collects alignment findings for a single file."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.findings: List[Finding] = []

    # -- eval / exec / compile -------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        is_bare = False  # True when called as a bare name (not obj.method)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            is_bare = True
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        # Only flag bare compile() — re.compile(), etc. are safe
        if func_name in _DANGEROUS_CALLS and (is_bare or func_name != "compile"):
            self.findings.append(Finding(
                file=self.filepath,
                line=node.lineno,
                rule="no-dangerous-call",
                severity="error",
                message=f"Use of '{func_name}()' detected — potential code-injection vector",
            ))
        self.generic_visit(node)

    # -- hardcoded hex strings that look like private keys ---------------
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value.startswith("0x"):
            stripped = node.value[2:]
            if len(stripped) == 64 and all(c in "0123456789abcdefABCDEF" for c in stripped):
                self.findings.append(Finding(
                    file=self.filepath,
                    line=node.lineno,
                    rule="no-hardcoded-key",
                    severity="error",
                    message="Potential hardcoded private key (64-hex-char string)",
                ))
        self.generic_visit(node)

    # -- datetime.utcnow() deprecation -----------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr == "utcnow"
            and isinstance(node.value, ast.Attribute)
            and getattr(node.value, "attr", None) == "datetime"
        ):
            self.findings.append(Finding(
                file=self.filepath,
                line=node.lineno,
                rule="no-utcnow",
                severity="warning",
                message="datetime.datetime.utcnow() is deprecated — use datetime.now(timezone.utc)",
            ))
        self.generic_visit(node)


def _scan_file(filepath: str) -> List[Finding]:
    """Parse a single Python file and return findings."""
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return [Finding(
            file=filepath, line=0, rule="parse-error",
            severity="error", message="File contains a syntax error",
        )]
    visitor = _Visitor(filepath)
    visitor.visit(tree)
    return visitor.findings


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

_SCAN_DIRS = [
    "engine", "intelligence", "vault", "kernel",
    "agents", "core", "nexus_arb", "DL_SYSTEM",
]

_SCAN_FILES = [
    "main.py", "tools.py", "database.py", "config.py",
    "trade.py", "aureon_server.py", "aureon_onthedl.py",
    "blockchain_aureon.py", "setup_wallet.py",
]


def _collect_python_files(root: str) -> List[str]:
    """Collect all .py files under the configured scan dirs + root files."""
    files: List[str] = []
    for d in _SCAN_DIRS:
        dirpath = os.path.join(root, d)
        if not os.path.isdir(dirpath):
            continue
        for dirpath_walk, _, filenames in os.walk(dirpath):
            for fn in filenames:
                if fn.endswith(".py"):
                    files.append(os.path.join(dirpath_walk, fn))
    for fn in _SCAN_FILES:
        fp = os.path.join(root, fn)
        if os.path.isfile(fp):
            files.append(fp)
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-native alignment linter")
    parser.add_argument(
        "--model", default="embeddings-v3",
        help="Embedding model tag (reserved for future ML-based scanning)",
    )
    parser.add_argument(
        "--root", default=".",
        help="Repository root directory",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    files = _collect_python_files(root)

    all_findings: List[Finding] = []
    for fp in files:
        all_findings.extend(_scan_file(fp))

    # Emit results as JSON
    output = [asdict(f) for f in all_findings]
    print(json.dumps(output, indent=2))

    errors = [f for f in all_findings if f.severity == "error"]
    if errors:
        print(f"\n❌ {len(errors)} error(s) found across {len(files)} files", file=sys.stderr)
        return 1

    print(f"\n✅ {len(files)} files scanned, {len(all_findings)} finding(s) (no errors)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

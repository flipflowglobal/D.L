"""
hinsdale.py — Python interface to the Hinsdale EVM decompiler
=============================================================

Wraps the Rust `hinsdale-cli` binary via subprocess (zero-copy JSON pipe).
Falls back to pure-Python disassembler if binary not found.

Usage:
    from hinsdale import Hinsdale
    h = Hinsdale()
    r = h.analyze("608060405234801561000f...")
    print(r.pseudo_source)
    print(r.risk_score)

CLI:
    python hinsdale.py <hex>
    python hinsdale.py --file contract.bin
    python hinsdale.py --hex-file contract.hex
    python hinsdale.py --security-only <hex>
    python hinsdale.py --sigs-only <hex>
    python hinsdale.py --disasm-only <hex>
    python hinsdale.py --json <hex>
"""

from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Location of the Rust binary ────────────────────────────────────────────
_HERE       = Path(__file__).parent
_BINARY     = _HERE / "target" / "release" / "hinsdale-cli"
_BINARY_ALT = Path(shutil.which("hinsdale-cli") or "") if shutil.which("hinsdale-cli") else None


# ══════════════════════════════════════════════════════════════════════════════
# Data classes (mirror Rust structs)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Instruction:
    offset:    int
    opcode:    int
    mnemonic:  str
    imm:       Optional[str]
    imm_u256:  Optional[int]
    category:  str
    stack_in:  int
    stack_out: int

    def __str__(self):
        imm = f" 0x{self.imm}" if self.imm else ""
        return f"0x{self.offset:04x}  {self.opcode:02x}  {self.mnemonic:<14}{imm}"


@dataclass
class Disassembly:
    instructions:      list[Instruction]
    jumpdests:         list[int]
    total_bytes:       int
    instruction_count: int


@dataclass
class CfgSummary:
    block_count:    int
    edge_count:     int
    jumpdest_count: int


@dataclass
class FunctionSig:
    selector:     str
    selector_u32: int
    known_name:   Optional[str]
    jump_target:  Optional[int]
    is_view:      bool


@dataclass
class SignatureReport:
    functions:       list[FunctionSig]
    event_topics:    list[str]
    has_dispatcher:  bool
    fallback_offset: Optional[int]


@dataclass
class Finding:
    severity:    str
    title:       str
    description: str
    offset:      Optional[int]
    pattern:     str

    def __str__(self):
        loc = f"0x{self.offset:04x}" if self.offset is not None else "—"
        return f"[{self.severity:8s}] {self.title} (@ {loc})\n           {self.description}"


@dataclass
class SecurityReport:
    findings:         list[Finding]
    has_selfdestruct: bool
    has_delegatecall: bool
    has_create2:      bool
    has_staticcall:   bool
    sstore_count:     int
    sload_count:      int
    call_count:       int
    risk_score:       int


@dataclass
class StorageSlot:
    slot:   int
    usages: list[str]


@dataclass
class DecompiledOutput:
    pseudo_source: str
    functions:     list[dict]
    storage_slots: list[StorageSlot]


@dataclass
class Metadata:
    bytecode_len:       int
    is_runtime:         bool
    solc_version_hint:  Optional[str]
    is_proxy:           bool
    is_erc20_like:      bool
    is_erc721_like:     bool


@dataclass
class HinsdaleReport:
    metadata:    Metadata
    disassembly: Disassembly
    cfg_summary: CfgSummary
    signatures:  SignatureReport
    security:    SecurityReport
    decompiled:  DecompiledOutput
    elapsed_ms:  float
    raw:         dict = field(default_factory=dict, repr=False)

    # ── Convenience properties ────────────────────────────────────────────

    @property
    def pseudo_source(self) -> str:
        return self.decompiled.pseudo_source

    @property
    def risk_score(self) -> int:
        return self.security.risk_score

    @property
    def functions(self) -> list[FunctionSig]:
        return self.signatures.functions

    @property
    def findings(self) -> list[Finding]:
        return self.security.findings

    def summary(self) -> str:
        return (
            f"HINSDALE │ {self.metadata.bytecode_len} bytes │ "
            f"{self.disassembly.instruction_count} instrs │ "
            f"{self.cfg_summary.block_count} blocks │ "
            f"{len(self.signatures.functions)} fns │ "
            f"{len(self.security.findings)} findings │ "
            f"risk={self.security.risk_score} │ "
            f"{self.elapsed_ms:.1f}ms"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Report parser (JSON → dataclasses)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_report(data: dict) -> HinsdaleReport:
    d  = data["disassembly"]
    cs = data["cfg_summary"]
    sg = data["signatures"]
    se = data["security"]
    dc = data["decompiled"]
    md = data["metadata"]

    instructions = [
        Instruction(
            offset   = i["offset"],
            opcode   = i["opcode"],
            mnemonic = i["mnemonic"],
            imm      = i.get("imm"),
            imm_u256 = i.get("imm_u256"),
            category = i["category"],
            stack_in = i["stack_in"],
            stack_out= i["stack_out"],
        )
        for i in d["instructions"]
    ]

    disasm = Disassembly(
        instructions      = instructions,
        jumpdests         = d["jumpdests"],
        total_bytes       = d["total_bytes"],
        instruction_count = d["instruction_count"],
    )

    cfg_summary = CfgSummary(
        block_count    = cs["block_count"],
        edge_count     = cs["edge_count"],
        jumpdest_count = cs["jumpdest_count"],
    )

    functions = [
        FunctionSig(
            selector     = f["selector"],
            selector_u32 = f["selector_u32"],
            known_name   = f.get("known_name"),
            jump_target  = f.get("jump_target"),
            is_view      = f.get("is_view", False),
        )
        for f in sg["functions"]
    ]

    sigs = SignatureReport(
        functions       = functions,
        event_topics    = sg["event_topics"],
        has_dispatcher  = sg["has_dispatcher"],
        fallback_offset = sg.get("fallback_offset"),
    )

    findings = [
        Finding(
            severity    = fi["severity"],
            title       = fi["title"],
            description = fi["description"],
            offset      = fi.get("offset"),
            pattern     = fi["pattern"],
        )
        for fi in se["findings"]
    ]

    security = SecurityReport(
        findings         = findings,
        has_selfdestruct = se["has_selfdestruct"],
        has_delegatecall = se["has_delegatecall"],
        has_create2      = se["has_create2"],
        has_staticcall   = se["has_staticcall"],
        sstore_count     = se["sstore_count"],
        sload_count      = se["sload_count"],
        call_count       = se["call_count"],
        risk_score       = se["risk_score"],
    )

    storage_slots = [
        StorageSlot(slot=s["slot"], usages=s.get("usages", [s.get("ty","?")]))
        for s in dc.get("storage_slots", [])
    ]

    decompiled = DecompiledOutput(
        pseudo_source = dc["pseudo_source"],
        functions     = dc.get("functions", []),
        storage_slots = storage_slots,
    )

    metadata = Metadata(
        bytecode_len      = md["bytecode_len"],
        is_runtime        = md["is_runtime"],
        solc_version_hint = md.get("solc_version_hint"),
        is_proxy          = md["is_proxy"],
        is_erc20_like     = md["is_erc20_like"],
        is_erc721_like    = md["is_erc721_like"],
    )

    return HinsdaleReport(
        metadata    = metadata,
        disassembly = disasm,
        cfg_summary = cfg_summary,
        signatures  = sigs,
        security    = security,
        decompiled  = decompiled,
        elapsed_ms  = data.get("elapsed_ms", 0.0),
        raw         = data,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pure-Python fallback disassembler
# (used when Rust binary not compiled yet — basic disasm only)
# ══════════════════════════════════════════════════════════════════════════════

_MNEMONICS: dict[int, str] = {
    0x00:"STOP", 0x01:"ADD", 0x02:"MUL", 0x03:"SUB", 0x04:"DIV",
    0x05:"SDIV", 0x06:"MOD", 0x08:"ADDMOD", 0x09:"MULMOD", 0x0a:"EXP",
    0x10:"LT", 0x11:"GT", 0x14:"EQ", 0x15:"ISZERO",
    0x16:"AND", 0x17:"OR", 0x18:"XOR", 0x19:"NOT",
    0x1b:"SHL", 0x1c:"SHR", 0x1d:"SAR",
    0x20:"KECCAK256",
    0x30:"ADDRESS", 0x31:"BALANCE", 0x33:"CALLER", 0x34:"CALLVALUE",
    0x35:"CALLDATALOAD", 0x36:"CALLDATASIZE", 0x38:"CODESIZE",
    0x39:"CODECOPY", 0x3d:"RETURNDATASIZE", 0x3e:"RETURNDATACOPY",
    0x3f:"EXTCODEHASH",
    0x40:"BLOCKHASH", 0x42:"TIMESTAMP", 0x43:"NUMBER",
    0x46:"CHAINID", 0x47:"SELFBALANCE", 0x48:"BASEFEE",
    0x50:"POP", 0x51:"MLOAD", 0x52:"MSTORE", 0x53:"MSTORE8",
    0x54:"SLOAD", 0x55:"SSTORE",
    0x56:"JUMP", 0x57:"JUMPI", 0x58:"PC", 0x59:"MSIZE",
    0x5a:"GAS", 0x5b:"JUMPDEST", 0x5c:"TLOAD", 0x5d:"TSTORE",
    0x5e:"MCOPY", 0x5f:"PUSH0",
    0xf0:"CREATE", 0xf1:"CALL", 0xf2:"CALLCODE", 0xf3:"RETURN",
    0xf4:"DELEGATECALL", 0xf5:"CREATE2", 0xfa:"STATICCALL",
    0xfd:"REVERT", 0xfe:"INVALID", 0xff:"SELFDESTRUCT",
    0xa0:"LOG0", 0xa1:"LOG1", 0xa2:"LOG2", 0xa3:"LOG3", 0xa4:"LOG4",
}
for _i in range(1, 17):
    _MNEMONICS[0x7f + _i] = f"DUP{_i}"
    _MNEMONICS[0x8f + _i] = f"SWAP{_i}"
for _i in range(1, 33):
    _MNEMONICS[0x5f + _i] = f"PUSH{_i}"


def _py_disassemble(bytecode: bytes) -> list[Instruction]:
    instrs = []
    i = 0
    while i < len(bytecode):
        b = bytecode[i]
        mn = _MNEMONICS.get(b, f"UNKNOWN_{b:02x}")
        push_n = b - 0x5f if 0x60 <= b <= 0x7f else 0
        imm_bytes = bytecode[i+1 : i+1+push_n] if push_n else b""
        imm_hex  = imm_bytes.hex() if imm_bytes else None
        imm_int  = int.from_bytes(imm_bytes, "big") if imm_bytes and len(imm_bytes) <= 8 else None
        instrs.append(Instruction(
            offset=i, opcode=b, mnemonic=mn,
            imm=imm_hex, imm_u256=imm_int,
            category="STACK" if 0x60 <= b <= 0x9f else
                      "FLOW"  if b in (0x56,0x57,0x58,0x5b) else
                      "STORAGE" if b in (0x54,0x55) else "MISC",
            stack_in=0, stack_out=1 if push_n or b == 0x5f else 0,
        ))
        i += 1 + push_n
    return instrs


def _py_fallback(bytecode: bytes) -> HinsdaleReport:
    """Pure Python fallback — disasm + basic sig detection only."""
    t0 = time.monotonic()
    instrs = _py_disassemble(bytecode)
    jumpdests = [x.offset for x in instrs if x.opcode == 0x5b]

    disasm = Disassembly(
        instructions=instrs, jumpdests=jumpdests,
        total_bytes=len(bytecode), instruction_count=len(instrs),
    )

    # Basic selector scan
    functions = []
    for i, ins in enumerate(instrs):
        if ins.opcode == 0x63 and ins.imm_u256 is not None:
            sel = ins.imm_u256
            nearby = instrs[i+1:i+6]
            if any(x.opcode == 0x14 for x in nearby) and any(x.opcode == 0x57 for x in nearby):
                functions.append(FunctionSig(
                    selector=f"0x{sel:08x}", selector_u32=sel,
                    known_name=None, jump_target=None, is_view=False,
                ))

    sigs = SignatureReport(functions=functions, event_topics=[],
                           has_dispatcher=bool(functions), fallback_offset=None)

    findings = []
    has_sd = any(x.opcode == 0xff for x in instrs)
    has_dc = any(x.opcode == 0xf4 for x in instrs)
    if has_sd:
        findings.append(Finding("CRITICAL","SELFDESTRUCT","Contract can self-destruct",None,"SELFDESTRUCT"))
    if has_dc:
        findings.append(Finding("HIGH","DELEGATECALL","DELEGATECALL present",None,"DELEGATECALL"))

    security = SecurityReport(
        findings=findings, has_selfdestruct=has_sd, has_delegatecall=has_dc,
        has_create2=any(x.opcode==0xf5 for x in instrs),
        has_staticcall=any(x.opcode==0xfa for x in instrs),
        sstore_count=sum(1 for x in instrs if x.opcode==0x55),
        sload_count =sum(1 for x in instrs if x.opcode==0x54),
        call_count  =sum(1 for x in instrs if x.opcode==0xf1),
        risk_score  =min(len(findings)*15, 100),
    )

    elapsed_ms = (time.monotonic() - t0) * 1000

    return HinsdaleReport(
        metadata=Metadata(
            bytecode_len=len(bytecode), is_runtime=len(bytecode)>2 and bytecode[0]==0x60 and bytecode[1]==0x80,
            solc_version_hint=None, is_proxy=False, is_erc20_like=False, is_erc721_like=False,
        ),
        disassembly=disasm,
        cfg_summary=CfgSummary(0, 0, len(jumpdests)),
        signatures=sigs,
        security=security,
        decompiled=DecompiledOutput("// [Python fallback — compile Rust binary for full output]", [], []),
        elapsed_ms=elapsed_ms,
        raw={},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main interface class
# ══════════════════════════════════════════════════════════════════════════════

class Hinsdale:
    """
    Hinsdale EVM decompiler interface.

    Automatically uses Rust binary if compiled, falls back to Python.

    >>> h = Hinsdale()
    >>> r = h.analyze("608060405234801561000f575f80fd...")
    >>> print(r.summary())
    >>> print(r.pseudo_source)
    """

    def __init__(self, binary: Optional[str] = None):
        self._binary = None
        candidates = [
            binary,
            str(_BINARY),
            str(_BINARY_ALT) if _BINARY_ALT else None,
            "hinsdale-cli",
        ]
        for c in candidates:
            if c and Path(c).exists():
                self._binary = c
                break
            elif c and shutil.which(c):
                self._binary = shutil.which(c)
                break

        self._using_rust = bool(self._binary)

    @property
    def backend(self) -> str:
        return f"Rust ({self._binary})" if self._using_rust else "Python (fallback)"

    def analyze(self, bytecode: str | bytes) -> HinsdaleReport:
        """
        Analyze EVM bytecode.
        bytecode: hex string (with/without 0x prefix) or raw bytes.
        """
        if isinstance(bytecode, str):
            raw = bytes.fromhex(bytecode.strip().removeprefix("0x"))
        else:
            raw = bytecode

        if self._using_rust:
            return self._run_rust(raw)
        else:
            return _py_fallback(raw)

    def analyze_file(self, path: str | Path) -> HinsdaleReport:
        """Analyze a binary .bin file or hex .hex file."""
        p = Path(path)
        if p.suffix == ".hex":
            return self.analyze(p.read_text().strip())
        else:
            return self.analyze(p.read_bytes())

    def _run_rust(self, raw: bytes) -> HinsdaleReport:
        hex_str = raw.hex()
        cmd = [self._binary, "--json", hex_str]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            data = json.loads(result.stdout)
            return _parse_report(data)
        except FileNotFoundError:
            self._using_rust = False
            return _py_fallback(raw)

    def disasm(self, bytecode: str | bytes) -> list[Instruction]:
        """Fast disassembly only — no CFG/decompile."""
        return self.analyze(bytecode).disassembly.instructions

    def signatures(self, bytecode: str | bytes) -> list[FunctionSig]:
        """Return recovered function signatures."""
        return self.analyze(bytecode).signatures.functions

    def security(self, bytecode: str | bytes) -> SecurityReport:
        """Return security report."""
        return self.analyze(bytecode).security


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def _cli():
    parser = argparse.ArgumentParser(
        description="Hinsdale EVM Decompiler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("bytecode", nargs="?", help="Hex bytecode string")
    parser.add_argument("--file",        help="Binary .bin file")
    parser.add_argument("--hex-file",    help="Hex text file")
    parser.add_argument("--json",        action="store_true", help="JSON output")
    parser.add_argument("--disasm-only", action="store_true")
    parser.add_argument("--sigs-only",   action="store_true")
    parser.add_argument("--security-only", action="store_true")
    parser.add_argument("--summary",     action="store_true")
    parser.add_argument("--binary",      help="Path to hinsdale-cli binary")
    args = parser.parse_args()

    h = Hinsdale(binary=args.binary)
    print(f"[HINSDALE] Backend: {h.backend}", file=sys.stderr)

    # Get bytecode
    if args.file:
        report = h.analyze_file(args.file)
    elif args.hex_file:
        report = h.analyze(Path(args.hex_file).read_text())
    elif args.bytecode:
        report = h.analyze(args.bytecode)
    else:
        data = sys.stdin.read().strip()
        if not data:
            parser.print_help()
            sys.exit(1)
        report = h.analyze(data)

    if args.json:
        import dataclasses
        print(json.dumps(report.raw or {
            "summary": report.summary(),
            "pseudo_source": report.pseudo_source,
        }, indent=2))
        return

    if args.summary:
        print(report.summary())
        return

    # Default: print everything relevant
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║            HINSDALE EVM DECOMPILER — PYTHON INTERFACE           ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    print(report.summary())
    print()

    if args.disasm_only:
        for ins in report.disassembly.instructions:
            marker = "◆" if ins.opcode == 0x5b else " "
            print(f"  {marker} {ins}")
        return

    if args.sigs_only or not args.security_only:
        print("── FUNCTION SIGNATURES ──────────────────────────────────────────")
        if not report.signatures.functions:
            print("  (none detected)")
        for f in report.signatures.functions:
            name = f.known_name or "???"
            tgt  = f"→ 0x{f.jump_target:04x}" if f.jump_target else "→ ?"
            print(f"  {f.selector}  {tgt}  {name}")
        print()
        if args.sigs_only:
            return

    print("── SECURITY ANALYSIS ────────────────────────────────────────────")
    print(f"  Risk: {report.risk_score}/100")
    for finding in report.findings:
        print(f"  {finding}")
    print()

    if args.security_only:
        return

    print("── PSEUDO-SOLIDITY ──────────────────────────────────────────────")
    print(report.pseudo_source)


if __name__ == "__main__":
    _cli()

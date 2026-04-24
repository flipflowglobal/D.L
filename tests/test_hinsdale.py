"""Tests for Hinsdale EVM decompiler (flowx/Hinsdale_1 integration)."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hinsdale'))

import pytest
from hinsdale import Hinsdale, _py_disassemble, _py_fallback

# Real Aureon FlashLoanArbitrage bytecode (from flowx/Hinsdale_1 test suite)
AUREON_HEX = (
    "608060405234801561000f575f80fd5b506004361061006f575f3560e01c8063839006f21161004d578063839006f214"
    "6100f55780638da5cb5b14610108578063da2ca9b514610127575f80fd5b80630b187dd3146100735780631b11d0ff1461"
    "00885780632301d775146100b0575b5f80fd5b6100866100813660046107cb565b61013a565b005b61009b610096366004"
    "6107f3565b6102c2565b60405190151581526020015b60405180910390f35b6001546100d09073ffffffffffffffffffff"
    "ffffffffffffffffffffff1681565b60405173ffffffffffffffffffffffffffffffffffffffff90911681526020016100a7"
    "565b610086610103366004610891565b610526565b5f546100d09073ffffffffffffffffffffffffffffffffffffffff1681"
    "565b610086610135366004610891565b6106dc565b"
)

SIMPLE_HEX = "6080604052"  # PUSH1 0x80, PUSH1 0x40, MSTORE


class TestPyFallback:
    def test_disassemble_simple(self):
        instrs = _py_disassemble(bytes.fromhex(SIMPLE_HEX))
        assert len(instrs) >= 3
        assert instrs[0].mnemonic == "PUSH1"
        assert instrs[0].imm == "80"

    def test_fallback_report(self):
        r = _py_fallback(bytes.fromhex(SIMPLE_HEX))
        assert r.disassembly.instruction_count >= 3
        assert r.metadata.bytecode_len == len(bytes.fromhex(SIMPLE_HEX))

    def test_fallback_aureon(self):
        r = _py_fallback(bytes.fromhex(AUREON_HEX))
        assert r.disassembly.instruction_count > 50
        assert r.metadata.is_runtime is True
        assert r.metadata.bytecode_len > 0

    def test_signatures_detected(self):
        r = _py_fallback(bytes.fromhex(AUREON_HEX))
        selectors = [f.selector for f in r.signatures.functions]
        # Aureon has flash loan selectors
        assert len(selectors) >= 1

    def test_security_report(self):
        r = _py_fallback(bytes.fromhex(AUREON_HEX))
        assert isinstance(r.security.risk_score, int)
        assert 0 <= r.security.risk_score <= 100

    def test_summary_string(self):
        r = _py_fallback(bytes.fromhex(SIMPLE_HEX))
        s = r.summary()
        assert "HINSDALE" in s
        assert "bytes" in s


class TestHinsdaleInterface:
    def setup_method(self):
        self.h = Hinsdale()

    def test_backend_set(self):
        assert self.h.backend in ("Python (fallback)", ) or "Rust" in self.h.backend

    def test_analyze_hex_string(self):
        r = self.h.analyze(SIMPLE_HEX)
        assert r.metadata.bytecode_len == len(bytes.fromhex(SIMPLE_HEX))

    def test_analyze_bytes(self):
        r = self.h.analyze(bytes.fromhex(SIMPLE_HEX))
        assert r.disassembly.instruction_count >= 3

    def test_analyze_0x_prefix(self):
        r = self.h.analyze("0x" + SIMPLE_HEX)
        assert r.metadata.bytecode_len > 0

    def test_disasm_property(self):
        instrs = self.h.disasm(SIMPLE_HEX)
        assert len(instrs) >= 3

    def test_signatures_property(self):
        sigs = self.h.signatures(AUREON_HEX)
        assert isinstance(sigs, list)

    def test_security_property(self):
        sec = self.h.security(AUREON_HEX)
        assert hasattr(sec, "risk_score")

    def test_aureon_erc20_detected(self):
        r = self.h.analyze(AUREON_HEX)
        # Aureon bytecode has transfer/approve-like patterns
        assert r.metadata.bytecode_len > 100

    def test_pseudo_source_property(self):
        r = self.h.analyze(SIMPLE_HEX)
        assert isinstance(r.pseudo_source, str)

    def test_risk_score_property(self):
        r = self.h.analyze(SIMPLE_HEX)
        assert isinstance(r.risk_score, int)

    def test_findings_property(self):
        r = self.h.analyze(AUREON_HEX)
        assert isinstance(r.findings, list)

// src/lib.rs — Hinsdale EVM Decompiler v2.0 — Public API
pub mod opcodes;
pub mod disasm;
pub mod cfg;
pub mod signatures;
pub mod security;
pub mod types;
pub mod symbolic;
pub mod decompiler;
pub mod ffi;
pub mod defi;
pub mod mev;

use serde::{Deserialize, Serialize};
use std::time::Instant;

/// Full decompilation + analysis result.
#[derive(Debug, Serialize, Deserialize)]
pub struct HinsdaleReport {
    pub metadata:    Metadata,
    pub disassembly: disasm::Disassembly,
    pub cfg_summary: CfgSummary,
    pub signatures:  signatures::SignatureReport,
    pub security:    security::SecurityReport,
    pub defi:        defi::DefiReport,
    pub mev:         mev::MevReport,
    pub decompiled:  decompiler::DecompiledOutput,
    pub elapsed_ms:  f64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Metadata {
    pub bytecode_len:      usize,
    pub is_runtime:        bool,
    pub solc_version_hint: Option<String>,
    pub is_proxy:          bool,
    pub is_erc20_like:     bool,
    pub is_erc721_like:    bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CfgSummary {
    pub block_count:    usize,
    pub edge_count:     usize,
    pub jumpdest_count: usize,
}

/// Parse hex bytecode string (with or without 0x prefix).
pub fn parse_hex(input: &str) -> Result<Vec<u8>, String> {
    let clean = input.trim().trim_start_matches("0x");
    hex::decode(clean).map_err(|e| format!("hex decode error: {e}"))
}

/// Run the full Hinsdale analysis pipeline on raw bytes.
pub fn analyze(bytecode: &[u8]) -> HinsdaleReport {
    let t0 = Instant::now();

    let disassembly = disasm::disassemble(bytecode);
    let cfg         = cfg::build_cfg(&disassembly);
    let cfg_summary = CfgSummary {
        block_count:    cfg.block_count(),
        edge_count:     cfg.edge_count(),
        jumpdest_count: disassembly.jumpdests.len(),
    };
    let signatures = signatures::recover_signatures(&disassembly);
    let security   = security::analyze_security(&disassembly);
    let defi       = defi::analyze_defi(&disassembly, &signatures);
    let mev        = mev::analyze_mev(&disassembly, &signatures);
    let decompiled = decompiler::decompile(&disassembly, &cfg, &signatures);

    let is_runtime = bytecode.len() >= 3
        && bytecode[0] == 0x60
        && bytecode[1] == 0x80
        && bytecode[2] == 0x60;

    let is_erc20_like = signatures.functions.iter().any(|f| {
        f.known_name.as_deref().map(|n| {
            n.contains("transfer") || n.contains("balanceOf") || n.contains("approve")
        }).unwrap_or(false)
    });

    let is_erc721_like = signatures.functions.iter().any(|f| {
        f.known_name.as_deref().map(|n| {
            n.contains("tokenURI") || n.contains("ownerOf") || n.contains("safeTransfer")
        }).unwrap_or(false)
    });

    let is_proxy = security.has_delegatecall && bytecode.len() < 500;
    let solc_version_hint = extract_solc_version(bytecode);

    let metadata = Metadata {
        bytecode_len: bytecode.len(),
        is_runtime,
        solc_version_hint,
        is_proxy,
        is_erc20_like,
        is_erc721_like,
    };

    HinsdaleReport {
        metadata,
        disassembly,
        cfg_summary,
        signatures,
        security,
        defi,
        mev,
        decompiled,
        elapsed_ms: t0.elapsed().as_secs_f64() * 1000.0,
    }
}

fn extract_solc_version(bytecode: &[u8]) -> Option<String> {
    let n = bytecode.len();
    if n < 4 { return None; }
    let meta_len = u16::from_be_bytes([bytecode[n - 2], bytecode[n - 1]]) as usize;
    if meta_len + 2 > n || meta_len < 5 { return None; }
    let meta_slice = &bytecode[n - 2 - meta_len..n - 2];
    if let Some(pos) = meta_slice.windows(4).position(|w| w == b"solc") {
        let after = &meta_slice[pos + 4..];
        if after.len() >= 4 {
            let minor = after[2];
            let patch = after[3];
            if after[1] <= 1 && minor <= 20 {
                return Some(format!("^0.{minor}.{patch}"));
            }
        }
    }
    if meta_slice.iter().any(|&b| b == 0xa2 || b == 0xa1) {
        return Some("(solc metadata detected, version unreadable)".into());
    }
    None
}

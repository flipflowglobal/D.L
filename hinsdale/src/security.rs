// src/security.rs — Static security analysis for EVM bytecode
use crate::disasm::Disassembly;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum Severity {
    Critical,
    High,
    Medium,
    Low,
    Info,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Critical => "CRITICAL",
            Self::High     => "HIGH",
            Self::Medium   => "MEDIUM",
            Self::Low      => "LOW",
            Self::Info     => "INFO",
        };
        write!(f, "{s}")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecurityFinding {
    pub id:          String,
    pub title:       String,
    pub severity:    Severity,
    pub offset:      usize,
    pub description: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SecurityReport {
    pub findings:          Vec<SecurityFinding>,
    pub has_selfdestruct:  bool,
    pub has_delegatecall:  bool,
    pub has_create2:       bool,
    pub has_tx_origin:     bool,
    pub has_timestamp_dep: bool,
    pub has_reenter_risk:  bool,
    pub has_unchecked_ret: bool,
    pub risk_score:        u32,
}

pub fn analyze_security(disasm: &Disassembly) -> SecurityReport {
    let instrs = &disasm.instructions;
    let n = instrs.len();
    let mut findings: Vec<SecurityFinding> = Vec::new();

    let mut has_selfdestruct  = false;
    let mut has_delegatecall  = false;
    let mut has_create2       = false;
    let mut has_tx_origin     = false;
    let mut has_timestamp_dep = false;
    let mut has_reenter_risk  = false;
    let mut has_unchecked_ret = false;

    for (i, ins) in instrs.iter().enumerate() {
        match ins.opcode {
            // SELFDESTRUCT
            0xff => {
                has_selfdestruct = true;
                findings.push(SecurityFinding {
                    id: "SWC-106".into(),
                    title: "SELFDESTRUCT present".into(),
                    severity: Severity::Critical,
                    offset: ins.offset,
                    description: format!(
                        "SELFDESTRUCT at 0x{:x}. Contract can be destroyed, \
                         permanently removing code and funds.", ins.offset),
                });
            }
            // DELEGATECALL
            0xf4 => {
                has_delegatecall = true;
                findings.push(SecurityFinding {
                    id: "SWC-112".into(),
                    title: "DELEGATECALL to potentially untrusted callee".into(),
                    severity: Severity::High,
                    offset: ins.offset,
                    description: format!(
                        "DELEGATECALL at 0x{:x}. Delegated code executes in the caller's \
                         storage context — storage layout mismatch or malicious target \
                         can corrupt state.", ins.offset),
                });
            }
            // CREATE2
            0xf5 => {
                has_create2 = true;
                findings.push(SecurityFinding {
                    id: "SWC-CREATE2".into(),
                    title: "CREATE2 deterministic deployment".into(),
                    severity: Severity::Medium,
                    offset: ins.offset,
                    description: format!(
                        "CREATE2 at 0x{:x}. Deterministic addresses enable metamorphic \
                         contract patterns; verify the deployed init code cannot be \
                         swapped.", ins.offset),
                });
            }
            // tx.origin (ORIGIN)
            0x32 => {
                has_tx_origin = true;
                findings.push(SecurityFinding {
                    id: "SWC-115".into(),
                    title: "Use of tx.origin for authorization".into(),
                    severity: Severity::High,
                    offset: ins.offset,
                    description: format!(
                        "ORIGIN opcode at 0x{:x}. Using tx.origin for auth is vulnerable \
                         to phishing attacks; use msg.sender instead.", ins.offset),
                });
            }
            // TIMESTAMP
            0x42 => {
                // Only flag if TIMESTAMP is subsequently used in a comparison (GT/LT/EQ)
                let used_in_cmp = instrs[i + 1..std::cmp::min(i + 8, n)]
                    .iter()
                    .any(|x| matches!(x.opcode, 0x10 | 0x11 | 0x12 | 0x13 | 0x14));
                if used_in_cmp {
                    has_timestamp_dep = true;
                    findings.push(SecurityFinding {
                        id: "SWC-116".into(),
                        title: "Timestamp dependence".into(),
                        severity: Severity::Low,
                        offset: ins.offset,
                        description: format!(
                            "TIMESTAMP at 0x{:x} followed by comparison. Block timestamp \
                             can be manipulated by miners within ~15 s; avoid for \
                             critical timing logic.", ins.offset),
                    });
                }
            }
            // SSTORE after CALL — classic reentrancy pattern
            0xf1 | 0xf2 | 0xfa => {
                // Look ahead for SSTORE within next 20 instructions
                let has_sstore_after = instrs[i + 1..std::cmp::min(i + 20, n)]
                    .iter()
                    .any(|x| x.opcode == 0x55);
                if has_sstore_after {
                    has_reenter_risk = true;
                    let op_name = match ins.opcode {
                        0xf1 => "CALL",
                        0xf2 => "CALLCODE",
                        _    => "STATICCALL",
                    };
                    findings.push(SecurityFinding {
                        id: "SWC-107".into(),
                        title: "Potential reentrancy (state write after external call)".into(),
                        severity: Severity::High,
                        offset: ins.offset,
                        description: format!(
                            "{op_name} at 0x{:x} is followed by SSTORE. Violates \
                             Checks-Effects-Interactions; consider reentrancy guard.", ins.offset),
                    });
                }
            }
            // Unchecked return value: CALL/STATICCALL immediately followed by POP
            _ => {}
        }

        // Check for unchecked CALL return value: CALL (0xf1) or STATICCALL (0xfa)
        // return value sits on stack; if the very next meaningful op is POP the return is discarded
        if matches!(ins.opcode, 0xf1 | 0xfa | 0xf4) && i + 1 < n {
            if instrs[i + 1].opcode == 0x50 {
                has_unchecked_ret = true;
                findings.push(SecurityFinding {
                    id: "SWC-104".into(),
                    title: "Unchecked call return value".into(),
                    severity: Severity::Medium,
                    offset: ins.offset,
                    description: format!(
                        "Call at 0x{:x} return value immediately discarded (POP). \
                         Failed external calls will be silently ignored.", ins.offset),
                });
            }
        }
    }

    // Callcode is always deprecated
    for ins in instrs {
        if ins.opcode == 0xf2 {
            findings.push(SecurityFinding {
                id: "SWC-111".into(),
                title: "Use of deprecated CALLCODE".into(),
                severity: Severity::Medium,
                offset: ins.offset,
                description: format!(
                    "CALLCODE at 0x{:x} is deprecated in favour of DELEGATECALL; \
                     behaviour differs in how msg.sender/value are set.", ins.offset),
            });
        }
    }

    // Deduplicate by offset + id — must sort first since dedup_by only removes adjacent duplicates
    findings.sort_by(|a, b| a.id.cmp(&b.id).then(a.offset.cmp(&b.offset)));
    findings.dedup_by(|a, b| a.id == b.id && a.offset == b.offset);

    let risk_score = findings.iter().map(|f| match f.severity {
        Severity::Critical => 40,
        Severity::High     => 20,
        Severity::Medium   => 10,
        Severity::Low      =>  5,
        Severity::Info     =>  1,
    }).sum::<u32>().min(100);

    SecurityReport {
        findings,
        has_selfdestruct,
        has_delegatecall,
        has_create2,
        has_tx_origin,
        has_timestamp_dep,
        has_reenter_risk,
        has_unchecked_ret,
        risk_score,
    }
}

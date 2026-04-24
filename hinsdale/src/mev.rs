// src/mev.rs — MEV pattern detector
use crate::disasm::Disassembly;
use crate::signatures::SignatureReport;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct MevReport {
    pub sandwich_risk:    bool,
    pub frontrun_risk:    bool,
    pub arbitrage_hint:   bool,
    pub mev_patterns:     Vec<String>,
}

pub fn analyze_mev(disasm: &Disassembly, sigs: &SignatureReport) -> MevReport {
    let instrs = &disasm.instructions;
    let mut patterns: Vec<String> = Vec::new();

    // Timestamp dependency → frontrun risk
    let has_timestamp = instrs.iter().any(|i| i.opcode == 0x42);
    let frontrun_risk = has_timestamp;
    if frontrun_risk { patterns.push("TimestampDependency".into()); }

    // Multiple CALL + SLOAD patterns → potential sandwich
    let call_count = instrs.iter().filter(|i| i.opcode == 0xf1).count();
    let sandwich_risk = call_count >= 2;
    if sandwich_risk { patterns.push("MultipleCalls".into()); }

    // flashLoan + arbitrage selectors
    let arbitrage_hint = sigs.functions.iter().any(|f| {
        f.selector == "0x839006f2" || f.selector == "0x0b187dd3" || f.selector == "0x42b0b77c"
    });
    if arbitrage_hint { patterns.push("FlashArbitrage".into()); }

    // BLOCKHASH usage — historical randomness manipulation
    if instrs.iter().any(|i| i.opcode == 0x40) {
        patterns.push("BlockhashRandomness".into());
    }

    MevReport {
        sandwich_risk,
        frontrun_risk,
        arbitrage_hint,
        mev_patterns: patterns,
    }
}

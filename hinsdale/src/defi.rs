// src/defi.rs — DeFi pattern detector
use crate::disasm::Disassembly;
use crate::signatures::SignatureReport;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct DefiReport {
    pub is_flash_loan_receiver: bool,
    pub is_aave_pool:           bool,
    pub is_uniswap_v3:          bool,
    pub is_erc20:               bool,
    pub is_erc721:              bool,
    pub defi_patterns:          Vec<String>,
}

pub fn analyze_defi(_disasm: &Disassembly, sigs: &SignatureReport) -> DefiReport {
    let mut patterns: Vec<String> = Vec::new();

    let is_flash_loan_receiver = sigs.functions.iter().any(|f| {
        f.selector == "0x1b11d0ff" || f.selector == "0x920f5c84"
    });
    if is_flash_loan_receiver { patterns.push("FlashLoanReceiver".into()); }

    let is_aave_pool = sigs.functions.iter().any(|f| {
        matches!(f.selector.as_str(), "0xe8eda9df"|"0x69328dec"|"0x617ba037"|"0x573ade81")
    });
    if is_aave_pool { patterns.push("AavePool".into()); }

    let is_uniswap_v3 = sigs.functions.iter().any(|f| {
        matches!(f.selector.as_str(), "0x128acb08"|"0x3850c7bd"|"0x514ea4bf")
    });
    if is_uniswap_v3 { patterns.push("UniswapV3".into()); }

    let is_erc20 = sigs.functions.iter().any(|f| {
        matches!(f.selector.as_str(), "0xa9059cbb"|"0x095ea7b3"|"0x70a08231"|"0x23b872dd")
    });
    if is_erc20 { patterns.push("ERC20".into()); }

    let is_erc721 = sigs.functions.iter().any(|f| {
        matches!(f.selector.as_str(), "0xc87b56dd"|"0xb88d4fde"|"0x6352211e")
    });
    if is_erc721 { patterns.push("ERC721".into()); }

    DefiReport {
        is_flash_loan_receiver,
        is_aave_pool,
        is_uniswap_v3,
        is_erc20,
        is_erc721,
        defi_patterns: patterns,
    }
}

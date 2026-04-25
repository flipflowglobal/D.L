// src/signatures.rs — Function selector recovery with 4byte DB lookup
use crate::disasm::Disassembly;
use rustc_hash::FxHashMap;
use serde::{Deserialize, Serialize};

/// A recovered function selector + optional known name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionSignature {
    /// Hex selector, e.g. "0xa9059cbb"
    pub selector:    String,
    /// Offset in bytecode where the selector comparison was detected
    pub offset:      usize,
    /// Known human-readable name from the embedded 4byte DB
    pub known_name:  Option<String>,
    /// Reconstructed Solidity-style signature if known
    pub signature:   Option<String>,
}

/// Full signature recovery report.
#[derive(Debug, Serialize, Deserialize)]
pub struct SignatureReport {
    pub functions:       Vec<FunctionSignature>,
    pub selector_count:  usize,
    pub resolved_count:  usize,
}

/// Minimal embedded 4byte selector database — covers the most common DeFi / ERC selectors.
fn builtin_4byte_db() -> FxHashMap<u32, (&'static str, &'static str)> {
    let mut m: FxHashMap<u32, (&'static str, &'static str)> = FxHashMap::default();
    // ERC-20
    m.insert(0xa9059cbb, ("transfer",          "transfer(address,uint256)"));
    m.insert(0x23b872dd, ("transferFrom",       "transferFrom(address,address,uint256)"));
    m.insert(0x095ea7b3, ("approve",            "approve(address,uint256)"));
    m.insert(0x70a08231, ("balanceOf",          "balanceOf(address)"));
    m.insert(0x18160ddd, ("totalSupply",        "totalSupply()"));
    m.insert(0xdd62ed3e, ("allowance",          "allowance(address,address)"));
    m.insert(0x06fdde03, ("name",               "name()"));
    m.insert(0x95d89b41, ("symbol",             "symbol()"));
    m.insert(0x313ce567, ("decimals",           "decimals()"));
    // ERC-721
    m.insert(0x6352211e, ("ownerOf",            "ownerOf(uint256)"));
    m.insert(0xb88d4fde, ("safeTransferFrom",   "safeTransferFrom(address,address,uint256,bytes)"));
    m.insert(0x42842e0e, ("safeTransferFrom",   "safeTransferFrom(address,address,uint256)"));
    m.insert(0xc87b56dd, ("tokenURI",           "tokenURI(uint256)"));
    m.insert(0x081812fc, ("getApproved",        "getApproved(uint256)"));
    m.insert(0xe985e9c5, ("isApprovedForAll",   "isApprovedForAll(address,address)"));
    m.insert(0xa22cb465, ("setApprovalForAll",  "setApprovalForAll(address,bool)"));
    // ERC-165
    m.insert(0x01ffc9a7, ("supportsInterface",  "supportsInterface(bytes4)"));
    // Ownable
    m.insert(0x8da5cb5b, ("owner",              "owner()"));
    m.insert(0xf2fde38b, ("transferOwnership",  "transferOwnership(address)"));
    m.insert(0x715018a6, ("renounceOwnership",  "renounceOwnership()"));
    // Pausable
    m.insert(0x8456cb59, ("pause",              "pause()"));
    m.insert(0x3f4ba83a, ("unpause",            "unpause()"));
    m.insert(0x5c975abb, ("paused",             "paused()"));
    // Uniswap V2
    m.insert(0x022c0d9f, ("swap",               "swap(uint256,uint256,address,bytes)"));
    m.insert(0x0902f1ac, ("getReserves",        "getReserves()"));
    m.insert(0xe8e33700, ("addLiquidity",        "addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)"));
    m.insert(0x38ed1739, ("swapExactTokensForTokens","swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"));
    m.insert(0x7ff36ab5, ("swapExactETHForTokens","swapExactETHForTokens(uint256,address[],address,uint256)"));
    // Uniswap V3
    m.insert(0x128acb08, ("swap",               "swap(address,bool,int256,uint160,bytes)"));
    m.insert(0x3850c7bd, ("slot0",              "slot0()"));
    m.insert(0x514ea4bf, ("ticks",              "ticks(int24)"));
    m.insert(0x04e45aaf, ("exactInputSingle",   "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"));
    // Aave V2/V3
    m.insert(0xe8eda9df, ("deposit",            "deposit(address,uint256,address,uint16)"));
    m.insert(0x69328dec, ("withdraw",           "withdraw(address,uint256,address)"));
    m.insert(0x617ba037, ("supply",             "supply(address,uint256,address,uint16)"));
    m.insert(0x573ade81, ("repay",              "repay(address,uint256,uint256,address)"));
    m.insert(0xab9c4b5d, ("flashLoan",          "flashLoan(address,address[],uint256[],uint256[],address,bytes,uint16)"));
    m.insert(0x42b0b77c, ("flashLoanSimple",    "flashLoanSimple(address,address,uint256,bytes,uint16)"));
    m.insert(0x1b11d0ff, ("executeOperation",   "executeOperation(address,uint256,uint256,address,bytes)"));
    m.insert(0x920f5c84, ("executeOperation",   "executeOperation(address[],uint256[],uint256[],address,bytes)"));
    // Flash loan callback (dYdX)
    m.insert(0xa67a6a45, ("callFunction",       "callFunction(address,(address,uint256,bytes))"));
    // Access control
    m.insert(0x9010d07c, ("getRoleMember",      "getRoleMember(bytes32,uint256)"));
    m.insert(0xca15c873, ("getRoleMemberCount", "getRoleMemberCount(bytes32)"));
    m.insert(0x2f2ff15d, ("grantRole",          "grantRole(bytes32,address)"));
    m.insert(0xd547741f, ("revokeRole",         "revokeRole(bytes32,address)"));
    m.insert(0x91d14854, ("hasRole",            "hasRole(bytes32,address)"));
    // Proxy patterns
    m.insert(0x3659cfe6, ("upgradeTo",          "upgradeTo(address)"));
    m.insert(0x4f1ef286, ("upgradeToAndCall",   "upgradeToAndCall(address,bytes)"));
    m.insert(0x5c60da1b, ("implementation",     "implementation()"));
    m.insert(0xaaf10f42, ("getImplementation",  "getImplementation()"));
    // Misc DeFi
    m.insert(0x2e1a7d4d, ("withdraw",           "withdraw(uint256)"));
    m.insert(0xd0e30db0, ("deposit",            "deposit()"));
    m.insert(0xa694fc3a, ("stake",              "stake(uint256)"));
    m.insert(0x2e17de78, ("unstake",            "unstake(uint256)"));
    m.insert(0x3d18b912, ("getReward",          "getReward()"));
    m.insert(0xe9fad8ee, ("exit",               "exit()"));
    m.insert(0x4e71d92d, ("claim",              "claim()"));
    m.insert(0x1249c58b, ("mint",               "mint()"));
    m.insert(0x40c10f19, ("mint",               "mint(address,uint256)"));
    m.insert(0x9dc29fac, ("burn",               "burn(address,uint256)"));
    m.insert(0x42966c68, ("burn",               "burn(uint256)"));
    m.insert(0x4cdad506, ("release",            "release(address)"));
    m.insert(0x19165587, ("release",            "release(address,uint256)"));
    m
}

/// Scan bytecode for PUSH4 + EQ patterns that indicate function dispatch.
pub fn recover_signatures(disasm: &Disassembly) -> SignatureReport {
    let db = builtin_4byte_db();
    let instrs = &disasm.instructions;
    let n = instrs.len();
    let mut seen: FxHashMap<u32, usize> = FxHashMap::default();
    let mut functions: Vec<FunctionSignature> = Vec::new();

    for i in 0..n {
        let ins = &instrs[i];
        // PUSH4 (0x63) followed within ~3 opcodes by EQ (0x14) or GT/LT (dispatcher pattern)
        if ins.opcode == 0x63 {
            if let Some(imm) = ins.imm_u256 {
                let sel = imm as u32;
                // Only keep 4-byte selectors (non-trivially small values)
                if sel > 0x0000_ffff {
                    // Look ahead for EQ/GT/LT confirming it's a dispatch selector
                    let is_dispatch = instrs[i + 1..std::cmp::min(i + 5, n)]
                        .iter()
                        .any(|x| matches!(x.opcode, 0x14 | 0x10 | 0x11));
                    if is_dispatch && !seen.contains_key(&sel) {
                        seen.insert(sel, ins.offset);
                    }
                }
            }
        }
    }

    // Build function entries sorted by offset
    let mut entries: Vec<(u32, usize)> = seen.into_iter().collect();
    entries.sort_by_key(|&(_, off)| off);

    for (sel, offset) in entries {
        let selector = format!("0x{sel:08x}");
        let (known_name, signature) = if let Some(&(name, sig)) = db.get(&sel) {
            (Some(name.to_string()), Some(sig.to_string()))
        } else {
            (None, None)
        };
        functions.push(FunctionSignature { selector, offset, known_name, signature });
    }

    let resolved_count = functions.iter().filter(|f| f.known_name.is_some()).count();
    let selector_count = functions.len();
    SignatureReport { functions, selector_count, resolved_count }
}

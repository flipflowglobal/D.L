// src/types.rs — EVM Type Inference
use serde::{Deserialize, Serialize};
use rustc_hash::FxHashMap;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EvmType {
    Address,
    Uint(u16),
    Int(u16),
    Bool,
    Bytes(u8),
    BytesDynamic,
    StringType,
    Mapping(Box<EvmType>, Box<EvmType>),
    Array(Box<EvmType>),
    Unknown,
}

impl EvmType {
    pub fn solidity_name(&self) -> String {
        match self {
            Self::Address       => "address".into(),
            Self::Uint(n)       => format!("uint{n}"),
            Self::Int(n)        => format!("int{n}"),
            Self::Bool          => "bool".into(),
            Self::Bytes(n)      => format!("bytes{n}"),
            Self::BytesDynamic  => "bytes".into(),
            Self::StringType    => "string".into(),
            Self::Mapping(k, v) => format!("mapping({} => {})",
                k.solidity_name(), v.solidity_name()),
            Self::Array(t)      => format!("{}[]", t.solidity_name()),
            Self::Unknown       => "uint256".into(),
        }
    }

    pub fn from_shr_shift(shift: u64) -> Option<Self> {
        match shift {
            96  => Some(Self::Address),
            224 => Some(Self::Bytes(4)),
            248 => Some(Self::Bytes(1)),
            240 => Some(Self::Bytes(2)),
            _   => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageVar {
    pub slot:   u64,
    pub name:   String,
    pub ty:     EvmType,
    pub reads:  usize,
    pub writes: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CalldataParam {
    pub index:  usize,
    pub offset: u64,
    pub name:   String,
    pub ty:     EvmType,
}

#[derive(Debug, Default)]
pub struct TypeCtx {
    pub storage: FxHashMap<u64, (EvmType, usize, usize)>,
    pub params:  FxHashMap<usize, EvmType>,
    pub var_ctr: usize,
}

impl TypeCtx {
    pub fn new() -> Self { Self::default() }

    pub fn record_sload(&mut self, slot: u64) {
        let e = self.storage.entry(slot).or_insert((EvmType::Unknown, 0, 0));
        e.1 += 1;
    }

    pub fn record_sstore(&mut self, slot: u64, ty: EvmType) {
        let e = self.storage.entry(slot).or_insert((EvmType::Unknown, 0, 0));
        if e.0 == EvmType::Unknown { e.0 = ty; }
        e.2 += 1;
    }

    pub fn record_param(&mut self, index: usize, ty: EvmType) {
        self.params.entry(index).or_insert(ty);
    }

    pub fn fresh_var(&mut self, prefix: &str) -> String {
        let n = self.var_ctr;
        self.var_ctr += 1;
        format!("{prefix}{n}")
    }

    pub fn to_storage_vars(&self) -> Vec<StorageVar> {
        let slot_names: FxHashMap<u64, (&str, EvmType)> = [
            (0, ("owner",       EvmType::Address)),
            (1, ("profitWallet",EvmType::Address)),
            (2, ("totalSupply", EvmType::Uint(256))),
            (5, ("paused",      EvmType::Bool)),
        ].iter().cloned().collect();

        let mut vars: Vec<StorageVar> = self.storage.iter().map(|(&slot, (ty, r, w))| {
            let (name, inferred) = slot_names.get(&slot)
                .map(|(n, t)| (n.to_string(), t.clone()))
                .unwrap_or_else(|| (format!("_slot{slot}"), ty.clone()));
            StorageVar {
                slot,
                name,
                ty: if *ty == EvmType::Unknown { inferred } else { ty.clone() },
                reads: *r,
                writes: *w,
            }
        }).collect();
        vars.sort_by_key(|v| v.slot);
        vars
    }
}

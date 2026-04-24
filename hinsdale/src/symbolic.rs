// src/symbolic.rs — Symbolic execution engine for EVM bytecode
use crate::cfg::CFG;
use crate::disasm::Disassembly;
use crate::types::{EvmType, TypeCtx};
use rustc_hash::FxHashMap;
use serde::{Deserialize, Serialize};

// ── Symbolic value ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Val {
    /// Concrete 64-bit constant (widened to u256 semantics on demand)
    Const(u64),
    /// Symbolic variable with a name tag
    Sym(String),
    /// Binary operation
    BinOp { op: BinOpKind, lhs: Box<Val>, rhs: Box<Val> },
    /// Unary operation
    UnOp  { op: UnOpKind,  operand: Box<Val> },
    /// Memory read
    MLoad(Box<Val>),
    /// Storage read
    SLoad(Box<Val>),
    /// Call return value
    CallResult(usize),
    /// msg.sender
    Caller,
    /// msg.value
    CallValue,
    /// tx.origin
    Origin,
    /// block.timestamp
    Timestamp,
    /// block.number
    BlockNumber,
    /// keccak256(data)
    Keccak(Box<Val>),
    /// Unknown / top
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum BinOpKind {
    Add, Sub, Mul, Div, Mod, Exp,
    And, Or, Xor, Shl, Shr, Sar,
    Eq, Lt, Gt, Slt, Sgt,
    Byte,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum UnOpKind { Not, IsZero, Neg }

impl Val {
    /// Constant-fold two concrete values through a binary op.
    pub fn binop(op: BinOpKind, lhs: Val, rhs: Val) -> Val {
        match (&lhs, &rhs) {
            (Val::Const(a), Val::Const(b)) => {
                let (a, b) = (*a, *b);
                let r = match op {
                    BinOpKind::Add  => a.wrapping_add(b),
                    BinOpKind::Sub  => a.wrapping_sub(b),
                    BinOpKind::Mul  => a.wrapping_mul(b),
                    BinOpKind::Div  => if b == 0 { 0 } else { a / b },
                    BinOpKind::Mod  => if b == 0 { 0 } else { a % b },
                    BinOpKind::And  => a & b,
                    BinOpKind::Or   => a | b,
                    BinOpKind::Xor  => a ^ b,
                    BinOpKind::Shl  => if b >= 64 { 0 } else { a << b },
                    BinOpKind::Shr  => if b >= 64 { 0 } else { a >> b },
                    BinOpKind::Eq   => u64::from(a == b),
                    BinOpKind::Lt   => u64::from(a <  b),
                    BinOpKind::Gt   => u64::from(a >  b),
                    BinOpKind::Byte => if b >= 8 { 0 } else { (a >> (56 - b * 8)) & 0xff },
                    _               => return Val::BinOp { op, lhs: Box::new(lhs), rhs: Box::new(rhs) },
                };
                Val::Const(r)
            }
            _ => Val::BinOp { op, lhs: Box::new(lhs), rhs: Box::new(rhs) },
        }
    }

    pub fn unop(op: UnOpKind, operand: Val) -> Val {
        if let Val::Const(v) = &operand {
            let v = *v;
            let r = match op {
                UnOpKind::Not    => !v,
                UnOpKind::IsZero => u64::from(v == 0),
                UnOpKind::Neg    => v.wrapping_neg(),
            };
            return Val::Const(r);
        }
        Val::UnOp { op, operand: Box::new(operand) }
    }

    pub fn is_const(&self) -> bool { matches!(self, Val::Const(_)) }

    pub fn as_const(&self) -> Option<u64> {
        if let Val::Const(v) = self { Some(*v) } else { None }
    }

    /// Render a concise display string for decompiled output.
    pub fn display(&self) -> String {
        match self {
            Val::Const(v)      => {
                // Print as address-like hex for large values, decimal for small
                if *v > 0xffff { format!("0x{v:x}") } else { v.to_string() }
            }
            Val::Sym(s)        => s.clone(),
            Val::Caller        => "msg.sender".into(),
            Val::CallValue     => "msg.value".into(),
            Val::Origin        => "tx.origin".into(),
            Val::Timestamp     => "block.timestamp".into(),
            Val::BlockNumber   => "block.number".into(),
            Val::Keccak(v)     => format!("keccak256({})", v.display()),
            Val::MLoad(a)      => format!("mload({})", a.display()),
            Val::SLoad(k)      => format!("sload({})", k.display()),
            Val::CallResult(n) => format!("call_ret_{n}"),
            Val::Unknown       => "_".into(),
            Val::BinOp { op, lhs, rhs } => {
                let sym = match op {
                    BinOpKind::Add => "+",  BinOpKind::Sub => "-",
                    BinOpKind::Mul => "*",  BinOpKind::Div => "/",
                    BinOpKind::Mod => "%",  BinOpKind::And => "&",
                    BinOpKind::Or  => "|",  BinOpKind::Xor => "^",
                    BinOpKind::Shl => "<<", BinOpKind::Shr => ">>",
                    BinOpKind::Eq  => "==", BinOpKind::Lt  => "<",
                    BinOpKind::Gt  => ">",  BinOpKind::Slt => "s<",
                    BinOpKind::Sgt => "s>", BinOpKind::Sar => ">>s",
                    BinOpKind::Exp => "**", BinOpKind::Byte => "byte",
                };
                format!("({} {sym} {})", lhs.display(), rhs.display())
            }
            Val::UnOp { op, operand } => {
                let sym = match op {
                    UnOpKind::Not    => "~",
                    UnOpKind::IsZero => "!",
                    UnOpKind::Neg    => "-",
                };
                format!("{sym}({})", operand.display())
            }
        }
    }
}

// ── Symbolic stack ──────────────────────────────────────────────────────────

const STACK_LIMIT: usize = 1024;

#[derive(Debug, Clone, Default)]
pub struct SymStack {
    items: Vec<Val>,
}

impl SymStack {
    pub fn new() -> Self { Self::default() }

    pub fn push(&mut self, v: Val) {
        if self.items.len() < STACK_LIMIT {
            self.items.push(v);
        }
    }

    pub fn pop(&mut self) -> Val {
        self.items.pop().unwrap_or(Val::Unknown)
    }

    pub fn peek(&self) -> &Val {
        self.items.last().unwrap_or(&Val::Unknown)
    }

    pub fn depth(&self) -> usize { self.items.len() }

    pub fn dup(&mut self, n: usize) {
        let idx = self.items.len().saturating_sub(n);
        let v = self.items.get(idx).cloned().unwrap_or(Val::Unknown);
        self.push(v);
    }

    pub fn swap(&mut self, n: usize) {
        let len = self.items.len();
        if len > n {
            self.items.swap(len - 1, len - 1 - n);
        }
    }
}

// ── IR statements produced during symbolic execution ───────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Stmt {
    Assign   { lhs: String,      rhs: String },
    SStore   { key: String,      val: String },
    MStore   { addr: String,     val: String },
    Emit     { topics: Vec<String>, data: String },
    Call     { id: usize, gas: String, addr: String, value: String,
                args_offset: String, args_len: String, ret_id: String },
    Return   { offset: String, len: String },
    Revert   { offset: String, len: String },
    Jump     { target: String },
    JumpI    { cond: String, target: String },
    Stop,
    SelfDestruct { addr: String },
    Create   { id: usize, value: String, offset: String, len: String },
    Comment  (String),
}

// ── Per-block symbolic execution result ────────────────────────────────────

#[derive(Debug, Clone)]
pub struct BlockResult {
    pub block_id:   usize,
    pub stmts:      Vec<Stmt>,
    pub stack_out:  SymStack,
    pub call_count: usize,
}

// ── Main symbolic executor ──────────────────────────────────────────────────

pub struct SymExec<'a> {
    disasm:    &'a Disassembly,
    cfg:       &'a CFG,
    pub type_ctx: TypeCtx,
    call_ctr:  usize,
    create_ctr: usize,
}

impl<'a> SymExec<'a> {
    pub fn new(disasm: &'a Disassembly, cfg: &'a CFG) -> Self {
        Self {
            disasm,
            cfg,
            type_ctx: TypeCtx::new(),
            call_ctr: 0,
            create_ctr: 0,
        }
    }

    /// Execute a single basic block symbolically, returning the IR + stack state.
    pub fn exec_block(&mut self, block_id: usize, stack_in: SymStack) -> BlockResult {
        let Some(block) = self.cfg.blocks.get(block_id) else {
            return BlockResult { block_id, stmts: vec![], stack_out: stack_in, call_count: 0 };
        };

        let mut stack = stack_in;
        let mut stmts: Vec<Stmt> = Vec::new();
        let instrs = &self.disasm.instructions;
        let call_count_before = self.call_ctr;

        for &idx in &block.instructions {
            let ins = &instrs[idx];
            self.step(ins.opcode, ins.imm_u256, &mut stack, &mut stmts);
        }

        BlockResult {
            block_id,
            stmts,
            stack_out: stack,
            call_count: self.call_ctr - call_count_before,
        }
    }

    /// Execute all blocks in program order (linear pass, ignoring back-edges).
    pub fn exec_all(&mut self) -> FxHashMap<usize, BlockResult> {
        let block_count = self.cfg.blocks.len();
        let mut results: FxHashMap<usize, BlockResult> = FxHashMap::default();
        let mut stack = SymStack::new();

        for bid in 0..block_count {
            let result = self.exec_block(bid, stack.clone());
            stack = result.stack_out.clone();
            results.insert(bid, result);
        }
        results
    }

    fn step(
        &mut self,
        opcode: u8,
        imm: Option<u64>,
        stack: &mut SymStack,
        stmts: &mut Vec<Stmt>,
    ) {
        match opcode {
            // ── Arithmetic ──────────────────────────────────────────────
            0x01 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Add,a,b)); }
            0x02 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Mul,a,b)); }
            0x03 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Sub,a,b)); }
            0x04 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Div,a,b)); }
            0x06 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Mod,a,b)); }
            0x0a => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Exp,a,b)); }
            0x05 | 0x07 | 0x08 | 0x09 | 0x0b => {
                let a=stack.pop(); let b=stack.pop();
                if matches!(opcode, 0x08 | 0x09) { stack.pop(); }
                stack.push(Val::Unknown);
                let _ = (a,b);
            }
            // ── Comparison / bitwise ────────────────────────────────────
            0x10 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Lt,a,b)); }
            0x11 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Gt,a,b)); }
            0x12 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Slt,a,b)); }
            0x13 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Sgt,a,b)); }
            0x14 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Eq,a,b)); }
            0x15 => { let a=stack.pop(); stack.push(Val::unop(UnOpKind::IsZero,a)); }
            0x16 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::And,a,b)); }
            0x17 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Or,a,b)); }
            0x18 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Xor,a,b)); }
            0x19 => { let a=stack.pop(); stack.push(Val::unop(UnOpKind::Not,a)); }
            0x1a => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Byte,a,b)); }
            0x1b => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Shl,a,b)); }
            0x1c => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Shr,a,b)); }
            0x1d => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::binop(BinOpKind::Sar,a,b)); }
            // ── KECCAK256 ───────────────────────────────────────────────
            0x20 => { let a=stack.pop(); let _b=stack.pop(); stack.push(Val::Keccak(Box::new(a))); }
            // ── Environment ─────────────────────────────────────────────
            0x30 => stack.push(Val::Sym("address(this)".into())),
            0x31 => { let _a=stack.pop(); stack.push(Val::Sym("balance".into())); }
            0x32 => stack.push(Val::Origin),
            0x33 => stack.push(Val::Caller),
            0x34 => stack.push(Val::CallValue),
            0x35 => {
                let off = stack.pop();
                // Infer type from SHR 96 pattern (address extraction)
                if let Val::BinOp { op: BinOpKind::Shr, lhs, .. } = &off {
                    if let Val::Const(96) = lhs.as_ref() {
                        self.type_ctx.record_param(0, EvmType::Address);
                    }
                }
                stack.push(Val::Sym(format!("calldata[{}]", off.display())));
            }
            0x36 => stack.push(Val::Sym("calldatasize".into())),
            0x37 | 0x39 | 0x3c | 0x3e => {
                stack.pop(); stack.pop(); stack.pop();
                if opcode == 0x3c { stack.pop(); }
            }
            0x38 => stack.push(Val::Sym("codesize".into())),
            0x3a => stack.push(Val::Sym("tx.gasprice".into())),
            0x3b => { let _a=stack.pop(); stack.push(Val::Sym("extcodesize".into())); }
            0x3d => stack.push(Val::Sym("returndatasize".into())),
            0x3f => { let _a=stack.pop(); stack.push(Val::Sym("extcodehash".into())); }
            // ── Block info ──────────────────────────────────────────────
            0x40 => { let _=stack.pop(); stack.push(Val::Sym("blockhash".into())); }
            0x41 => stack.push(Val::Sym("block.coinbase".into())),
            0x42 => stack.push(Val::Timestamp),
            0x43 => stack.push(Val::BlockNumber),
            0x44 => stack.push(Val::Sym("block.prevrandao".into())),
            0x45 => stack.push(Val::Sym("block.gaslimit".into())),
            0x46 => stack.push(Val::Sym("block.chainid".into())),
            0x47 => stack.push(Val::Sym("address(this).balance".into())),
            0x48 => stack.push(Val::Sym("block.basefee".into())),
            0x49 => { let _=stack.pop(); stack.push(Val::Sym("blobhash".into())); }
            0x4a => stack.push(Val::Sym("block.blobbasefee".into())),
            // ── Stack ops ───────────────────────────────────────────────
            0x50 => { stack.pop(); }
            0x58 => stack.push(Val::Sym("pc".into())),
            0x5a => stack.push(Val::Sym("gasleft()".into())),
            0x5b => {} // JUMPDEST — no-op
            0x5f => stack.push(Val::Const(0)),
            // PUSH1–PUSH32
            0x60..=0x7f => {
                stack.push(imm.map(Val::Const).unwrap_or(Val::Unknown));
            }
            // DUP1–DUP16
            0x80..=0x8f => {
                let n = (opcode - 0x7f) as usize;
                stack.dup(n);
            }
            // SWAP1–SWAP16
            0x90..=0x9f => {
                let n = (opcode - 0x8f) as usize;
                stack.swap(n);
            }
            // ── Memory ──────────────────────────────────────────────────
            0x51 => { let addr=stack.pop(); stack.push(Val::MLoad(Box::new(addr))); }
            0x52 => {
                let addr=stack.pop(); let val=stack.pop();
                stmts.push(Stmt::MStore { addr: addr.display(), val: val.display() });
            }
            0x53 => { stack.pop(); stack.pop(); }
            0x59 => stack.push(Val::Sym("msize".into())),
            0x5e => { stack.pop(); stack.pop(); stack.pop(); } // MCOPY
            // ── Storage ─────────────────────────────────────────────────
            0x54 => {
                let key=stack.pop();
                if let Some(slot) = key.as_const() { self.type_ctx.record_sload(slot); }
                stack.push(Val::SLoad(Box::new(key)));
            }
            0x55 => {
                let key=stack.pop(); let val=stack.pop();
                if let Some(slot) = key.as_const() {
                    let ty = infer_type_from_val(&val);
                    self.type_ctx.record_sstore(slot, ty);
                }
                stmts.push(Stmt::SStore { key: key.display(), val: val.display() });
            }
            // Transient storage
            0x5c => { let _k=stack.pop(); stack.push(Val::Unknown); }
            0x5d => { stack.pop(); stack.pop(); }
            // ── Flow ────────────────────────────────────────────────────
            0x56 => {
                let target=stack.pop();
                stmts.push(Stmt::Jump { target: target.display() });
            }
            0x57 => {
                let target=stack.pop(); let cond=stack.pop();
                stmts.push(Stmt::JumpI { cond: cond.display(), target: target.display() });
            }
            // ── Logs ────────────────────────────────────────────────────
            0xa0..=0xa4 => {
                let topic_count = (opcode - 0xa0) as usize;
                let _offset=stack.pop(); let _len=stack.pop();
                let mut topics = Vec::with_capacity(topic_count);
                for _ in 0..topic_count { topics.push(stack.pop().display()); }
                stmts.push(Stmt::Emit { topics, data: "mem[...]".into() });
            }
            // ── System ──────────────────────────────────────────────────
            0xf0 => {
                let value=stack.pop(); let offset=stack.pop(); let len=stack.pop();
                let id = self.create_ctr; self.create_ctr += 1;
                stmts.push(Stmt::Create { id, value: value.display(),
                    offset: offset.display(), len: len.display() });
                stack.push(Val::Sym(format!("created_{id}")));
            }
            0xf1 | 0xf2 | 0xfa => {
                let gas=stack.pop(); let addr=stack.pop();
                let value = if opcode == 0xfa { Val::Const(0) } else { stack.pop() };
                let args_offset=stack.pop(); let args_len=stack.pop();
                let ret_offset=stack.pop(); let ret_len=stack.pop();
                let id = self.call_ctr; self.call_ctr += 1;
                let ret_id = format!("ret_{id}");
                stmts.push(Stmt::Call {
                    id, gas: gas.display(), addr: addr.display(), value: value.display(),
                    args_offset: args_offset.display(), args_len: args_len.display(),
                    ret_id: ret_id.clone(),
                });
                let _ = (ret_offset, ret_len);
                stack.push(Val::CallResult(id));
            }
            0xf4 => {
                let gas=stack.pop(); let addr=stack.pop();
                let args_offset=stack.pop(); let args_len=stack.pop();
                let ret_offset=stack.pop(); let ret_len=stack.pop();
                let id = self.call_ctr; self.call_ctr += 1;
                stmts.push(Stmt::Call {
                    id, gas: gas.display(), addr: addr.display(), value: "0".into(),
                    args_offset: args_offset.display(), args_len: args_len.display(),
                    ret_id: format!("ret_{id}"),
                });
                let _ = (ret_offset, ret_len);
                stack.push(Val::CallResult(id));
            }
            0xf5 => {
                let value=stack.pop(); let offset=stack.pop();
                let len=stack.pop(); let _salt=stack.pop();
                let id = self.create_ctr; self.create_ctr += 1;
                stmts.push(Stmt::Create { id, value: value.display(),
                    offset: offset.display(), len: len.display() });
                stack.push(Val::Sym(format!("created2_{id}")));
            }
            0xf3 => {
                let offset=stack.pop(); let len=stack.pop();
                stmts.push(Stmt::Return { offset: offset.display(), len: len.display() });
            }
            0xfd => {
                let offset=stack.pop(); let len=stack.pop();
                stmts.push(Stmt::Revert { offset: offset.display(), len: len.display() });
            }
            0x00 => stmts.push(Stmt::Stop),
            0xff => {
                let addr=stack.pop();
                stmts.push(Stmt::SelfDestruct { addr: addr.display() });
            }
            0xfe => stmts.push(Stmt::Comment("INVALID".into())),
            _ => {} // Unknown / unhandled
        }
    }
}

fn infer_type_from_val(val: &Val) -> EvmType {
    match val {
        Val::Caller | Val::Origin => EvmType::Address,
        Val::Const(v) if *v <= 1  => EvmType::Bool,
        Val::Const(_) => EvmType::Uint(256),
        Val::BinOp { op: BinOpKind::And, rhs, .. } => {
            if let Val::Const(mask) = rhs.as_ref() {
                match *mask {
                    0xff   => return EvmType::Uint(8),
                    0xffff => return EvmType::Uint(16),
                    _      => {}
                }
            }
            EvmType::Unknown
        }
        _ => EvmType::Unknown,
    }
}

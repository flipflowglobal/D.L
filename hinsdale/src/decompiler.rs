// src/decompiler.rs — High-level EVM decompiler using symbolic execution
use crate::cfg::CFG;
use crate::disasm::Disassembly;
use crate::signatures::SignatureReport;
use crate::symbolic::{Stmt, SymExec};
use crate::types::StorageVar;
use serde::{Deserialize, Serialize};

/// A decompiled function body.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecompiledFunction {
    pub selector:   String,
    pub name:       String,
    pub signature:  Option<String>,
    pub body:       Vec<String>,
    pub start_block: usize,
}

/// Complete decompiler output.
#[derive(Debug, Serialize, Deserialize)]
pub struct DecompiledOutput {
    pub functions:    Vec<DecompiledFunction>,
    pub storage_vars: Vec<StorageVar>,
    pub pseudo_code:  String,
}

pub fn decompile(
    disasm: &Disassembly,
    cfg: &CFG,
    sigs: &SignatureReport,
) -> DecompiledOutput {
    if disasm.instructions.is_empty() {
        return DecompiledOutput {
            functions:    vec![],
            storage_vars: vec![],
            pseudo_code:  String::new(),
        };
    }

    let mut exec = SymExec::new(disasm, cfg);
    let results  = exec.exec_all();

    // Map selector offset → block ID
    let offset_to_block: std::collections::HashMap<usize, usize> = cfg
        .offset_to_block.iter().cloned().collect();

    let mut functions: Vec<DecompiledFunction> = Vec::new();

    for func_sig in &sigs.functions {
        let start_block = offset_to_block
            .get(&func_sig.offset)
            .copied()
            .unwrap_or(0);

        let body = collect_body(&results, start_block, cfg);
        let name = func_sig.known_name.clone()
            .unwrap_or_else(|| format!("fn_{}", &func_sig.selector));

        functions.push(DecompiledFunction {
            selector:    func_sig.selector.clone(),
            name,
            signature:   func_sig.signature.clone(),
            body,
            start_block,
        });
    }

    // If no selectors found but there is code, emit a fallback function
    if functions.is_empty() && !cfg.blocks.is_empty() {
        let body = collect_body(&results, 0, cfg);
        functions.push(DecompiledFunction {
            selector:    "0x00000000".into(),
            name:        "fallback".into(),
            signature:   None,
            body,
            start_block: 0,
        });
    }

    let storage_vars = exec.type_ctx.to_storage_vars();
    let pseudo_code  = render_pseudo_code(&functions, &storage_vars);

    DecompiledOutput { functions, storage_vars, pseudo_code }
}

/// Collect IR statements for a function starting at `start_block`,
/// following CFG successors up to a configurable depth limit.
fn collect_body(
    results: &rustc_hash::FxHashMap<usize, crate::symbolic::BlockResult>,
    start_block: usize,
    cfg: &CFG,
) -> Vec<String> {
    const MAX_STMTS: usize = 256;
    const MAX_DEPTH: usize = 32;

    let mut body:    Vec<String> = Vec::new();
    let mut visited: std::collections::HashSet<usize> = std::collections::HashSet::new();
    let mut queue:   std::collections::VecDeque<(usize, usize)> = std::collections::VecDeque::new();
    queue.push_back((start_block, 0));

    while let Some((bid, depth)) = queue.pop_front() {
        if !visited.insert(bid) || depth > MAX_DEPTH || body.len() >= MAX_STMTS { continue; }

        if let Some(result) = results.get(&bid) {
            let block = &cfg.blocks[bid];
            // Label for block transitions
            if depth > 0 {
                body.push(format!("/* block_{bid} @ 0x{:x} */", block.start_offset));
            }
            for stmt in &result.stmts {
                body.push(render_stmt(stmt));
            }
            for &succ in &block.successors {
                queue.push_back((succ, depth + 1));
            }
        }
    }

    if body.is_empty() { body.push("/* empty */".into()); }
    body
}

fn render_stmt(stmt: &Stmt) -> String {
    match stmt {
        Stmt::Assign   { lhs, rhs }     => format!("    {lhs} = {rhs};"),
        Stmt::SStore   { key, val }      => format!("    storage[{key}] = {val};"),
        Stmt::MStore   { addr, val }     => format!("    mem[{addr}] = {val};"),
        Stmt::Return   { offset, len }   => format!("    return mem[{offset}..{offset}+{len}];"),
        Stmt::Revert   { offset, len }   => format!("    revert(mem[{offset}..{offset}+{len}]);"),
        Stmt::Stop                       => "    return;".into(),
        Stmt::SelfDestruct { addr }      => format!("    selfdestruct({addr});"),
        Stmt::Jump     { target }        => format!("    goto {target};"),
        Stmt::JumpI    { cond, target }  => format!("    if ({cond}) goto {target};"),
        Stmt::Create   { id, value, offset, len } =>
            format!("    addr_{id} = new contract(value={value}, mem[{offset}..{offset}+{len}]);"),
        Stmt::Call { id, addr, value, args_offset, args_len, ret_id, .. } =>
            format!("    {ret_id} = call({addr}, value={value}, \
                     args=mem[{args_offset}..{args_offset}+{args_len}]); // call_{id}"),
        Stmt::Emit { topics, data }     => {
            let t = topics.join(", ");
            format!("    emit Event(topics=[{t}], data={data});")
        }
        Stmt::Comment(s)                => format!("    // {s}"),
    }
}

fn render_pseudo_code(
    functions: &[DecompiledFunction],
    storage_vars: &[StorageVar],
) -> String {
    let mut out = String::with_capacity(4096);
    out.push_str("// Hinsdale EVM Decompiler — pseudo-Solidity output\n");
    out.push_str("// ─────────────────────────────────────────────────\n\n");

    if !storage_vars.is_empty() {
        out.push_str("// Storage layout:\n");
        for v in storage_vars {
            out.push_str(&format!("//   slot {:>3}  {}  {}  (reads={}, writes={})\n",
                v.slot, v.ty.solidity_name(), v.name, v.reads, v.writes));
        }
        out.push('\n');
    }

    for func in functions {
        let sig = func.signature.as_deref().unwrap_or(&func.name);
        out.push_str(&format!("function {} {{  // selector {}\n", sig, func.selector));
        for line in &func.body {
            out.push_str(line);
            out.push('\n');
        }
        out.push_str("}\n\n");
    }

    out
}

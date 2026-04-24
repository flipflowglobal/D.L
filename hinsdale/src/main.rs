// src/main.rs — Hinsdale CLI
use std::io::Read;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    // Flags can appear before or after the positional hex argument
    let pretty = args.iter().any(|a| a == "--pretty" || a == "-p");

    // First non-flag argument is the hex input; fall back to stdin
    let hex_input = args.iter()
        .find(|a| !a.starts_with('-'))
        .cloned()
        .unwrap_or_else(|| {
            let mut buf = String::new();
            std::io::stdin().read_to_string(&mut buf).expect("failed to read stdin");
            buf.trim().to_string()
        });

    if hex_input.is_empty() {
        eprintln!("Usage: hinsdale-cli [--pretty|-p] <hex_bytecode>");
        eprintln!("       echo <hex> | hinsdale-cli [--pretty|-p]");
        std::process::exit(1);
    }

    let bytes = match hinsdale::parse_hex(&hex_input) {
        Ok(b) => b,
        Err(e) => { eprintln!("Error: {e}"); std::process::exit(1); }
    };

    let report = hinsdale::analyze(&bytes);

    let json = if pretty {
        serde_json::to_string_pretty(&report)
    } else {
        serde_json::to_string(&report)
    }.expect("serialization failed");

    println!("{json}");
}

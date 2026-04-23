// src/main.rs — Hinsdale CLI
use std::io::Read;

fn main() {
    let args: Vec<String> = std::env::args().collect();

    let hex_input = if args.len() > 1 {
        args[1].clone()
    } else {
        let mut buf = String::new();
        std::io::stdin().read_to_string(&mut buf).expect("failed to read stdin");
        buf.trim().to_string()
    };

    let bytes = match hinsdale::parse_hex(&hex_input) {
        Ok(b) => b,
        Err(e) => { eprintln!("Error: {e}"); std::process::exit(1); }
    };

    let report = hinsdale::analyze(&bytes);

    let pretty = std::env::args().any(|a| a == "--pretty" || a == "-p");
    let json = if pretty {
        serde_json::to_string_pretty(&report)
    } else {
        serde_json::to_string(&report)
    }.expect("serialization failed");

    println!("{json}");
}

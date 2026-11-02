use acg::commands::memory_handoff::run_handoff;
use acg::console::{log_error, log_info};
use acg::paths::entrypoint;

fn memory_usage() -> String {
    format!(
        "Usage: {} memory <status|enable [codex|agy]|disable [codex|agy]|adopt [路徑|all|--scan [目錄]]|release [路徑]|path|push|handoff|autopush>",
        entrypoint()
    )
}

fn main() {
    let raw_args: Vec<String> = std::env::args().skip(1).collect();

    let mut force = false;
    let mut args = Vec::new();
    for token in raw_args {
        if token == "--force" || token == "-f" {
            force = true;
        } else {
            args.push(token);
        }
    }

    if args.is_empty() || args.first().map(|s| s.as_str()) == Some("--help") || args.first().map(|s| s.as_str()) == Some("-h") {
        log_info(&format!("Usage: {} memory handoff ...", entrypoint()));
        std::process::exit(0);
    }

    let cmd = args[0].as_str();
    let rest = &args[1..];

    if cmd == "memory" {
        if rest.is_empty() || rest[0] == "--help" || rest[0] == "-h" {
            log_info(&memory_usage());
            std::process::exit(0);
        }
        if rest[0] == "handoff" {
            let code = run_handoff(&rest[1..], force);
            std::process::exit(code);
        } else {
            log_error(&memory_usage());
            std::process::exit(1);
        }
    } else {
        log_error(&format!("Unknown command: {cmd}"));
        std::process::exit(1);
    }
}

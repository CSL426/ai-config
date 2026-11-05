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
    let raw_args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|s| s.to_string_lossy().into_owned())
        .collect();

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
        let sub = rest.first().map(|s| s.as_str()).unwrap_or("");
        if sub == "--help" || sub == "-h" {
            log_info(&memory_usage());
            std::process::exit(0);
        }

        let data_dir = match acg::paths::data_dir() {
            Ok(dir) => dir,
            Err(err) => {
                log_error(&err);
                log_info(&format!(
                    "Run {} setup to replace the invalid configuration",
                    entrypoint()
                ));
                std::process::exit(1);
            }
        };

        if !data_dir.join("claude").is_dir() {
            log_error(&format!(
                "Repository configuration directory not found at {}.\nRun {} setup to configure and verify your data repository.",
                data_dir.display(),
                entrypoint()
            ));
            std::process::exit(1);
        }

        if sub == "handoff" {
            let code = run_handoff(&rest[1..], force);
            std::process::exit(code);
        }
        if sub.is_empty() {
            log_error("Command not yet implemented in Rust: memory");
        } else {
            log_error(&format!("Command not yet implemented in Rust: memory {sub}"));
        }
        std::process::exit(1);
    }

    let data_dir = match acg::paths::data_dir() {
        Ok(dir) => dir,
        Err(err) => {
            log_error(&err);
            log_info(&format!(
                "Run {} setup to replace the invalid configuration",
                entrypoint()
            ));
            std::process::exit(1);
        }
    };

    if !data_dir.join("claude").is_dir() {
        log_error(&format!(
            "Repository configuration directory not found at {}.\nRun {} setup to configure and verify your data repository.",
            data_dir.display(),
            entrypoint()
        ));
        std::process::exit(1);
    }

    log_error(&format!("Command not yet implemented in Rust: {cmd}"));
    std::process::exit(1);
}

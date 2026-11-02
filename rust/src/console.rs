use std::io::IsTerminal;

fn use_color(stream_is_stdout: bool) -> bool {
    let is_term = if stream_is_stdout {
        std::io::stdout().is_terminal()
    } else {
        std::io::stderr().is_terminal()
    };
    is_term && std::env::var("NO_COLOR").is_err()
}

pub fn log_info(msg: &str) {
    if use_color(true) {
        println!("\x1b[0;34mℹ\x1b[0m {msg}");
    } else {
        println!("ℹ {msg}");
    }
}

pub fn log_success(msg: &str) {
    if use_color(true) {
        println!("\x1b[0;32m✓\x1b[0m {msg}");
    } else {
        println!("✓ {msg}");
    }
}

pub fn log_warn(msg: &str) {
    if use_color(true) {
        println!("\x1b[1;33m⚠\x1b[0m {msg}");
    } else {
        println!("⚠ {msg}");
    }
}

pub fn log_error(msg: &str) {
    if use_color(false) {
        eprintln!("\x1b[0;31m✗\x1b[0m {msg}");
    } else {
        eprintln!("✗ {msg}");
    }
}

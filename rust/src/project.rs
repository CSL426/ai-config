use regex::Regex;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn sanitize_key(name: &str) -> String {
    let re = Regex::new(r"[^A-Za-z0-9._-]+").unwrap();
    let cleaned = re.replace_all(name.trim(), "-");
    let trimmed = cleaned.trim_matches(['-', '.']);
    if trimmed.is_empty() {
        "unnamed".to_string()
    } else {
        trimmed.to_string()
    }
}

pub fn parse_project_key(remote_url: &str) -> String {
    let tail_re = Regex::new(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$").unwrap();
    if let Some(caps) = tail_re.captures(remote_url.trim()) {
        let owner = sanitize_key(caps.get(1).unwrap().as_str());
        let repo = sanitize_key(caps.get(2).unwrap().as_str());
        format!("{owner}--{repo}")
    } else {
        String::new()
    }
}

fn git_output(cwd: &Path, args: &[&str]) -> Option<String> {
    let mut child = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;

    let (tx, rx) = std::sync::mpsc::channel();
    let stdout = child.stdout.take();
    let thread_handle = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut stream) = stdout {
            use std::io::Read;
            let _ = stream.read_to_end(&mut buf);
        }
        let _ = tx.send(buf);
    });

    match rx.recv_timeout(std::time::Duration::from_secs(10)) {
        Ok(bytes) => {
            let status = child.wait().ok()?;
            let _ = thread_handle.join();
            if status.success() {
                let text = String::from_utf8_lossy(&bytes).trim().to_string();
                if !text.is_empty() {
                    return Some(text);
                }
            }
            None
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = thread_handle.join();
            None
        }
    }
}

pub fn project_key(cwd: Option<&Path>) -> String {
    let cur = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let here = cwd.unwrap_or(&cur);

    if let Some(remote) = git_output(here, &["remote", "get-url", "origin"]) {
        let key = parse_project_key(&remote);
        if !key.is_empty() {
            return key;
        }
    }

    if let Some(toplevel) = git_output(here, &["rev-parse", "--show-toplevel"]) {
        let top_path = PathBuf::from(toplevel);
        if let Some(name) = top_path.file_name().and_then(|n| n.to_str()) {
            return sanitize_key(name);
        }
    }

    let resolved_name = here
        .canonicalize()
        .ok()
        .and_then(|p| p.file_name().and_then(|n| n.to_str()).map(|s| s.to_string()))
        .or_else(|| here.file_name().and_then(|n| n.to_str()).map(|s| s.to_string()))
        .unwrap_or_else(|| "cwd".to_string());
    sanitize_key(&resolved_name)
}

pub fn home_project(cwd: Option<&Path>) -> String {
    let target = cwd.map(|p| p.to_path_buf()).or_else(crate::session::session_home);
    project_key(target.as_deref())
}

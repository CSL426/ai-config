use regex::Regex;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::handoff::write_text_atomic;
use crate::locking::apply_lock;
use crate::paths;
use crate::safety;
use crate::sha256::sha256_hex;

pub const STATUS_COMMAND: &str = "__handoff-statusline";
pub const HOOK_COMMAND: &str = "__handoff-reminder";
pub const MARKER: &str = "acg：交接用量提醒";
pub const EVENTS: [&str; 4] = ["UserPromptSubmit", "PostToolUse", "PreCompact", "SessionEnd"];
pub const DEFAULT_THRESHOLD: i64 = 70;
pub const MAX_AGE: f64 = 300.0;
pub const MAX_INPUT: u64 = 4 * 1024 * 1024;

pub fn format_g(val: f64) -> String {
    if val.trunc() == val {
        format!("{:.0}", val)
    } else {
        let s = format!("{val:.6}");
        let trimmed = s.trim_end_matches('0').trim_end_matches('.');
        trimmed.to_string()
    }
}

pub fn urlsafe_b64decode(input: &str) -> Option<Vec<u8>> {
    let clean = input.trim_end_matches('=');
    let mut bits = 0u32;
    let mut bit_count = 0;
    let mut out = Vec::new();
    for byte in clean.bytes() {
        let val = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'-' | b'+' => 62,
            b'_' | b'/' => 63,
            b'\r' | b'\n' | b' ' => continue,
            _ => return None,
        };
        bits = (bits << 6) | (val as u32);
        bit_count += 6;
        if bit_count >= 8 {
            bit_count -= 8;
            out.push((bits >> bit_count) as u8);
            bits &= (1 << bit_count) - 1;
        }
    }
    Some(out)
}

fn decode_original(value: &str) -> Option<Option<String>> {
    let bytes = urlsafe_b64decode(value)?;
    let text = std::str::from_utf8(&bytes).ok()?;
    let val: Value = serde_json::from_str(text).ok()?;
    if val.is_null() {
        return Some(None);
    }
    let obj = val.as_object()?;
    if obj.get("type").and_then(|v| v.as_str()) != Some("command") {
        return None;
    }
    let cmd = obj.get("command").and_then(|v| v.as_str())?;
    Some(Some(cmd.to_string()))
}

fn check_threshold(value: i64) -> Option<i64> {
    if (1..=99).contains(&value) {
        Some(value)
    } else {
        None
    }
}

fn wrapper(document: &Value) -> Option<(Option<String>, i64)> {
    let line = document.get("statusLine")?.as_object()?;
    let cmd = line.get("command")?.as_str()?;
    let re = Regex::new(r"(?:^| )__handoff-statusline ([A-Za-z0-9_=-]+) ([0-9]+)$").unwrap();
    let caps = re.captures(cmd)?;
    let encoded = caps.get(1)?.as_str();
    let thresh_str = caps.get(2)?.as_str();
    let original = decode_original(encoded)?;
    let threshold = check_threshold(thresh_str.parse::<i64>().ok()?)?;
    Some((original, threshold))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReminderStatus {
    pub enabled: bool,
    pub threshold: i64,
    pub installed: bool,
}

fn has_reminder_hook(rows: Option<&Value>) -> bool {
    let arr = match rows.and_then(|v| v.as_array()) {
        Some(a) => a,
        None => return false,
    };
    arr.iter().any(|row| {
        row.get("hooks")
            .and_then(|v| v.as_array())
            .map(|hooks| {
                hooks.iter().any(|h| {
                    h.get("statusMessage").and_then(|v| v.as_str()) == Some(MARKER)
                })
            })
            .unwrap_or(false)
    })
}

pub fn reminder_status() -> ReminderStatus {
    let home = match paths::claude_home() {
        Some(h) => h,
        None => {
            return ReminderStatus {
                enabled: false,
                threshold: DEFAULT_THRESHOLD,
                installed: false,
            }
        }
    };
    let settings_path = home.join("settings.json");
    if safety::assert_plain_path(&settings_path, Some(false)).is_err() {
        return ReminderStatus {
            enabled: false,
            threshold: DEFAULT_THRESHOLD,
            installed: false,
        };
    }

    let document: Value = if settings_path.exists() {
        match std::fs::read_to_string(&settings_path) {
            Ok(content) => {
                let trimmed = content.strip_prefix('\u{feff}').unwrap_or(&content);
                serde_json::from_str(trimmed).unwrap_or(Value::Null)
            }
            Err(_) => Value::Null,
        }
    } else {
        Value::Object(serde_json::Map::new())
    };

    if !document.is_object() {
        return ReminderStatus {
            enabled: false,
            threshold: DEFAULT_THRESHOLD,
            installed: false,
        };
    }

    let wrapped = wrapper(&document);
    let events = document.get("hooks").and_then(|v| v.as_object());
    let installed = events.is_some()
        && EVENTS
            .iter()
            .all(|event| has_reminder_hook(events.and_then(|e| e.get(*event))));

    ReminderStatus {
        enabled: wrapped.is_some(),
        threshold: wrapped.as_ref().map(|w| w.1).unwrap_or(DEFAULT_THRESHOLD),
        installed: wrapped.is_some() && installed,
    }
}

pub fn session_path(payload: &Value) -> Result<PathBuf, String> {
    let session = payload
        .get("session_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing session identity".to_string())?;
    let cwd = payload
        .get("cwd")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing session identity".to_string())?;
    if session.is_empty() || session.len() > 512 || cwd.is_empty() || !Path::new(cwd).is_absolute() {
        return Err("Missing session identity".to_string());
    }

    let key = sha256_hex(session.as_bytes());
    let home = paths::claude_home().ok_or_else(|| "Missing claude home".to_string())?;
    let path = home.join(".acg-handoff-reminder").join(format!("{key}.json"));
    safety::assert_plain_path(&path, Some(false))?;
    Ok(path)
}

pub fn read_state(path: &Path) -> serde_json::Map<String, Value> {
    if !path.exists() {
        return serde_json::Map::new();
    }
    if let Ok(meta) = path.metadata() {
        if meta.len() > 8192 {
            return serde_json::Map::new();
        }
    } else {
        return serde_json::Map::new();
    }
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return serde_json::Map::new(),
    };
    let val: Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(_) => return serde_json::Map::new(),
    };
    match val {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    }
}

pub fn percentage(payload: &Value) -> Option<f64> {
    let window = payload.get("context_window")?.as_object()?;
    if let Some(usage) = window.get("current_usage") {
        if usage.is_null() {
            return None;
        }
    }

    let val_opt = if let Some(used) = window.get("used_percentage") {
        Some(used.as_f64()?)
    } else if let Some(remaining) = window.get("remaining_percentage") {
        Some(100.0 - remaining.as_f64()?)
    } else {
        None
    };

    let val = val_opt?;
    if val.is_finite() && (0.0..=100.0).contains(&val) {
        Some(val)
    } else {
        None
    }
}

pub fn record(payload: &Value, threshold: i64) -> Option<f64> {
    let _threshold_val = check_threshold(threshold)?;
    let _guard = apply_lock(Duration::from_millis(200)).ok()?;
    let path = session_path(payload).ok()?;
    let previous = read_state(&path);
    let used = percentage(payload);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let cwd = payload.get("cwd")?.as_str()?;
    let prev_notified = previous
        .get("notified")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let prev_threshold = previous.get("threshold").and_then(|v| v.as_i64());
    let notified = prev_notified && prev_threshold == Some(threshold);

    let mut state = serde_json::Map::new();
    state.insert("cwd".to_string(), Value::String(cwd.to_string()));
    state.insert(
        "used".to_string(),
        match used {
            Some(u) => serde_json::Number::from_f64(u)
                .map(Value::Number)
                .unwrap_or(Value::Null),
            None => Value::Null,
        },
    );
    state.insert(
        "time".to_string(),
        serde_json::Number::from_f64(now)
            .map(Value::Number)
            .unwrap_or(Value::Null),
    );
    state.insert("threshold".to_string(), Value::Number(threshold.into()));
    state.insert("notified".to_string(), Value::Bool(notified));

    let body = serde_json::to_string(&state).ok()?;
    write_text_atomic(&path, &body).ok()?;
    used
}

pub fn record_window(payload: &Value) {
    let limits = match payload.get("rate_limits").and_then(|v| v.as_object()) {
        Some(l) => l,
        None => return,
    };
    let five = match limits.get("five_hour").and_then(|v| v.as_object()) {
        Some(f) => f,
        None => return,
    };
    let resets_at = match five.get("resets_at") {
        Some(r) => r,
        None => return,
    };
    let stamp: i64 = if let Some(n) = resets_at.as_i64() {
        n
    } else if let Some(f) = resets_at.as_f64() {
        f as i64
    } else if let Some(s) = resets_at.as_str() {
        if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
            dt.timestamp()
        } else {
            return;
        }
    } else {
        return;
    };

    let home = match paths::home_dir() {
        Some(h) => h,
        None => return,
    };
    let state_base = std::env::var("XDG_STATE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home.join(".local").join("state"));
    let window_path = state_base.join("acg").join("window-claude.json");

    if let Ok(content) = std::fs::read_to_string(&window_path) {
        if let Ok(val) = serde_json::from_str::<Value>(&content) {
            if val.get("resets_at").and_then(|v| v.as_i64()) == Some(stamp) {
                return;
            }
        }
    }

    if let Some(parent) = window_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let body = format!("{{\"resets_at\": {stamp}}}\n");
    let _ = std::fs::write(&window_path, body);
}

fn unlink_file(path: &Path) -> Result<(), String> {
    safety::assert_plain_path(path, Some(false))?;
    std::fs::remove_file(path)
        .map_err(|e| format!("Failed to remove {}: {e}", path.display()))?;
    Ok(())
}

pub fn reminder(payload: &Value) -> Option<Value> {
    let event = payload.get("hook_event_name").and_then(|v| v.as_str())?;
    if !EVENTS.contains(&event) {
        return None;
    }
    if let Some(agent_id) = payload.get("agent_id") {
        if !agent_id.is_null() && agent_id.as_str() != Some("") {
            return None;
        }
    }

    let _guard = apply_lock(Duration::from_millis(200)).ok()?;
    let path = session_path(payload).ok()?;
    if event == "PreCompact" || event == "SessionEnd" {
        if path.exists() {
            let _ = unlink_file(&path);
        }
        return None;
    }

    let settings = reminder_status();
    if !settings.installed {
        return None;
    }

    let mut state = read_state(&path);
    let payload_cwd = payload.get("cwd").and_then(|v| v.as_str())?;
    if state.get("cwd").and_then(|v| v.as_str()) != Some(payload_cwd) {
        return None;
    }
    if state
        .get("notified")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        return None;
    }
    if state.get("threshold").and_then(|v| v.as_i64()) != Some(settings.threshold) {
        return None;
    }

    let used = state.get("used").and_then(|v| v.as_f64())?;
    if !used.is_finite() || used < settings.threshold as f64 || used > 100.0 {
        return None;
    }

    let timestamp = state.get("time").and_then(|v| v.as_f64())?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    if !timestamp.is_finite() || !(0.0..=MAX_AGE).contains(&(now - timestamp)) {
        return None;
    }

    state.insert("notified".to_string(), Value::Bool(true));
    let body = serde_json::to_string(&state).ok()?;
    write_text_atomic(&path, &body).ok()?;

    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": format!(
                "本 session 最近的 context 使用率為 {}%，已達交接提醒門檻 {}%。建議提醒使用者準備 /acg handoff，交接應包含已完成事項、目前阻礙與下一步。這只是提醒，尚未建立或更新交接；是否寫入由使用者決定。",
                format_g(used),
                settings.threshold
            )
        }
    }))
}

fn run_shell_command(command: &str, input: &str, timeout: Duration) -> Option<String> {
    #[cfg(not(windows))]
    let (prog, args) = {
        let sh = std::env::var("CLAUDE_CODE_GIT_BASH_PATH").unwrap_or_else(|_| "sh".to_string());
        (sh, vec!["-c".to_string(), command.to_string()])
    };

    #[cfg(windows)]
    let (prog, args) = {
        if let Ok(p) = std::env::var("CLAUDE_CODE_GIT_BASH_PATH") {
            (p, vec!["-c".to_string(), command.to_string()])
        } else if let Ok(pf) = std::env::var("ProgramFiles") {
            let candidate = PathBuf::from(pf).join("Git").join("bin").join("bash.exe");
            if candidate.is_file() {
                (
                    candidate.to_string_lossy().into_owned(),
                    vec!["-c".to_string(), command.to_string()],
                )
            } else {
                (
                    "powershell".to_string(),
                    vec![
                        "-NoProfile".to_string(),
                        "-Command".to_string(),
                        command.to_string(),
                    ],
                )
            }
        } else {
            (
                "powershell".to_string(),
                vec![
                    "-NoProfile".to_string(),
                    "-Command".to_string(),
                    command.to_string(),
                ],
            )
        }
    };

    let mut child = Command::new(&prog)
        .args(&args)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .ok()?;

    if let Some(mut stdin) = child.stdin.take() {
        use std::io::Write;
        let _ = stdin.write_all(input.as_bytes());
    }

    let (tx, rx) = std::sync::mpsc::channel();
    let stdout = child.stdout.take();
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut stream) = stdout {
            use std::io::Read;
            let _ = stream.read_to_end(&mut buf);
        }
        let _ = tx.send(buf);
    });

    match rx.recv_timeout(timeout) {
        Ok(bytes) => {
            let _ = child.wait();
            let _ = reader.join();
            Some(String::from_utf8_lossy(&bytes).into_owned())
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            None
        }
    }
}

pub fn run_statusline(args: &[String]) -> i32 {
    if args.len() != 2 {
        return 0;
    }
    let original = match decode_original(&args[0]) {
        Some(o) => o,
        None => return 0,
    };
    let threshold = match args[1].parse::<i64>().ok().and_then(check_threshold) {
        Some(t) => t,
        None => return 0,
    };

    use std::io::Read;
    let mut buf = Vec::new();
    let _ = std::io::stdin()
        .take(MAX_INPUT + 1)
        .read_to_end(&mut buf);
    let raw_len = buf.len() as u64;
    let raw = String::from_utf8_lossy(&buf).into_owned();

    let mut used: Option<f64> = None;
    if raw_len <= MAX_INPUT {
        if let Ok(payload) = serde_json::from_str::<Value>(&raw) {
            if payload.is_object() {
                used = record(&payload, threshold);
                record_window(&payload);
            }
        }
    }

    let should_remind = used.is_some() && used.unwrap() >= threshold as f64;
    if let Some(orig_cmd) = original {
        if let Some(stdout) = run_shell_command(&orig_cmd, &raw, Duration::from_secs(3)) {
            use std::io::Write;
            let _ = std::io::stdout().write_all(stdout.as_bytes());
            if should_remind && !stdout.ends_with('\n') {
                println!();
            }
        }
    }

    if should_remind {
        println!("Context {}% · 建議準備 /acg handoff", format_g(used.unwrap()));
    }
    0
}

pub fn run_hook(args: &[String]) -> i32 {
    if !args.is_empty() {
        return 0;
    }
    use std::io::Read;
    let mut buf = Vec::new();
    let _ = std::io::stdin()
        .take(MAX_INPUT + 1)
        .read_to_end(&mut buf);
    if (buf.len() as u64) > MAX_INPUT {
        return 0;
    }
    let payload: Value = match serde_json::from_slice::<Value>(&buf) {
        Ok(v) if v.is_object() => v,
        _ => return 0,
    };
    if let Some(output) = reminder(&payload) {
        println!("{}", serde_json::to_string(&output).unwrap_or_default());
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_g() {
        assert_eq!(format_g(75.0), "75");
        assert_eq!(format_g(75.5), "75.5");
        assert_eq!(format_g(0.0), "0");
        assert_eq!(format_g(100.0), "100");
    }

    #[test]
    fn test_urlsafe_b64decode() {
        let decoded = urlsafe_b64decode("bnVsbA==").unwrap();
        assert_eq!(decoded, b"null");

        let orig = json!({"type": "command", "command": "echo 1"});
        let json_str = serde_json::to_string(&orig).unwrap();
        // Base64 encode in python style
        use std::io::Write;
        let mut enc = Vec::new();
        let _ = write!(enc, "{json_str}");
        // Decode
        assert!(decode_original("bnVsbA==").unwrap().is_none());
    }

    #[test]
    fn test_percentage() {
        let mut p = json!({
            "context_window": {
                "used_percentage": 75
            }
        });
        assert_eq!(percentage(&p), Some(75.0));

        p = json!({
            "context_window": {
                "remaining_percentage": 20
            }
        });
        assert_eq!(percentage(&p), Some(80.0));

        p = json!({
            "context_window": {
                "used_percentage": null,
                "remaining_percentage": 20
            }
        });
        assert_eq!(percentage(&p), None);

        p = json!({
            "context_window": {
                "current_usage": null,
                "used_percentage": 75
            }
        });
        assert_eq!(percentage(&p), None);

        for invalid in [
            json!(true),
            json!(-1),
            json!(101),
            json!("75"),
        ] {
            let inv_p = json!({
                "context_window": {
                    "used_percentage": invalid
                }
            });
            assert_eq!(percentage(&inv_p), None);
        }
    }
}


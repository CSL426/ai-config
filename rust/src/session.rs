use std::path::PathBuf;

pub fn session_id() -> String {
    std::env::var("CLAUDE_CODE_SESSION_ID")
        .unwrap_or_default()
        .trim()
        .to_string()
}

pub fn session_record() -> Option<serde_json::Value> {
    let me = session_id();
    if me.is_empty() {
        return None;
    }
    let sessions_dir = crate::paths::claude_home()?.join("sessions");
    if !sessions_dir.is_dir() {
        return None;
    }
    let entries = std::fs::read_dir(sessions_dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("json") {
            if let Ok(text) = std::fs::read_to_string(&path) {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    if val.get("sessionId").and_then(|s| s.as_str()) == Some(&me) {
                        return Some(val);
                    }
                }
            }
        }
    }
    None
}

pub fn session_name() -> String {
    session_record()
        .and_then(|rec| rec.get("name").and_then(|v| v.as_str()).map(|s| s.trim().to_string()))
        .unwrap_or_default()
}

pub fn session_home() -> Option<PathBuf> {
    session_record()
        .and_then(|rec| rec.get("cwd").and_then(|v| v.as_str()).map(|s| PathBuf::from(s.trim())))
        .filter(|p| p.is_dir())
}

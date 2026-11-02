use std::path::PathBuf;

pub fn is_windows_mode() -> bool {
    std::env::var("AI_CONFIG_PLATFORM")
        .map(|v| v == "windows")
        .unwrap_or(cfg!(windows))
}

pub fn entrypoint() -> String {
    if let Ok(ep) = std::env::var("AI_CONFIG_ENTRYPOINT") {
        let trimmed = ep.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    if is_windows_mode() {
        r".\ai-config.ps1".to_string()
    } else {
        "./ai-config.sh".to_string()
    }
}

pub fn home_dir() -> Option<PathBuf> {
    if let Ok(h) = std::env::var("HOME") {
        let trimmed = h.trim();
        if !trimmed.is_empty() {
            return Some(PathBuf::from(trimmed));
        }
    }
    if let Ok(u) = std::env::var("USERPROFILE") {
        let trimmed = u.trim();
        if !trimmed.is_empty() {
            return Some(PathBuf::from(trimmed));
        }
    }
    None
}

pub fn claude_home() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".claude"))
}

pub fn find_config_path() -> Option<PathBuf> {
    if let Ok(c) = std::env::var("AI_CONFIG_CONFIG") {
        let trimmed = c.trim();
        if !trimmed.is_empty() {
            return Some(PathBuf::from(trimmed));
        }
    }
    let home = home_dir()?;
    if is_windows_mode() {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return Some(PathBuf::from(appdata).join("ai-config").join("config.json"));
        }
    }
    #[cfg(target_os = "macos")]
    {
        return Some(home.join("Library").join("Application Support").join("ai-config").join("config.json"));
    }
    if let Ok(xdg) = std::env::var("XDG_CONFIG_HOME") {
        return Some(PathBuf::from(xdg).join("ai-config").join("config.json"));
    }
    Some(home.join(".config").join("ai-config").join("config.json"))
}

pub fn data_dir() -> PathBuf {
    if let Ok(val) = std::env::var("AI_CONFIG_REPO") {
        let trimmed = val.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Some(config_path) = find_config_path() {
        if config_path.is_file() {
            if let Ok(text) = std::fs::read_to_string(&config_path) {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(repo) = val.get("data_repo").and_then(|v| v.as_str()) {
                        let trimmed = repo.trim();
                        if !trimmed.is_empty() {
                            return PathBuf::from(trimmed);
                        }
                    }
                }
            }
        }
    }
    let home = home_dir().unwrap_or_else(|| PathBuf::from("."));
    home.join(".acg").join("data")
}

pub fn memory_dir() -> PathBuf {
    data_dir().join("memory")
}

pub fn handoff_dir() -> PathBuf {
    memory_dir().join("handoff")
}

pub fn archive_dir() -> PathBuf {
    handoff_dir().join("archive")
}

use std::path::{Path, PathBuf};

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
        Some(home.join("Library").join("Application Support").join("ai-config").join("config.json"))
    }
    #[cfg(not(target_os = "macos"))]
    {
        if let Ok(xdg) = std::env::var("XDG_CONFIG_HOME") {
            return Some(PathBuf::from(xdg).join("ai-config").join("config.json"));
        }
        Some(home.join(".config").join("ai-config").join("config.json"))
    }
}

pub fn strip_verbatim_prefix(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = s.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path.to_path_buf()
    }
}

fn normalize_path_components(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for c in path.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            c => out.push(c.as_os_str()),
        }
    }
    out
}

pub fn resolve_like_python(path: &Path) -> PathBuf {
    let path = if path.starts_with("~") || path == Path::new("~") {
        if let Some(home) = home_dir() {
            if path == Path::new("~") {
                home
            } else if let Ok(stripped) = path.strip_prefix("~") {
                home.join(stripped)
            } else {
                path.to_path_buf()
            }
        } else {
            path.to_path_buf()
        }
    } else {
        path.to_path_buf()
    };

    let path = if path.is_relative() {
        std::env::current_dir()
            .map(|cwd| cwd.join(&path))
            .unwrap_or(path)
    } else {
        path
    };

    let mut existing = path.clone();
    let mut tail = Vec::new();
    loop {
        if let Ok(canon) = std::fs::canonicalize(&existing) {
            let mut resolved = strip_verbatim_prefix(&canon);
            for part in tail.into_iter().rev() {
                resolved.push(part);
            }
            return normalize_path_components(&resolved);
        }
        if let Some(file_name) = existing.file_name() {
            tail.push(file_name.to_os_string());
            if let Some(parent) = existing.parent() {
                if parent == existing || parent.as_os_str().is_empty() {
                    break;
                }
                existing = parent.to_path_buf();
            } else {
                break;
            }
        } else {
            break;
        }
    }

    normalize_path_components(&strip_verbatim_prefix(&path))
}

pub fn configured_data_repo() -> Result<Option<PathBuf>, String> {
    if let Ok(val) = std::env::var("AI_CONFIG_REPO") {
        let trimmed = val.trim();
        if !trimmed.is_empty() {
            return Ok(Some(resolve_like_python(Path::new(trimmed))));
        }
    }

    if let Some(config_path) = find_config_path() {
        if config_path.exists() {
            let text = std::fs::read_to_string(&config_path)
                .map_err(|e| format!("Cannot read configuration file {}: {e}", config_path.display()))?;
            let val = serde_json::from_str::<serde_json::Value>(&text)
                .map_err(|e| format!("Cannot read configuration file {}: {e}", config_path.display()))?;
            let obj = val.as_object().ok_or_else(|| {
                format!("Configuration file must contain a JSON object: {}", config_path.display())
            })?;
            if let Some(repo_val) = obj.get("data_repo") {
                let repo_str = repo_val.as_str().ok_or_else(|| {
                    format!("data_repo must be a string in {}", config_path.display())
                })?;
                let trimmed = repo_str.trim();
                if !trimmed.is_empty() {
                    return Ok(Some(resolve_like_python(Path::new(trimmed))));
                }
            }
        }
    }
    Ok(None)
}

pub fn fallback_data_repo() -> PathBuf {
    let home = home_dir().unwrap_or_else(|| PathBuf::from("."));
    let default_home_repo = resolve_like_python(&home.join(".acg").join("data"));
    let previous_default_repo = resolve_like_python(&home.join("ai-config").join("data"));
    let legacy_home_repo = resolve_like_python(&home.join("ai-config"));

    let has_any_tool = |p: &Path| {
        p.join("claude").is_dir() || p.join("codex").is_dir() || p.join("agy").is_dir()
    };

    if has_any_tool(&default_home_repo) {
        default_home_repo
    } else if has_any_tool(&previous_default_repo) {
        previous_default_repo
    } else if has_any_tool(&legacy_home_repo) {
        legacy_home_repo
    } else {
        default_home_repo
    }
}

pub fn data_dir() -> Result<PathBuf, String> {
    match configured_data_repo()? {
        Some(repo) => Ok(repo),
        None => Ok(fallback_data_repo()),
    }
}

pub fn raw_data_dir() -> PathBuf {
    data_dir().unwrap_or_else(|_| fallback_data_repo())
}

pub fn memory_dir() -> PathBuf {
    raw_data_dir().join("memory")
}

pub fn handoff_dir() -> PathBuf {
    memory_dir().join("handoff")
}

pub fn archive_dir() -> PathBuf {
    handoff_dir().join("archive")
}

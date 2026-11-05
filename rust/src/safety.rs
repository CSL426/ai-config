use std::path::{Path, PathBuf};

pub fn is_reparse_point(path: &Path) -> bool {
    #[cfg(unix)]
    {
        std::fs::symlink_metadata(path)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false)
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        std::fs::symlink_metadata(path)
            .map(|m| {
                m.file_type().is_symlink()
                    || (m.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
            })
            .unwrap_or(false)
    }
}

pub fn normalize_lexically(path: &Path) -> PathBuf {
    let mut normalized = if path.is_absolute() {
        PathBuf::new()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    };

    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                normalized.pop();
            }
            c => normalized.push(c.as_os_str()),
        }
    }
    normalized
}

pub fn assert_plain_path(path: &Path, directory: Option<bool>) -> Result<(), String> {
    let normalized = normalize_lexically(path);
    let mut parents: Vec<PathBuf> = Vec::new();
    let mut curr = normalized.as_path();
    while let Some(parent) = curr.parent() {
        parents.push(parent.to_path_buf());
        curr = parent;
    }
    parents.reverse();

    for parent in parents {
        if is_reparse_point(&parent) {
            return Err(format!("Refusing reparse point parent: {}", parent.display()));
        }
        if parent.exists() && !parent.is_dir() {
            return Err(format!("Expected directory: {}", parent.display()));
        }
    }

    if is_reparse_point(&normalized) {
        return Err(format!("Refusing reparse point: {}", normalized.display()));
    }

    if !normalized.exists() {
        return Ok(());
    }

    let meta = std::fs::symlink_metadata(&normalized)
        .map_err(|e| format!("Failed to read metadata {}: {e}", normalized.display()))?;
    let file_type = meta.file_type();
    if file_type.is_symlink() {
        return Err(format!("Refusing reparse point: {}", normalized.display()));
    }

    let valid = match directory {
        Some(true) => file_type.is_dir(),
        Some(false) => file_type.is_file(),
        None => file_type.is_dir() || file_type.is_file(),
    };

    if !valid {
        return Err(format!("Unexpected path type: {}", normalized.display()));
    }

    Ok(())
}

pub fn assert_root_not_reparse(path: &Path, label: &str) -> Result<(), String> {
    if is_reparse_point(path) {
        return Err(format!("Refusing reparse point {label}: {}", path.display()));
    }
    Ok(())
}

pub fn assert_safe_write_target(path: &Path) -> Result<(), String> {
    if is_reparse_point(path) {
        return Err(format!("Refusing reparse point file destination: {}", path.display()));
    }
    if let Some(parent) = path.parent() {
        if is_reparse_point(parent) {
            return Err(format!("Refusing reparse point parent destination: {}", parent.display()));
        }
    }
    Ok(())
}

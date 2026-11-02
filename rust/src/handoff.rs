use std::collections::HashMap;
use std::path::{Path, PathBuf};
use chrono::{DateTime, Utc};
use regex::Regex;

use crate::paths::{archive_dir, handoff_dir};
pub use crate::project::home_project;
use crate::safety::assert_plain_path;
use crate::session::{session_id, session_name};

pub const ARCHIVE_DIR_NAME: &str = "archive";
pub const ARCHIVE_AFTER_DAYS: u64 = 30;
pub const STALE_AFTER_HOURS: f64 = 24.0;
pub const OPEN: &str = "open";
pub const DONE: &str = "done";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Handoff {
    pub path: PathBuf,
    pub thread: String,
    pub project: String,
    pub state: String,
    pub author: String,
    pub claimed_by: String,
    pub session_name: String,
    pub claimed_name: String,
    pub created: String,
    pub updated: String,
    pub body: String,
}

impl Handoff {
    pub fn name(&self) -> String {
        self.path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string()
    }
}

pub fn slug(text: &str) -> String {
    let unsafe_re = Regex::new(r#"[\x00-\x1f/\\:*?"<>|]+"#).unwrap();
    let cleaned = unsafe_re.replace_all(text.trim(), "-");
    let trimmed = cleaned.trim_matches(['-', '.', ' ']);
    if trimmed.is_empty() || trimmed == "." || trimmed == ".." {
        "thread".to_string()
    } else {
        trimmed.to_string()
    }
}

pub fn parse(path: &Path) -> Result<Option<Handoff>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    let raw_bytes = std::fs::read(path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    let mut text = String::from_utf8_lossy(&raw_bytes).to_string();
    if text.starts_with('\u{feff}') {
        text.remove(0);
    }
    let text = text.replace("\r\n", "\n");

    const FRONT: &str = "---\n";
    if !text.starts_with(FRONT) {
        return Ok(None);
    }
    let rest = &text[FRONT.len()..];
    let (front, body) = match rest.find(FRONT) {
        Some(idx) => (&rest[..idx], &rest[idx + FRONT.len()..]),
        None => return Ok(None),
    };

    let field_re = Regex::new(r"(?m)^([a-z_]+):[ \t]*(.*)$").unwrap();
    let mut fields: HashMap<String, String> = HashMap::new();
    for cap in field_re.captures_iter(front) {
        let key = cap.get(1).unwrap().as_str().to_string();
        let val = cap.get(2).unwrap().as_str().to_string();
        fields.insert(key, val);
    }

    let raw_state = fields.get("state").map(|s| s.trim()).unwrap_or("");
    let state = if raw_state == "claimed" {
        OPEN.to_string()
    } else if raw_state == OPEN || raw_state == DONE {
        raw_state.to_string()
    } else {
        OPEN.to_string()
    };

    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string();

    let updated = fields.get("updated").map(|s| s.trim()).unwrap_or("").to_string();
    let created = fields
        .get("created")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .unwrap_or(&updated)
        .to_string();

    let thread = fields
        .get("thread")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .unwrap_or(&stem)
        .to_string();

    Ok(Some(Handoff {
        path: path.to_path_buf(),
        thread,
        project: fields.get("project").map(|s| s.trim()).unwrap_or("").to_string(),
        state,
        author: fields.get("author").map(|s| s.trim()).unwrap_or("").to_string(),
        claimed_by: fields.get("claimed_by").map(|s| s.trim()).unwrap_or("").to_string(),
        session_name: fields.get("session_name").map(|s| s.trim()).unwrap_or("").to_string(),
        claimed_name: fields.get("claimed_name").map(|s| s.trim()).unwrap_or("").to_string(),
        created,
        updated,
        body: body.trim().to_string(),
    }))
}

pub fn render(note: &Handoff) -> String {
    let mut out = String::from("---\n");
    out.push_str(&format!("thread: {}\n", note.thread));
    out.push_str(&format!("project: {}\n", note.project));
    out.push_str(&format!("state: {}\n", note.state));
    out.push_str(&format!("author: {}\n", note.author));
    if !note.claimed_by.is_empty() {
        out.push_str(&format!("claimed_by: {}\n", note.claimed_by));
    }
    if !note.session_name.is_empty() {
        out.push_str(&format!("session_name: {}\n", note.session_name));
    }
    if !note.claimed_name.is_empty() {
        out.push_str(&format!("claimed_name: {}\n", note.claimed_name));
    }
    out.push_str(&format!("created: {}\n", note.created));
    out.push_str(&format!("updated: {}\n", note.updated));
    out.push_str("---\n\n");
    out.push_str(note.body.trim());
    out.push('\n');
    out
}

pub fn now_utc() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

pub fn parse_timestamp(stamp: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(stamp)
        .map(|dt| dt.with_timezone(&Utc))
        .or_else(|_| {
            chrono::NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M:%SZ")
                .map(|ndt| ndt.and_utc())
        })
        .ok()
}

pub fn age_in_days(created: &str) -> u64 {
    let Some(opened) = parse_timestamp(created) else {
        return 0;
    };
    let now = Utc::now();
    let days = (now - opened).num_days();
    if days > 0 { days as u64 } else { 0 }
}

pub fn hours_since(stamp: &str) -> Option<f64> {
    let then = parse_timestamp(stamp)?;
    let now = Utc::now();
    let hours = (now - then).num_milliseconds() as f64 / 3_600_000.0;
    Some(hours.max(0.0))
}

pub fn age_text(note: &Handoff) -> String {
    let age = age_in_days(&note.created);
    if age == 0 {
        return String::new();
    }
    let opened = format!("{age} 天前開的");
    let idle = hours_since(&note.updated);
    if note.updated == note.created || idle.is_none() {
        return opened;
    }
    let idle = idle.unwrap();
    if idle < 1.0 {
        format!("{opened},剛更新")
    } else if idle < 24.0 {
        format!("{opened},{} 小時前更新", idle as u64)
    } else {
        format!("{opened},{} 天前更新", (idle / 24.0) as u64)
    }
}

pub fn is_stale(note: &Handoff) -> bool {
    hours_since(&note.updated).is_some_and(|h| h >= STALE_AFTER_HOURS)
}

pub fn short_id(value: &str, keep: usize) -> &str {
    if value.len() <= keep {
        return value;
    }
    let head = &value[..keep];
    if head.contains('-') || head.contains('_') {
        head.trim_end_matches(['-', '_'])
    } else {
        head
    }
}

pub fn taker(note: &Handoff) -> String {
    if !note.claimed_name.is_empty() {
        note.claimed_name.clone()
    } else if !note.claimed_by.is_empty() {
        short_id(&note.claimed_by, 8).to_string()
    } else {
        String::new()
    }
}

pub fn mark_label(heading: &str) -> &'static str {
    if heading.starts_with("next") || heading.starts_with("下一步") {
        "還剩:"
    } else {
        "未定:"
    }
}

pub fn summary(body: &str, limit: usize) -> String {
    let heading_re = Regex::new(r"(?m)^#{1,3}\s*(.+?)\s*$").unwrap();
    let matches: Vec<_> = heading_re.find_iter(body).collect();
    let mut sections: Vec<(String, String)> = Vec::new();

    for (i, m) in matches.iter().enumerate() {
        let caps = heading_re.captures(m.as_str()).unwrap();
        let heading_title = caps.get(1).unwrap().as_str().trim().to_lowercase();
        let start = m.end();
        let end = if i + 1 < matches.len() {
            matches[i + 1].start()
        } else {
            body.len()
        };
        let text = body[start..end].trim().to_string();

        if let Some(pos) = sections.iter().position(|(k, _)| k == &heading_title) {
            sections[pos].1 = text;
        } else {
            sections.push((heading_title, text));
        }
    }

    let wanted = ["next", "下一步", "unknowns", "待決定", "待辦"];
    for w in wanted {
        for (name, text) in &sections {
            if !name.starts_with(w) || text.is_empty() {
                continue;
            }
            if let Some(first_line) = text.lines().next() {
                let first = first_line.replace("**", "");
                let first = first.trim_start_matches(['-', '*', ' ']).trim();
                if !first.is_empty() {
                    let full = format!("{}{first}", mark_label(name));
                    return full.chars().take(limit).collect();
                }
            }
        }
    }

    for line in body.lines() {
        let trimmed = line.trim();
        if !trimmed.is_empty() && !heading_re.is_match(trimmed) {
            return trimmed.chars().take(limit).collect();
        }
    }

    String::new()
}

pub fn write_text_atomic(path: &Path, content: &str) -> Result<(), String> {
    assert_plain_path(path, Some(false))?;
    let parent = path.parent().ok_or_else(|| "Path has no parent".to_string())?;
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("Failed to create directory {}: {e}", parent.display()))?;

    let previous = if path.exists() {
        std::fs::read(path).ok()
    } else {
        None
    };

    let filename = path.file_name().and_then(|n| n.to_str()).unwrap_or("tmp");
    let prefix = format!(".{filename}.");
    let mut temp = tempfile::Builder::new()
        .prefix(&prefix)
        .tempfile_in(parent)
        .map_err(|e| format!("Failed to create temporary file: {e}"))?;

    use std::io::Write;
    temp.write_all(content.as_bytes())
        .map_err(|e| format!("Failed to write content: {e}"))?;
    temp.flush().map_err(|e| format!("Failed to flush temporary file: {e}"))?;

    assert_plain_path(path, Some(false))?;

    let current = if path.exists() {
        std::fs::read(path).ok()
    } else {
        None
    };

    if current != previous {
        return Err(format!("File changed during update: {}", path.display()));
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = path.metadata() {
            let _ = std::fs::set_permissions(temp.path(), meta.permissions());
        } else {
            let _ = std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o666));
        }
    }

    temp.persist(path)
        .map_err(|e| format!("Failed to replace {}: {e}", path.display()))?;
    Ok(())
}

pub fn load_all(project: Option<&str>) -> Result<Vec<Handoff>, String> {
    let root = handoff_dir();
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let mut notes = Vec::new();
    let mut entries: Vec<PathBuf> = std::fs::read_dir(&root)
        .map_err(|e| format!("Failed to read {}: {e}", root.display()))?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("md"))
        .collect();
    entries.sort();

    for path in entries {
        if crate::safety::is_reparse_point(&path) || !path.is_file() {
            continue;
        }
        if let Ok(Some(note)) = parse(&path) {
            if let Some(proj) = project {
                if !proj.is_empty() && note.project != proj {
                    continue;
                }
            }
            notes.push(note);
        }
    }
    // Python sorts notes with key=lambda n: n.updated, reverse=True
    // Stable sort preserves tie-breaker
    notes.sort_by(|a, b| b.updated.cmp(&a.updated));
    Ok(notes)
}

pub fn archive_finished(older_than_days: u64) -> Result<Vec<String>, String> {
    let root = handoff_dir();
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let mut moved = Vec::new();
    let notes = load_all(None)?;
    let arch_dir = archive_dir();

    for note in notes {
        if age_in_days(&note.updated) < older_than_days {
            continue;
        }
        std::fs::create_dir_all(&arch_dir)
            .map_err(|e| format!("Failed to create archive directory {}: {e}", arch_dir.display()))?;

        let file_name = note.path.file_name().unwrap();
        let stem = note.path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        let mut target = arch_dir.join(file_name);

        if target.exists() {
            let stamp = if note.updated.len() >= 10 {
                note.updated[..10].to_string()
            } else {
                let now = now_utc();
                if now.len() >= 10 {
                    now[..10].to_string()
                } else {
                    "archive".to_string()
                }
            };
            target = arch_dir.join(format!("{stem}.{stamp}.md"));
            let mut spare = 2;
            while target.exists() {
                target = arch_dir.join(format!("{stem}.{stamp}-{spare}.md"));
                spare += 1;
            }
        }
        assert_plain_path(&target, Some(false))?;
        std::fs::rename(&note.path, &target)
            .map_err(|e| format!("Failed to move {} to {}: {e}", note.path.display(), target.display()))?;
        moved.push(note.thread);
    }
    Ok(moved)
}

fn taken_by_someone_else(note: Option<&Handoff>) -> bool {
    let Some(note) = note else {
        return false;
    };
    if note.state != DONE || (note.claimed_by.is_empty() && note.claimed_name.is_empty()) {
        return false;
    }
    let mine_id = session_id();
    let mine_name = session_name();
    if !note.claimed_by.is_empty() && note.claimed_by == mine_id {
        return false;
    }
    if !note.claimed_name.is_empty() && note.claimed_name == mine_name {
        return false;
    }
    true
}

fn refuse_if_taken(note: Option<&Handoff>, thread: &str) -> Result<(), String> {
    if taken_by_someone_else(note) {
        let note = note.unwrap();
        return Err(format!(
            "這條線已經被 {} 接走了({}):{}。覆寫會把它變回待接手、蓋掉接手紀錄;要記自己的進度就換個名稱寫新的一條,確定要覆寫再加 --force",
            taker(note),
            note.updated,
            thread
        ));
    }
    Ok(())
}

pub fn load_or_fail(name: &str) -> Result<Handoff, String> {
    let root = handoff_dir();
    let path = root.join(format!("{}.md", slug(name)));
    assert_plain_path(&path, Some(false))?;
    if !path.is_file() {
        return Err(format!("沒有這則交接:{name}"));
    }
    match parse(&path)? {
        Some(note) => Ok(note),
        None => {
            let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            Err(format!("交接格式不正確:{fname}"))
        }
    }
}

pub fn named_elsewhere(project: &str) -> Result<Vec<Handoff>, String> {
    let mine = session_name();
    if mine.is_empty() {
        return Ok(Vec::new());
    }
    let all = load_all(None)?;
    Ok(all
        .into_iter()
        .filter(|n| n.state != DONE && n.session_name == mine && n.project != project)
        .collect())
}

pub fn own_thread() -> Result<Handoff, String> {
    let mine = session_name();
    if mine.is_empty() {
        return Err("讀不到這個 session 的名稱,請指定要認領的工作線".to_string());
    }
    let project = home_project(None);
    let mut candidates: Vec<Handoff> = load_all(Some(&project))?
        .into_iter()
        .filter(|n| n.state != DONE && n.session_name == mine)
        .collect();
    if candidates.is_empty() {
        candidates = named_elsewhere(&project)?
            .into_iter()
            .filter(|n| n.state != DONE && n.session_name == mine)
            .collect();
    }
    if candidates.is_empty() {
        return Err(format!("沒有 session「{mine}」留下的工作線,請指定要認領的工作線"));
    }
    if candidates.len() > 1 {
        let names = candidates
            .iter()
            .map(|n| format!("{}({})", n.thread, n.project))
            .collect::<Vec<_>>()
            .join("、");
        return Err(format!("session「{mine}」留下不只一條線:{names},請指定"));
    }
    Ok(candidates.remove(0))
}

pub fn write(
    thread: &str,
    body: &str,
    cwd: Option<&Path>,
    force: bool,
) -> Result<Handoff, String> {
    if thread.trim().is_empty() {
        return Err("交接需要一個看得懂的工作線名稱".to_string());
    }
    if body.trim().is_empty() {
        return Err("交接內容不能是空的".to_string());
    }
    let root = handoff_dir();
    std::fs::create_dir_all(&root)
        .map_err(|e| format!("Failed to create {}: {e}", root.display()))?;

    let slug_name = slug(thread);
    let path = root.join(format!("{slug_name}.md"));
    assert_plain_path(&path, Some(false))?;

    let existing = if path.is_file() {
        parse(&path)?
    } else {
        None
    };

    if !force {
        refuse_if_taken(existing.as_ref(), thread)?;
    }

    let now = now_utc();
    let author = {
        let sid = session_id();
        if !sid.is_empty() {
            sid
        } else if let Some(ref ex) = existing {
            ex.author.clone()
        } else {
            String::new()
        }
    };

    let sess_name = {
        let sname = session_name();
        if !sname.is_empty() {
            sname
        } else if let Some(ref ex) = existing {
            ex.session_name.clone()
        } else {
            String::new()
        }
    };

    let created = if let Some(ref ex) = existing {
        ex.created.clone()
    } else {
        now.clone()
    };

    let note = Handoff {
        path: path.clone(),
        thread: thread.trim().to_string(),
        project: home_project(cwd),
        state: OPEN.to_string(),
        author,
        claimed_by: String::new(),
        session_name: sess_name,
        claimed_name: String::new(),
        created,
        updated: now,
        body: body.to_string(),
    };

    if !force {
        let recheck = if path.is_file() { parse(&path)? } else { None };
        refuse_if_taken(recheck.as_ref(), thread)?;
    }

    write_text_atomic(&path, &render(&note))?;
    Ok(note)
}

pub fn claim(name: Option<&str>) -> Result<Handoff, String> {
    let mut note = match name {
        Some(n) if !n.trim().is_empty() => load_or_fail(n.trim())?,
        _ => own_thread()?,
    };
    let display_name = if let Some(n) = name {
        if !n.trim().is_empty() {
            n.trim().to_string()
        } else {
            note.thread.clone()
        }
    } else {
        note.thread.clone()
    };

    if note.state == DONE {
        let who = taker(&note);
        if !who.is_empty() {
            return Err(format!("這條線已經被 {who} 接走了:{display_name}"));
        }
        return Err(format!("這則交接已結束:{display_name}"));
    }

    note.state = DONE.to_string();
    note.claimed_by = session_id();
    note.claimed_name = session_name();
    note.updated = now_utc();
    write_text_atomic(&note.path, &render(&note))?;
    Ok(note)
}

pub fn done(name: &str) -> Result<Handoff, String> {
    let mut note = load_or_fail(name)?;
    note.state = DONE.to_string();
    note.claimed_by.clear();
    note.claimed_name.clear();
    note.updated = now_utc();
    write_text_atomic(&note.path, &render(&note))?;
    Ok(note)
}

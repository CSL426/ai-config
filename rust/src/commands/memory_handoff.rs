use std::path::{Path, PathBuf};
use crate::console::{log_error, log_info, log_success};
use crate::handoff::{
    archive_finished, claim, done, home_project, is_stale, load_all, named_elsewhere, summary,
    write, ARCHIVE_AFTER_DAYS, ARCHIVE_DIR_NAME, DONE,
};
use crate::paths::entrypoint;

pub fn handoff_usage() -> String {
    format!(
        "Usage: {} memory handoff [list [專案路徑] | write <線> <內容> | claim [線] | done <線> | remind [status|enable [百分比]|disable]]",
        entrypoint()
    )
}

pub fn run_handoff(args: &[String], force: bool) -> i32 {
    let action = args.first().map(|s| s.as_str()).unwrap_or("list");
    let rest = if args.is_empty() { &[] } else { &args[1..] };

    if action == "--help" || action == "-h" {
        log_info(&handoff_usage());
        return 0;
    }

    let result = match action {
        "list" if rest.len() <= 1 => {
            let cwd = rest.first().map(PathBuf::from);
            handoff_list(cwd.as_deref())
        }
        "write" if rest.len() >= 2 => {
            let thread = &rest[0];
            let body = rest[1..].join(" ");
            match write(thread, &body, None, force) {
                Ok(note) => {
                    log_success(&format!("已記下交接:{}(專案 {})", note.thread, note.project));
                    log_info("接手:清空對話後用 /acg pickup,或直接說「接著做」");
                    let quoted = shlex::try_quote(&note.name())
                        .map(|c| c.into_owned())
                        .unwrap_or_else(|_| note.name());
                    log_info(&format!("沒有 plugin 時:{} memory handoff claim {quoted}", entrypoint()));
                    Ok(0)
                }
                Err(err) => Err(err),
            }
        }
        "claim" if rest.len() <= 1 => {
            let thread = rest.first().map(|s| s.as_str());
            match claim(thread) {
                Ok(note) => {
                    log_success(&format!("已接手:{}(這則交接已結案,收工時再寫新的)", note.thread));
                    if note.project != home_project(None) {
                        log_info(&format!("這條線記在專案 {},不是這個 session 的專案", note.project));
                    }
                    println!();
                    println!("{}", note.body);
                    Ok(0)
                }
                Err(err) => Err(err),
            }
        }
        "done" if rest.len() == 1 => {
            let thread = &rest[0];
            match done(thread) {
                Ok(note) => {
                    log_success(&format!("已結束:{}", note.thread));
                    Ok(0)
                }
                Err(err) => Err(err),
            }
        }
        "remind" => {
            Err("Command not yet implemented in Rust: memory handoff remind".to_string())
        }
        _ => {
            log_error(&handoff_usage());
            return 1;
        }
    };

    match result {
        Ok(code) => code,
        Err(err) => {
            log_error(&err);
            1
        }
    }
}

fn handoff_list(cwd: Option<&Path>) -> Result<i32, String> {
    if let Some(dir) = cwd {
        if !dir.is_dir() {
            return Err(format!("找不到這個專案目錄:{}", dir.display()));
        }
    }

    let archived = archive_finished(ARCHIVE_AFTER_DAYS)?;
    if !archived.is_empty() {
        log_info(&format!(
            "已把 {} 條結案或 {} 天沒人動的線移到 {}/:{}",
            archived.len(),
            ARCHIVE_AFTER_DAYS,
            ARCHIVE_DIR_NAME,
            archived.join("、")
        ));
    }

    let project = home_project(cwd);
    let notes = load_all(Some(&project))?
        .into_iter()
        .filter(|n| n.state != DONE)
        .collect::<Vec<_>>();

    let elsewhere = if cwd.is_none() {
        named_elsewhere(&project).unwrap_or_default()
    } else {
        Vec::new()
    };

    if notes.is_empty() {
        let where_str = if let Some(p) = cwd {
            format!("{} 這個專案", p.display())
        } else {
            "這個專案".to_string()
        };
        log_info(&format!("{where_str}沒有待接手的工作線"));
    }

    for note in &notes {
        let age = crate::handoff::age_text(note);
        let waited = if !age.is_empty() { format!("  ({age})") } else { String::new() };
        let stale = if is_stale(note) { "  ⚠ 可能已過期" } else { "" };
        println!("  ○ {}{waited}{stale}", note.name());
        println!("    {}", summary(&note.body, 70));
    }

    for note in &elsewhere {
        log_info(&format!(
            "這個 session 在專案 {} 留了一條線:{};不帶名稱的 claim 會接它",
            note.project,
            note.name()
        ));
    }

    Ok(0)
}

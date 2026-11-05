use regex::Regex;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub const TYPES: [&str; 8] = [
    "feat", "fix", "refactor", "docs", "test", "chore", "perf", "ci",
];
pub const MAX_SUBJECT: usize = 72;

fn find_heredoc(command: &str) -> Option<String> {
    let lines: Vec<&str> = command.split('\n').collect();
    for (i, line) in lines.iter().enumerate() {
        if let Some(pos) = line.find("<<") {
            let mut after = &line[pos + 2..];
            if after.starts_with('-') {
                after = &after[1..];
            }
            let trimmed = after.trim_start();
            let (tag, rest) = if let Some(unquoted) = trimmed.strip_prefix('\'') {
                let end = unquoted.find('\'')?;
                (&unquoted[..end], &unquoted[end + 1..])
            } else if let Some(unquoted) = trimmed.strip_prefix('"') {
                let end = unquoted.find('"')?;
                (&unquoted[..end], &unquoted[end + 1..])
            } else {
                let end = trimmed
                    .find(|c: char| !c.is_alphanumeric() && c != '_')
                    .unwrap_or(trimmed.len());
                if end == 0 {
                    continue;
                }
                (&trimmed[..end], &trimmed[end..])
            };

            if !tag.chars().all(|c| c.is_alphanumeric() || c == '_') {
                continue;
            }
            if !rest.trim().is_empty() {
                continue;
            }

            let mut body_lines = Vec::new();
            let mut found = false;
            for next_line in &lines[i + 1..] {
                if next_line.trim() == tag {
                    found = true;
                    break;
                }
                body_lines.push(*next_line);
            }
            if found {
                return Some(body_lines.join("\n"));
            }
        }
    }
    None
}

fn literal(message: &str, command: &str) -> Option<String> {
    if message.contains("$(") || message.contains('`') {
        let heredoc = find_heredoc(command)?;
        let first = heredoc.trim().lines().next()?.trim();
        return Some(first.to_string());
    }
    if message.contains('$') {
        return None;
    }
    let first = message.lines().next()?.trim();
    Some(first.to_string())
}

pub fn commit_subject(command: &str) -> Option<String> {
    let words = shlex::split(command)?;
    let git_pos = words.iter().position(|w| w == "git")?;
    let mut rest = &words[git_pos + 1..];
    while !rest.is_empty() && rest[0].starts_with('-') {
        if rest[0] == "-C" || rest[0] == "-c" {
            rest = if rest.len() >= 2 { &rest[2..] } else { &[] };
        } else {
            rest = &rest[1..];
        }
    }
    if rest.is_empty() || rest[0] != "commit" {
        return None;
    }
    let flags = &rest[1..];
    for flag in flags {
        if matches!(
            flag.as_str(),
            "-F" | "--file" | "-t" | "--template" | "--squash" | "--fixup"
        ) {
            return None;
        }
    }
    if flags.iter().any(|f| f == "--amend")
        && !flags
            .iter()
            .any(|f| f == "-m" || f.starts_with("-m") || f.starts_with("--message"))
    {
        return None;
    }

    for (index, flag) in flags.iter().enumerate() {
        if (flag == "-m" || flag == "--message") && index + 1 < flags.len() {
            return literal(&flags[index + 1], command);
        }
        if let Some(msg) = flag.strip_prefix("--message=") {
            return literal(msg, command);
        }
        if flag.starts_with("-m") && flag.len() > 2 {
            return literal(&flag[2..], command);
        }
    }
    None
}

pub fn problems(subject: &str) -> Vec<String> {
    let mut found = Vec::new();
    let re = Regex::new(
        r"^(?:feat|fix|refactor|docs|test|chore|perf|ci)(?:\([a-z0-9._-]+\))?: .+",
    )
    .unwrap();
    if !re.is_match(subject) {
        found.push(format!(
            "主旨要寫成 type(scope): description。可用的 type:{}。",
            TYPES.join(", ")
        ));
    }
    let char_count = subject.chars().count();
    if char_count > MAX_SUBJECT {
        found.push(format!("主旨 {char_count} 字元,超過 {MAX_SUBJECT}。"));
    }
    if subject.ends_with('.') {
        found.push("主旨結尾不要句點。".to_string());
    }
    found
}

pub fn recent_subjects(cwd: &Path) -> String {
    let mut child = match Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(["log", "--format=%s", "-10"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return String::new(),
    };

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

    match rx.recv_timeout(Duration::from_secs(5)) {
        Ok(bytes) => {
            let _ = child.wait();
            let _ = reader.join();
            String::from_utf8_lossy(&bytes).trim().to_string()
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            String::new()
        }
    }
}

pub fn run_hook(args: &[String]) -> i32 {
    if !args.is_empty() {
        return 0;
    }
    use std::io::Read;
    let mut buf = Vec::new();
    if std::io::stdin()
        .take(4 * 1024 * 1024)
        .read_to_end(&mut buf)
        .is_err()
    {
        return 0;
    }
    let payload: serde_json::Value = match serde_json::from_slice::<serde_json::Value>(&buf) {
        Ok(v) if v.is_object() => v,
        _ => return 0,
    };
    if payload.get("tool_name").and_then(|v| v.as_str()) != Some("Bash") {
        return 0;
    }
    let command = match payload
        .get("tool_input")
        .and_then(|v| v.get("command"))
        .and_then(|v| v.as_str())
    {
        Some(c) => c,
        None => return 0,
    };
    let subject = match commit_subject(command) {
        Some(s) => s,
        None => return 0,
    };
    let found = problems(&subject);
    if found.is_empty() {
        return 0;
    }

    eprintln!("這個 commit 主旨不符慣例:{subject}");
    for problem in &found {
        eprintln!("  - {problem}");
    }
    if let Some(cwd) = payload.get("cwd").and_then(|v| v.as_str()) {
        if !cwd.is_empty() {
            let recent = recent_subjects(Path::new(cwd));
            if !recent.is_empty() {
                eprintln!("\n這個 repo 最近的寫法:");
                for line in recent.lines() {
                    eprintln!("  {line}");
                }
            }
        }
    }
    eprintln!("\n請改寫主旨後重送,不要繞過這個檢查。");
    2
}

#[cfg(test)]
mod tests {
    use super::*;

    fn judge(command: &str) -> Option<Vec<String>> {
        let subject = commit_subject(command)?;
        Some(problems(&subject))
    }

    #[test]
    fn test_conventional_subject_passes() {
        let valid = [
            "git commit -m \"fix(memory): keep the clock local\"",
            "git commit -m \"chore: release 1.2.3\"",
            "git commit -m\"feat(gui): add a button\"",
            "git commit --message=\"docs: explain the hook\"",
            "git -C /repo commit -m \"ci: pin the runner image\"",
        ];
        for cmd in valid {
            assert_eq!(judge(cmd), Some(Vec::new()), "Failed on: {cmd}");
        }
    }

    #[test]
    fn test_subject_off_convention_refused() {
        let invalid = [
            "git commit -m \"updated some files\"",
            "git commit -m \"fix memory clock\"",
            "git commit -m \"style: reformat\"",
            "git commit -m \"fix(memory): keep the clock local.\"",
        ];
        for cmd in invalid {
            assert!(
                !judge(cmd).unwrap().is_empty(),
                "Expected problems for: {cmd}"
            );
        }
    }

    #[test]
    fn test_what_cannot_be_read_is_not_judged() {
        let unjudged = [
            "git commit -F /tmp/msg.txt",
            "git commit --amend --no-edit",
            "git commit -m \"$MSG\"",
            "git status",
            "echo \"git commit -m bad\"",
        ];
        for cmd in unjudged {
            assert_eq!(judge(cmd), None, "Expected None for: {cmd}");
        }
    }

    #[test]
    fn test_body_written_as_heredoc() {
        let good = "git commit -m \"$(cat <<'EOF'\nfix(cli): stop the prompt\n\nWhy, not what.\nEOF\n)\"";
        let bad = "git commit -m \"$(cat <<'EOF'\nupdated stuff\n\nMore text.\nEOF\n)\"";
        assert_eq!(judge(good), Some(Vec::new()));
        assert!(!judge(bad).unwrap().is_empty());
    }

    #[test]
    fn test_subject_length_limit() {
        let good_64 = format!("git commit -m \"fix(x): {}\"", "a".repeat(64));
        let bad_65 = format!("git commit -m \"fix(x): {}\"", "a".repeat(65));
        assert_eq!(judge(&good_64), Some(Vec::new()));
        assert!(!judge(&bad_65).unwrap().is_empty());
    }
}

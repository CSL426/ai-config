use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

fn resolve_executable_in_path(name: &str) -> Option<PathBuf> {
    if let Ok(path_var) = std::env::var("PATH") {
        for entry in std::env::split_paths(&path_var) {
            let candidate = entry.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn git_directory() -> Option<PathBuf> {
    let name = if cfg!(windows) { "git.exe" } else { "git" };
    resolve_executable_in_path(name).and_then(|p| p.parent().map(|p| p.to_path_buf()))
}

fn python_executable() -> PathBuf {
    if let Ok(py) = std::env::var("PYTHON") {
        let p = PathBuf::from(py);
        if p.is_file() {
            return p;
        }
    }
    if let Ok(venv) = std::env::var("VIRTUAL_ENV") {
        let sub = if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        };
        let p = PathBuf::from(venv).join(sub);
        if p.is_file() {
            return p;
        }
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut curr = manifest.as_path();
    while let Some(parent) = curr.parent() {
        let sub = if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        };
        let p = parent.join(".venv").join(sub);
        if p.is_file() {
            return p;
        }
        curr = parent;
    }
    let name = if cfg!(windows) {
        "python.exe"
    } else {
        "python3"
    };
    if let Some(found) = resolve_executable_in_path(name) {
        return found;
    }
    PathBuf::from(name)
}

fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut root = manifest.clone();
    while let Some(parent) = root.parent() {
        if parent.join("ai_config").is_dir() {
            return parent.to_path_buf();
        }
        root = parent.to_path_buf();
    }
    manifest.parent().unwrap().to_path_buf()
}

struct HookTestEnv {
    _temp: tempfile::TempDir,
    py_home: PathBuf,
    py_work: PathBuf,
    rs_home: PathBuf,
    rs_work: PathBuf,
}

impl HookTestEnv {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let canonical_temp = acg::paths::resolve_like_python(temp.path());

        let py_home = canonical_temp.join("py_home");
        let py_work = canonical_temp.join("py_work");
        let rs_home = canonical_temp.join("rs_home");
        let rs_work = canonical_temp.join("rs_work");

        fs::create_dir_all(py_home.join(".claude")).unwrap();
        fs::create_dir_all(&py_work).unwrap();
        fs::create_dir_all(rs_home.join(".claude")).unwrap();
        fs::create_dir_all(&rs_work).unwrap();

        HookTestEnv {
            _temp: temp,
            py_home,
            py_work,
            rs_home,
            rs_work,
        }
    }

    fn clean_path(&self) -> String {
        let py_bin = python_executable();
        let mut paths = Vec::new();
        if let Some(py_dir) = py_bin.parent() {
            paths.push(py_dir.to_path_buf());
        }
        if let Some(git_dir) = git_directory() {
            paths.push(git_dir);
        }
        if cfg!(windows) {
            paths.push(PathBuf::from(r"C:\Windows\System32"));
            paths.push(PathBuf::from(r"C:\Windows"));
        } else {
            paths.push(PathBuf::from("/usr/bin"));
            paths.push(PathBuf::from("/bin"));
        }
        std::env::join_paths(paths)
            .unwrap()
            .to_string_lossy()
            .to_string()
    }

    fn run_py(&self, args: &[&str], stdin_bytes: &[u8]) -> Output {
        let py_bin = python_executable();
        let root = repo_root();
        let clean_path = self.clean_path();

        let mut py_cmd = Command::new(&py_bin);
        py_cmd.args(["-m", "ai_config"]);
        py_cmd.args(args);
        py_cmd.current_dir(&self.py_work);
        py_cmd.env("PATH", &clean_path);
        py_cmd.env("HOME", &self.py_home);
        py_cmd.env("USERPROFILE", &self.py_home);
        py_cmd.env("XDG_CONFIG_HOME", self.py_home.join(".config"));
        py_cmd.env("XDG_DATA_HOME", self.py_home.join(".local/share"));
        py_cmd.env("PYTHONPATH", &root);
        py_cmd.env("NO_COLOR", "1");
        py_cmd.stdin(std::process::Stdio::piped());
        py_cmd.stdout(std::process::Stdio::piped());
        py_cmd.stderr(std::process::Stdio::piped());
        if cfg!(windows) {
            if let Ok(sysroot) = std::env::var("SystemRoot") {
                py_cmd.env("SystemRoot", &sysroot);
            }
            if let Ok(comspec) = std::env::var("COMSPEC") {
                py_cmd.env("COMSPEC", &comspec);
            }
            if let Ok(pathext) = std::env::var("PATHEXT") {
                py_cmd.env("PATHEXT", &pathext);
            }
        }

        let mut py_child = py_cmd.spawn().expect("Failed to spawn Python CLI");
        if let Some(mut sin) = py_child.stdin.take() {
            use std::io::Write;
            let _ = sin.write_all(stdin_bytes);
        }
        py_child.wait_with_output().expect("Failed to wait Python CLI")
    }

    fn run_rs(&self, args: &[&str], stdin_bytes: &[u8]) -> Output {
        let rs_bin = PathBuf::from(env!("CARGO_BIN_EXE_acg"));
        let clean_path = self.clean_path();

        let mut rs_cmd = Command::new(&rs_bin);
        rs_cmd.args(args);
        rs_cmd.current_dir(&self.rs_work);
        rs_cmd.env("PATH", &clean_path);
        rs_cmd.env("HOME", &self.rs_home);
        rs_cmd.env("USERPROFILE", &self.rs_home);
        rs_cmd.env("XDG_CONFIG_HOME", self.rs_home.join(".config"));
        rs_cmd.env("XDG_DATA_HOME", self.rs_home.join(".local/share"));
        rs_cmd.env("NO_COLOR", "1");
        rs_cmd.stdin(std::process::Stdio::piped());
        rs_cmd.stdout(std::process::Stdio::piped());
        rs_cmd.stderr(std::process::Stdio::piped());
        if cfg!(windows) {
            if let Ok(sysroot) = std::env::var("SystemRoot") {
                rs_cmd.env("SystemRoot", &sysroot);
            }
            if let Ok(comspec) = std::env::var("COMSPEC") {
                rs_cmd.env("COMSPEC", &comspec);
            }
            if let Ok(pathext) = std::env::var("PATHEXT") {
                rs_cmd.env("PATHEXT", &pathext);
            }
        }

        let mut rs_child = rs_cmd.spawn().expect("Failed to spawn Rust CLI");
        if let Some(mut sin) = rs_child.stdin.take() {
            use std::io::Write;
            let _ = sin.write_all(stdin_bytes);
        }
        rs_child.wait_with_output().expect("Failed to wait Rust CLI")
    }

    fn run_both(&self, args: &[&str], stdin_bytes: &[u8]) -> (Output, Output) {
        (self.run_py(args, stdin_bytes), self.run_rs(args, stdin_bytes))
    }

    fn setup_settings(&self, status_line_cmd: Option<&str>, enable_reminder: bool) {
        for home in [&self.py_home, &self.rs_home] {
            let claude = home.join(".claude");
            fs::create_dir_all(&claude).unwrap();
            let mut doc = serde_json::json!({});
            if let Some(cmd) = status_line_cmd {
                doc["statusLine"] = serde_json::json!({
                    "type": "command",
                    "command": cmd
                });
            }
            if enable_reminder {
                let hook_row = serde_json::json!([
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "__handoff-reminder",
                                "statusMessage": "acg：交接用量提醒"
                            }
                        ]
                    }
                ]);
                doc["hooks"] = serde_json::json!({
                    "UserPromptSubmit": hook_row,
                    "PostToolUse": hook_row,
                    "PreCompact": hook_row,
                    "SessionEnd": hook_row,
                });
            }
            fs::write(
                claude.join("settings.json"),
                serde_json::to_string(&doc).unwrap(),
            )
            .unwrap();
        }
    }
}

fn norm(s: &[u8]) -> String {
    let text = String::from_utf8_lossy(s);
    text.replace("\r\n", "\n")
}

// ==========================================
// Part A: __commit-style comparison tests
// ==========================================

#[test]
fn test_compare_commit_style_valid_conventional_subject() {
    let env = HookTestEnv::new();
    let cases = [
        "git commit -m \"fix(cli): keep clock local\"",
        "git commit -m \"chore: release 1.2.3\"",
        "git commit -m\"feat(gui): add a button\"",
        "git commit --message=\"docs: explain the hook\"",
        "git -C /repo commit -m \"ci: pin the runner image\"",
        "git commit -m \"perf(core): optimize memory lookup\"",
    ];
    for cmd in cases {
        let payload = serde_json::json!({
            "tool_name": "Bash",
            "tool_input": {
                "command": cmd
            }
        });
        let bytes = serde_json::to_vec(&payload).unwrap();
        let (py, rs) = env.run_both(&["__commit-style"], &bytes);
        assert_eq!(py.status.code(), Some(0));
        assert_eq!(rs.status.code(), Some(0));
        assert_eq!(norm(&py.stdout), norm(&rs.stdout));
        assert_eq!(norm(&py.stderr), norm(&rs.stderr));
    }
}

#[test]
fn test_compare_commit_style_refused_unconventional_subject() {
    let env = HookTestEnv::new();
    let cases = [
        "git commit -m \"updated stuff\"",
        "git commit -m \"fix memory clock\"",
        "git commit -m \"style: reformat\"",
        "git commit -m \"fix(memory): keep the clock local.\"",
    ];
    for cmd in cases {
        let payload = serde_json::json!({
            "tool_name": "Bash",
            "tool_input": {
                "command": cmd
            }
        });
        let bytes = serde_json::to_vec(&payload).unwrap();
        let (py, rs) = env.run_both(&["__commit-style"], &bytes);
        assert_eq!(py.status.code(), Some(2));
        assert_eq!(rs.status.code(), Some(2));
        assert_eq!(norm(&py.stdout), norm(&rs.stdout));
        assert_eq!(norm(&py.stderr), norm(&rs.stderr));
    }
}

#[test]
fn test_compare_commit_style_subject_length_and_period() {
    let env = HookTestEnv::new();
    let good_64 = format!("git commit -m \"fix(x): {}\"", "a".repeat(64));
    let bad_65 = format!("git commit -m \"fix(x): {}\"", "a".repeat(65));

    let p_good = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": good_64}});
    let (py1, rs1) = env.run_both(
        &["__commit-style"],
        &serde_json::to_vec(&p_good).unwrap(),
    );
    assert_eq!(py1.status.code(), Some(0));
    assert_eq!(rs1.status.code(), Some(0));

    let p_bad = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": bad_65}});
    let (py2, rs2) = env.run_both(&["__commit-style"], &serde_json::to_vec(&p_bad).unwrap());
    assert_eq!(py2.status.code(), Some(2));
    assert_eq!(rs2.status.code(), Some(2));
    assert_eq!(norm(&py2.stderr), norm(&rs2.stderr));
}

#[test]
fn test_compare_commit_style_heredoc() {
    let env = HookTestEnv::new();
    let good = "git commit -m \"$(cat <<'EOF'\nfix(cli): stop the prompt\n\nWhy, not what.\nEOF\n)\"";
    let bad = "git commit -m \"$(cat <<'EOF'\nupdated stuff\n\nMore text.\nEOF\n)\"";

    let p_good = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": good}});
    let (py1, rs1) = env.run_both(
        &["__commit-style"],
        &serde_json::to_vec(&p_good).unwrap(),
    );
    assert_eq!(py1.status.code(), Some(0));
    assert_eq!(rs1.status.code(), Some(0));

    let p_bad = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": bad}});
    let (py2, rs2) = env.run_both(&["__commit-style"], &serde_json::to_vec(&p_bad).unwrap());
    assert_eq!(py2.status.code(), Some(2));
    assert_eq!(rs2.status.code(), Some(2));
    assert_eq!(norm(&py2.stderr), norm(&rs2.stderr));
}

#[test]
fn test_compare_commit_style_non_judged_commands() {
    let env = HookTestEnv::new();
    let cases = [
        "git commit -F /tmp/msg.txt",
        "git commit --amend --no-edit",
        "git commit -m \"$MSG\"",
        "git status",
        "echo \"git commit -m bad\"",
    ];
    for cmd in cases {
        let payload = serde_json::json!({
            "tool_name": "Bash",
            "tool_input": {
                "command": cmd
            }
        });
        let bytes = serde_json::to_vec(&payload).unwrap();
        let (py, rs) = env.run_both(&["__commit-style"], &bytes);
        assert_eq!(py.status.code(), Some(0));
        assert_eq!(rs.status.code(), Some(0));
        assert_eq!(norm(&py.stdout), "");
        assert_eq!(norm(&rs.stdout), "");
        assert_eq!(norm(&py.stderr), "");
        assert_eq!(norm(&rs.stderr), "");
    }
}

#[test]
fn test_compare_commit_style_malformed_stdin_and_extra_args() {
    let env = HookTestEnv::new();
    // Extra args -> exits 0
    let (py1, rs1) = env.run_both(&["__commit-style", "extra_arg"], b"");
    assert_eq!(py1.status.code(), Some(0));
    assert_eq!(rs1.status.code(), Some(0));

    // Empty stdin -> exits 0
    let (py2, rs2) = env.run_both(&["__commit-style"], b"");
    assert_eq!(py2.status.code(), Some(0));
    assert_eq!(rs2.status.code(), Some(0));

    // Broken JSON -> exits 0
    let (py3, rs3) = env.run_both(&["__commit-style"], b"{broken json");
    assert_eq!(py3.status.code(), Some(0));
    assert_eq!(rs3.status.code(), Some(0));

    // Not Bash tool -> exits 0
    let p_other = serde_json::json!({"tool_name": "Read", "tool_input": {"command": "git commit -m 'bad'"}});
    let (py4, rs4) = env.run_both(
        &["__commit-style"],
        &serde_json::to_vec(&p_other).unwrap(),
    );
    assert_eq!(py4.status.code(), Some(0));
    assert_eq!(rs4.status.code(), Some(0));
}

// ==========================================
// Part B: __handoff-statusline comparison tests
// ==========================================

#[test]
fn test_compare_handoff_statusline_under_threshold() {
    let env = HookTestEnv::new();
    // Null original command encoded: "bnVsbA==" is base64 for "null"
    let cwd = env.py_work.to_str().unwrap();
    let payload = serde_json::json!({
        "session_id": "test-session-1",
        "cwd": cwd,
        "context_window": {
            "used_percentage": 65
        }
    });
    let bytes = serde_json::to_vec(&payload).unwrap();

    let (py, rs) = env.run_both(&["__handoff-statusline", "bnVsbA==", "70"], &bytes);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");

    // Check state files in py_home and rs_home
    let py_key = acg::sha256::sha256_hex(b"test-session-1");
    let py_state_path = env
        .py_home
        .join(".claude")
        .join(".acg-handoff-reminder")
        .join(format!("{py_key}.json"));
    let rs_state_path = env
        .rs_home
        .join(".claude")
        .join(".acg-handoff-reminder")
        .join(format!("{py_key}.json"));

    assert!(py_state_path.exists());
    assert!(rs_state_path.exists());

    let py_state: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&py_state_path).unwrap()).unwrap();
    let rs_state: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&rs_state_path).unwrap()).unwrap();

    assert_eq!(py_state["used"], rs_state["used"]);
    assert_eq!(py_state["threshold"], rs_state["threshold"]);
    assert_eq!(py_state["notified"], rs_state["notified"]);
}

#[test]
fn test_compare_handoff_statusline_over_threshold_reminder() {
    let env = HookTestEnv::new();
    let cwd = env.py_work.to_str().unwrap();
    let payload = serde_json::json!({
        "session_id": "test-session-over",
        "cwd": cwd,
        "context_window": {
            "used_percentage": 75
        }
    });
    let bytes = serde_json::to_vec(&payload).unwrap();

    let (py, rs) = env.run_both(&["__handoff-statusline", "bnVsbA==", "70"], &bytes);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "Context 75% · 建議準備 /acg handoff\n");
    assert_eq!(norm(&rs.stdout), "Context 75% · 建議準備 /acg handoff\n");
}

#[test]
fn test_compare_handoff_statusline_with_original_command() {
    let env = HookTestEnv::new();
    let b64_orig = {
        let mut cmd = Command::new(python_executable());
        cmd.args([
            "-c",
            "import base64, json; print(base64.urlsafe_b64encode(json.dumps({'type':'command','command':'echo orig'}).encode()).decode())",
        ]);
        let out = cmd.output().unwrap();
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    };

    let cwd = env.py_work.to_str().unwrap();
    let payload = serde_json::json!({
        "session_id": "test-session-orig",
        "cwd": cwd,
        "context_window": {
            "used_percentage": 80
        }
    });
    let bytes = serde_json::to_vec(&payload).unwrap();

    let (py, rs) = env.run_both(&["__handoff-statusline", &b64_orig, "70"], &bytes);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), norm(&rs.stdout));
}

#[test]
fn test_compare_handoff_statusline_invalid_args_and_broken_input() {
    let env = HookTestEnv::new();
    // Wrong arg count
    let (py1, rs1) = env.run_both(&["__handoff-statusline", "one_arg"], b"");
    assert_eq!(py1.status.code(), Some(0));
    assert_eq!(rs1.status.code(), Some(0));

    // Invalid threshold (100 is > 99)
    let (py2, rs2) = env.run_both(&["__handoff-statusline", "bnVsbA==", "100"], b"");
    assert_eq!(py2.status.code(), Some(0));
    assert_eq!(rs2.status.code(), Some(0));

    // Broken JSON in stdin
    let (py3, rs3) = env.run_both(&["__handoff-statusline", "bnVsbA==", "70"], b"broken json");
    assert_eq!(py3.status.code(), Some(0));
    assert_eq!(rs3.status.code(), Some(0));
}

// ==========================================
// Part C: __handoff-reminder comparison tests
// ==========================================

#[test]
fn test_compare_handoff_reminder_not_installed_returns_empty() {
    let env = HookTestEnv::new();
    let cwd = env.py_work.to_str().unwrap();
    let payload = serde_json::json!({
        "session_id": "sess-not-inst",
        "cwd": cwd,
        "hook_event_name": "PostToolUse"
    });
    let bytes = serde_json::to_vec(&payload).unwrap();

    let (py, rs) = env.run_both(&["__handoff-reminder"], &bytes);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");
}

#[test]
fn test_compare_handoff_reminder_installed_and_triggered() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd_py = env.py_work.to_str().unwrap().to_string();
    let cwd_rs = env.rs_work.to_str().unwrap().to_string();

    // First record usage via statusline
    let p_status_py = serde_json::json!({
        "session_id": "sess-remind",
        "cwd": cwd_py,
        "context_window": { "used_percentage": 75 }
    });
    let p_status_rs = serde_json::json!({
        "session_id": "sess-remind",
        "cwd": cwd_rs,
        "context_window": { "used_percentage": 75 }
    });

    let py1 = env.run_py(
        &["__handoff-statusline", "bnVsbA==", "70"],
        &serde_json::to_vec(&p_status_py).unwrap(),
    );
    assert_eq!(py1.status.code(), Some(0));

    let rs1 = env.run_rs(
        &["__handoff-statusline", "bnVsbA==", "70"],
        &serde_json::to_vec(&p_status_rs).unwrap(),
    );
    assert_eq!(rs1.status.code(), Some(0));

    // Now trigger reminder hook
    let p_remind_py = serde_json::json!({
        "session_id": "sess-remind",
        "cwd": cwd_py,
        "hook_event_name": "PostToolUse"
    });
    let p_remind_rs = serde_json::json!({
        "session_id": "sess-remind",
        "cwd": cwd_rs,
        "hook_event_name": "PostToolUse"
    });

    let py2 = env.run_py(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_remind_py).unwrap(),
    );
    let rs2 = env.run_rs(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_remind_rs).unwrap(),
    );

    assert_eq!(py2.status.code(), Some(0));
    assert_eq!(rs2.status.code(), Some(0));

    let py_out: serde_json::Value = serde_json::from_str(norm(&py2.stdout).trim()).unwrap();
    let rs_out: serde_json::Value = serde_json::from_str(norm(&rs2.stdout).trim()).unwrap();

    assert_eq!(
        py_out["hookSpecificOutput"]["hookEventName"],
        rs_out["hookSpecificOutput"]["hookEventName"]
    );
    assert_eq!(
        py_out["hookSpecificOutput"]["additionalContext"],
        rs_out["hookSpecificOutput"]["additionalContext"]
    );

    // Second call: already notified -> empty output
    let py3 = env.run_py(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_remind_py).unwrap(),
    );
    let rs3 = env.run_rs(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_remind_rs).unwrap(),
    );
    assert_eq!(norm(&py3.stdout), "");
    assert_eq!(norm(&rs3.stdout), "");
}

#[test]
fn test_compare_handoff_reminder_precompact_cleans_state() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd = env.py_work.to_str().unwrap().to_string();
    let p_status = serde_json::json!({
        "session_id": "sess-compact",
        "cwd": cwd,
        "context_window": { "used_percentage": 75 }
    });
    let _ = env.run_both(
        &["__handoff-statusline", "bnVsbA==", "70"],
        &serde_json::to_vec(&p_status).unwrap(),
    );

    let key = acg::sha256::sha256_hex(b"sess-compact");
    let py_path = env
        .py_home
        .join(".claude")
        .join(".acg-handoff-reminder")
        .join(format!("{key}.json"));
    let rs_path = env
        .rs_home
        .join(".claude")
        .join(".acg-handoff-reminder")
        .join(format!("{key}.json"));

    assert!(py_path.exists());
    assert!(rs_path.exists());

    let p_compact = serde_json::json!({
        "session_id": "sess-compact",
        "cwd": cwd,
        "hook_event_name": "PreCompact"
    });
    let (py, rs) = env.run_both(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_compact).unwrap(),
    );
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));

    assert!(!py_path.exists());
    assert!(!rs_path.exists());
}

#[test]
fn test_compare_handoff_reminder_subagent_ignored() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd = env.py_work.to_str().unwrap().to_string();
    let p_sub = serde_json::json!({
        "session_id": "sess-subagent",
        "agent_id": "child-agent",
        "cwd": cwd,
        "hook_event_name": "PostToolUse"
    });
    let (py, rs) = env.run_both(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_sub).unwrap(),
    );
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");
}

#[test]
fn test_compare_handoff_reminder_session_end_cleans_state() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd = env.py_work.to_str().unwrap().to_string();
    let p_status = serde_json::json!({
        "session_id": "sess-end",
        "cwd": cwd,
        "context_window": { "used_percentage": 75 }
    });
    let _ = env.run_both(
        &["__handoff-statusline", "bnVsbA==", "70"],
        &serde_json::to_vec(&p_status).unwrap(),
    );

    let key = acg::sha256::sha256_hex(b"sess-end");
    let py_path = env.py_home.join(".claude").join(".acg-handoff-reminder").join(format!("{key}.json"));
    let rs_path = env.rs_home.join(".claude").join(".acg-handoff-reminder").join(format!("{key}.json"));

    assert!(py_path.exists());
    assert!(rs_path.exists());

    let p_end = serde_json::json!({
        "session_id": "sess-end",
        "cwd": cwd,
        "hook_event_name": "SessionEnd"
    });
    let (py, rs) = env.run_both(
        &["__handoff-reminder"],
        &serde_json::to_vec(&p_end).unwrap(),
    );
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));

    assert!(!py_path.exists());
    assert!(!rs_path.exists());
}

#[test]
fn test_compare_handoff_reminder_expired_age() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd_py = env.py_work.to_str().unwrap().to_string();
    let cwd_rs = env.rs_work.to_str().unwrap().to_string();

    let key = acg::sha256::sha256_hex(b"sess-expired");
    // Write state file with time 400 seconds ago
    let past = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64() - 400.0;

    for (home, cwd) in [(&env.py_home, &cwd_py), (&env.rs_home, &cwd_rs)] {
        let dir = home.join(".claude").join(".acg-handoff-reminder");
        fs::create_dir_all(&dir).unwrap();
        let state = serde_json::json!({
            "cwd": cwd,
            "used": 80.0,
            "time": past,
            "threshold": 70,
            "notified": false
        });
        fs::write(dir.join(format!("{key}.json")), serde_json::to_string(&state).unwrap()).unwrap();
    }

    let p_remind_py = serde_json::json!({
        "session_id": "sess-expired",
        "cwd": cwd_py,
        "hook_event_name": "PostToolUse"
    });
    let p_remind_rs = serde_json::json!({
        "session_id": "sess-expired",
        "cwd": cwd_rs,
        "hook_event_name": "PostToolUse"
    });

    let py = env.run_py(&["__handoff-reminder"], &serde_json::to_vec(&p_remind_py).unwrap());
    let rs = env.run_rs(&["__handoff-reminder"], &serde_json::to_vec(&p_remind_rs).unwrap());

    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");
}

#[test]
fn test_compare_handoff_reminder_corrupt_state_file() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd = env.py_work.to_str().unwrap().to_string();
    let key = acg::sha256::sha256_hex(b"sess-corrupt");

    for home in [&env.py_home, &env.rs_home] {
        let dir = home.join(".claude").join(".acg-handoff-reminder");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(format!("{key}.json")), b"{not a json string").unwrap();
    }

    let p_remind = serde_json::json!({
        "session_id": "sess-corrupt",
        "cwd": cwd,
        "hook_event_name": "PostToolUse"
    });

    let (py, rs) = env.run_both(&["__handoff-reminder"], &serde_json::to_vec(&p_remind).unwrap());
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");
}

#[test]
fn test_compare_handoff_reminder_state_file_symlink_refused() {
    let env = HookTestEnv::new();
    let statusline_cmd = "__handoff-statusline bnVsbA== 70";
    env.setup_settings(Some(statusline_cmd), true);

    let cwd = env.py_work.to_str().unwrap().to_string();
    let key = acg::sha256::sha256_hex(b"sess-sym");

    for home in [&env.py_home, &env.rs_home] {
        let dir = home.join(".claude").join(".acg-handoff-reminder");
        fs::create_dir_all(&dir).unwrap();
        let external = home.join("external.txt");
        fs::write(&external, "untouched").unwrap();

        let link_path = dir.join(format!("{key}.json"));
        #[cfg(unix)]
        {
            let _ = std::os::unix::fs::symlink(&external, &link_path);
        }
        #[cfg(windows)]
        {
            let _ = std::os::windows::fs::symlink_file(&external, &link_path);
        }
    }

    let p_remind = serde_json::json!({
        "session_id": "sess-sym",
        "cwd": cwd,
        "hook_event_name": "PostToolUse"
    });

    let (py, rs) = env.run_both(&["__handoff-reminder"], &serde_json::to_vec(&p_remind).unwrap());
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
    assert_eq!(norm(&py.stdout), "");
    assert_eq!(norm(&rs.stdout), "");

    for home in [&env.py_home, &env.rs_home] {
        let external = home.join("external.txt");
        assert_eq!(fs::read_to_string(external).unwrap(), "untouched");
    }
}

#[test]
fn test_compare_commit_style_with_real_git_history() {
    let env = HookTestEnv::new();
    for work in [&env.py_work, &env.rs_work] {
        Command::new("git").args(["init"]).current_dir(work).output().unwrap();
        Command::new("git").args(["config", "user.name", "Test"]).current_dir(work).output().unwrap();
        Command::new("git").args(["config", "user.email", "test@test.local"]).current_dir(work).output().unwrap();
        Command::new("git").args(["commit", "--allow-empty", "-m", "feat(core): initial commit"]).current_dir(work).output().unwrap();
        Command::new("git").args(["commit", "--allow-empty", "-m", "fix(cli): handle signals"]).current_dir(work).output().unwrap();
    }

    let cwd_py = env.py_work.to_str().unwrap().to_string();
    let cwd_rs = env.rs_work.to_str().unwrap().to_string();

    let p_py = serde_json::json!({
        "tool_name": "Bash",
        "tool_input": { "command": "git commit -m \"bad message\"" },
        "cwd": cwd_py
    });
    let p_rs = serde_json::json!({
        "tool_name": "Bash",
        "tool_input": { "command": "git commit -m \"bad message\"" },
        "cwd": cwd_rs
    });

    let py = env.run_py(&["__commit-style"], &serde_json::to_vec(&p_py).unwrap());
    let rs = env.run_rs(&["__commit-style"], &serde_json::to_vec(&p_rs).unwrap());

    assert_eq!(py.status.code(), Some(2));
    assert_eq!(rs.status.code(), Some(2));
    assert_eq!(norm(&py.stderr), norm(&rs.stderr));
}


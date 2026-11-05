use std::fs;
use std::path::{Path, PathBuf};
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
        let sub = if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" };
        let p = PathBuf::from(venv).join(sub);
        if p.is_file() {
            return p;
        }
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut curr = manifest.as_path();
    while let Some(parent) = curr.parent() {
        let sub = if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" };
        let p = parent.join(".venv").join(sub);
        if p.is_file() {
            return p;
        }
        curr = parent;
    }
    let name = if cfg!(windows) { "python.exe" } else { "python3" };
    if let Some(found) = resolve_executable_in_path(name) {
        return found;
    }
    if cfg!(windows) {
        if let Some(found) = resolve_executable_in_path("python") {
            return found;
        }
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

struct TestEnv {
    _temp: tempfile::TempDir,
    py_data: PathBuf,
    py_home: PathBuf,
    py_work: PathBuf,
    rs_data: PathBuf,
    rs_home: PathBuf,
    rs_work: PathBuf,
}

impl TestEnv {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let canonical_temp = acg::paths::resolve_like_python(temp.path());
        let py_data = canonical_temp.join("py_data");
        let py_home = canonical_temp.join("py_home");
        let py_work = canonical_temp.join("py_work");

        let rs_data = canonical_temp.join("rs_data");
        let rs_home = canonical_temp.join("rs_home");
        let rs_work = canonical_temp.join("rs_work");

        fs::create_dir_all(py_data.join("claude")).unwrap();
        fs::create_dir_all(&py_home).unwrap();
        fs::create_dir_all(&py_work).unwrap();

        fs::create_dir_all(rs_data.join("claude")).unwrap();
        fs::create_dir_all(&rs_home).unwrap();
        fs::create_dir_all(&rs_work).unwrap();

        TestEnv {
            _temp: temp,
            py_data,
            py_home,
            py_work,
            rs_data,
            rs_home,
            rs_work,
        }
    }

    fn init_git_repo(&self, remote_url: &str) {
        for work in [&self.py_work, &self.rs_work] {
            Command::new("git").arg("init").current_dir(work).output().unwrap();
            Command::new("git")
                .args(["remote", "add", "origin", remote_url])
                .current_dir(work)
                .output()
                .unwrap();
        }
    }

    fn setup_session(&self, session_id: &str, session_name: &str, cwd: Option<&str>) {
        for (home, work) in [(&self.py_home, &self.py_work), (&self.rs_home, &self.rs_work)] {
            let sess_dir = home.join(".claude").join("sessions");
            fs::create_dir_all(&sess_dir).unwrap();
            let mut record = serde_json::json!({
                "sessionId": session_id,
                "name": session_name,
            });
            if let Some(c) = cwd {
                record["cwd"] = serde_json::Value::String(c.to_string());
            } else {
                record["cwd"] = serde_json::Value::String(work.to_str().unwrap().to_string());
            }
            fs::write(sess_dir.join("100.json"), record.to_string()).unwrap();
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
        std::env::join_paths(paths).unwrap().to_string_lossy().to_string()
    }

    #[allow(dead_code)]
    fn run_both_raw_with_data_repos(
        &self,
        args: &[&str],
        py_data: &Path,
        rs_data: &Path,
        extra_envs: &[(&str, &str)],
    ) -> (Output, Output) {
        let py_bin = python_executable();
        let rs_bin = PathBuf::from(env!("CARGO_BIN_EXE_acg"));
        let root = repo_root();

        let clean_path = self.clean_path();

        let mut py_cmd = Command::new(&py_bin);
        py_cmd.args(["-m", "ai_config"]);
        py_cmd.args(args);
        py_cmd.current_dir(&self.py_work);
        py_cmd.env("PATH", &clean_path);
        py_cmd.env("AI_CONFIG_REPO", py_data);
        py_cmd.env("HOME", &self.py_home);
        py_cmd.env("USERPROFILE", &self.py_home);
        py_cmd.env("XDG_CONFIG_HOME", self.py_home.join(".config"));
        py_cmd.env("XDG_DATA_HOME", self.py_home.join(".local/share"));
        py_cmd.env("PYTHONPATH", &root);
        py_cmd.env("AI_CONFIG_ENTRYPOINT", "./ai-config.sh");
        py_cmd.env("NO_COLOR", "1");
        py_cmd.env("GIT_CONFIG_GLOBAL", if cfg!(windows) { "NUL" } else { "/dev/null" });
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
        for (k, v) in extra_envs {
            py_cmd.env(k, v);
        }
        let py_res = py_cmd.output().expect("Failed to execute Python CLI");

        let mut rs_cmd = Command::new(&rs_bin);
        rs_cmd.args(args);
        rs_cmd.current_dir(&self.rs_work);
        rs_cmd.env("PATH", &clean_path);
        rs_cmd.env("AI_CONFIG_REPO", rs_data);
        rs_cmd.env("HOME", &self.rs_home);
        rs_cmd.env("USERPROFILE", &self.rs_home);
        rs_cmd.env("XDG_CONFIG_HOME", self.rs_home.join(".config"));
        rs_cmd.env("XDG_DATA_HOME", self.rs_home.join(".local/share"));
        rs_cmd.env("AI_CONFIG_ENTRYPOINT", "./ai-config.sh");
        rs_cmd.env("NO_COLOR", "1");
        rs_cmd.env("GIT_CONFIG_GLOBAL", if cfg!(windows) { "NUL" } else { "/dev/null" });
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
        for (k, v) in extra_envs {
            rs_cmd.env(k, v);
        }
        let rs_res = rs_cmd.output().expect("Failed to execute Rust CLI");

        (py_res, rs_res)
    }

    fn run_both_raw(&self, args: &[&str], extra_envs: &[(&str, &str)]) -> (Output, Output) {
        self.run_both_raw_with_data_repos(args, &self.py_data, &self.rs_data, extra_envs)
    }

    #[allow(dead_code)]
    fn run_both_with_data_repos(
        &self,
        args: &[&str],
        py_data: &Path,
        rs_data: &Path,
        extra_envs: &[(&str, &str)],
    ) -> (Output, Output) {
        let mut full = vec!["memory", "handoff"];
        full.extend_from_slice(args);
        self.run_both_raw_with_data_repos(&full, py_data, rs_data, extra_envs)
    }

    fn run_both(&self, args: &[&str], extra_envs: &[(&str, &str)]) -> (Output, Output) {
        let mut full = vec!["memory", "handoff"];
        full.extend_from_slice(args);
        self.run_both_raw(&full, extra_envs)
    }

    fn assert_matches(&self, py_out: &Output, rs_out: &Output) {
        self.assert_matches_extra(py_out, rs_out, &[]);
    }

    #[allow(dead_code)]
    fn assert_matches_extra(
        &self,
        py_out: &Output,
        rs_out: &Output,
        extra_data_dirs: &[&Path],
    ) {
        let py_stdout = String::from_utf8_lossy(&py_out.stdout).replace("\r\n", "\n");
        let rs_stdout = String::from_utf8_lossy(&rs_out.stdout).replace("\r\n", "\n");
        let py_stderr = String::from_utf8_lossy(&py_out.stderr).replace("\r\n", "\n");
        let rs_stderr = String::from_utf8_lossy(&rs_out.stderr).replace("\r\n", "\n");

        assert_eq!(
            py_out.status.code(),
            rs_out.status.code(),
            "Exit code mismatch!\nPython stderr:\n{py_stderr}\nRust stderr:\n{rs_stderr}"
        );

        let ts_re = regex::Regex::new(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z").unwrap();
        let py_stdout_norm = ts_re.replace_all(&py_stdout, "<TIMESTAMP>");
        let rs_stdout_norm = ts_re.replace_all(&rs_stdout, "<TIMESTAMP>");
        let py_stderr_norm = ts_re.replace_all(&py_stderr, "<TIMESTAMP>");
        let rs_stderr_norm = ts_re.replace_all(&rs_stderr, "<TIMESTAMP>");

        let py_data_str = self.py_data.to_string_lossy();
        let py_work_str = self.py_work.to_string_lossy();
        let py_home_str = self.py_home.to_string_lossy();
        let rs_data_str = self.rs_data.to_string_lossy();
        let rs_work_str = self.rs_work.to_string_lossy();
        let rs_home_str = self.rs_home.to_string_lossy();

        let mut py_stdout_norm = py_stdout_norm
            .replace(&*py_data_str, "<DATA_DIR>")
            .replace(&*rs_data_str, "<DATA_DIR>")
            .replace(&*py_work_str, "<WORK_DIR>")
            .replace(&*rs_work_str, "<WORK_DIR>")
            .replace(&*py_home_str, "<HOME_DIR>")
            .replace(&*rs_home_str, "<HOME_DIR>");
        let mut rs_stdout_norm = rs_stdout_norm
            .replace(&*py_data_str, "<DATA_DIR>")
            .replace(&*rs_data_str, "<DATA_DIR>")
            .replace(&*py_work_str, "<WORK_DIR>")
            .replace(&*rs_work_str, "<WORK_DIR>")
            .replace(&*py_home_str, "<HOME_DIR>")
            .replace(&*rs_home_str, "<HOME_DIR>");

        let mut py_stderr_norm = py_stderr_norm
            .replace(&*py_data_str, "<DATA_DIR>")
            .replace(&*rs_data_str, "<DATA_DIR>")
            .replace(&*py_work_str, "<WORK_DIR>")
            .replace(&*rs_work_str, "<WORK_DIR>")
            .replace(&*py_home_str, "<HOME_DIR>")
            .replace(&*rs_home_str, "<HOME_DIR>");
        let mut rs_stderr_norm = rs_stderr_norm
            .replace(&*py_data_str, "<DATA_DIR>")
            .replace(&*rs_data_str, "<DATA_DIR>")
            .replace(&*py_work_str, "<WORK_DIR>")
            .replace(&*rs_work_str, "<WORK_DIR>")
            .replace(&*py_home_str, "<HOME_DIR>")
            .replace(&*rs_home_str, "<HOME_DIR>");

        for extra in extra_data_dirs {
            let s = extra.to_string_lossy();
            py_stdout_norm = py_stdout_norm.replace(&*s, "<DATA_DIR>");
            rs_stdout_norm = rs_stdout_norm.replace(&*s, "<DATA_DIR>");
            py_stderr_norm = py_stderr_norm.replace(&*s, "<DATA_DIR>");
            rs_stderr_norm = rs_stderr_norm.replace(&*s, "<DATA_DIR>");
        }

        assert_eq!(py_stdout_norm, rs_stdout_norm, "Stdout mismatch!");
        assert_eq!(py_stderr_norm, rs_stderr_norm, "Stderr mismatch!");

        self.compare_handoff_files();
    }

    fn compare_handoff_files(&self) {
        let py_handoff = self.py_data.join("memory").join("handoff");
        let rs_handoff = self.rs_data.join("memory").join("handoff");

        if !py_handoff.exists() && !rs_handoff.exists() {
            return;
        }

        assert_eq!(py_handoff.exists(), rs_handoff.exists());

        let mut py_files = Vec::new();
        for entry in walkdir(&py_handoff) {
            let rel = entry.strip_prefix(&py_handoff).unwrap().to_path_buf();
            py_files.push(rel);
        }
        py_files.sort();

        let mut rs_files = Vec::new();
        for entry in walkdir(&rs_handoff) {
            let rel = entry.strip_prefix(&rs_handoff).unwrap().to_path_buf();
            rs_files.push(rel);
        }
        rs_files.sort();

        assert_eq!(py_files, rs_files, "Generated file structure mismatch!");

        let ts_re = regex::Regex::new(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b").unwrap();
        for rel in &py_files {
            let py_file = py_handoff.join(rel);
            let rs_file = rs_handoff.join(rel);

            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let py_mode = fs::symlink_metadata(&py_file).unwrap().permissions().mode() & 0o777;
                let rs_mode = fs::symlink_metadata(&rs_file).unwrap().permissions().mode() & 0o777;
                assert_eq!(py_mode, rs_mode, "File permissions mismatch for {}", rel.display());
            }

            if py_file.is_file() {
                let py_bytes = fs::read(&py_file).unwrap();
                let rs_bytes = fs::read(&rs_file).unwrap();

                let py_norm = if let Ok(s) = std::str::from_utf8(&py_bytes) {
                    ts_re.replace_all(s, "<TIMESTAMP>").into_owned().into_bytes()
                } else {
                    py_bytes
                };
                let rs_norm = if let Ok(s) = std::str::from_utf8(&rs_bytes) {
                    ts_re.replace_all(s, "<TIMESTAMP>").into_owned().into_bytes()
                } else {
                    rs_bytes
                };
                assert_eq!(py_norm, rs_norm, "File byte mismatch for {}", rel.display());
            }
        }
    }
}

fn walkdir(dir: &Path) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            paths.push(path.clone());
            if path.is_dir() && !path.is_symlink() {
                paths.extend(walkdir(&path));
            }
        }
    }
    paths
}

#[test]
fn test_compare_help() {
    let env = TestEnv::new();
    let (py, rs) = env.run_both(&["--help"], &[]);
    env.assert_matches(&py, &rs);

    let (py_h, rs_h) = env.run_both(&["-h"], &[]);
    env.assert_matches(&py_h, &rs_h);
}

#[test]
fn test_compare_invalid_arguments() {
    let env = TestEnv::new();

    // write without content
    let (py, rs) = env.run_both(&["write", "thread_only"], &[]);
    env.assert_matches(&py, &rs);

    // done with extra arg
    let (py, rs) = env.run_both(&["done", "a", "b"], &[]);
    env.assert_matches(&py, &rs);

    // claim with extra arg
    let (py, rs) = env.run_both(&["claim", "a", "b"], &[]);
    env.assert_matches(&py, &rs);

    // unknown action
    let (py, rs) = env.run_both(&["unknown_cmd"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_write_normal_and_chinese() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(
        &["write", "記憶改善", "做到一半，下一步是測試"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-one")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_write_with_spaces_in_name() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(
        &["write", "含 空格 的線", "內 容 有 多 個 詞"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-space")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_write_empty_thread_or_body() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(&["write", "   ", "有內容"], &[]);
    env.assert_matches(&py, &rs);

    let (py, rs) = env.run_both(&["write", "有名稱", "   "], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_list_empty() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_list_with_notes_and_summary() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();

        // Distinct timestamps so sort order (newest first: C, B, A) is 100% deterministic across threads
        let note_a = "---\nthread: 線A\nproject: my-org--my-repo\nstate: open\nauthor: u\ncreated: 2026-11-02T10:00:01Z\nupdated: 2026-11-02T10:00:01Z\n---\n\n## Goal\n做完X\n\n## Next\n1. **優先事項**。細節說明\n- 第二項\n";
        let note_b = "---\nthread: 線B\nproject: my-org--my-repo\nstate: open\nauthor: u\ncreated: 2026-11-02T10:00:02Z\nupdated: 2026-11-02T10:00:02Z\n---\n\n## Unknowns\n- 尚未確認之細節\n";
        let note_c = "---\nthread: 線C\nproject: my-org--my-repo\nstate: open\nauthor: u\ncreated: 2026-11-02T10:00:03Z\nupdated: 2026-11-02T10:00:03Z\n---\n\n純文字無標題\n第二行\n";

        fs::write(hdir.join("線A.md"), note_a).unwrap();
        fs::write(hdir.join("線B.md"), note_b).unwrap();
        fs::write(hdir.join("線C.md"), note_c).unwrap();
    }

    // List them
    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_claim_and_done_lifecycle() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // 1. Write
    let (py, rs) = env.run_both(
        &["write", "生命週期", "進行中工作"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);

    // 2. Claim
    let (py, rs) = env.run_both(
        &["claim", "生命週期"],
        &[("CLAUDE_CODE_SESSION_ID", "taker-sess")],
    );
    env.assert_matches(&py, &rs);

    // 3. Second claim refused
    let (py, rs) = env.run_both(
        &["claim", "生命週期"],
        &[("CLAUDE_CODE_SESSION_ID", "third-sess")],
    );
    env.assert_matches(&py, &rs);

    // 4. Overwrite refused by someone else (Safety Scenario 3)
    let (py, rs) = env.run_both(
        &["write", "生命週期", "覆寫嘗試"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);

    // 5. Overwrite allowed with --force (Safety Scenario 3)
    let (py, rs) = env.run_both(
        &["write", "生命週期", "強制覆寫", "--force"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);

    // 6. Done
    let (py, rs) = env.run_both(&["done", "生命週期"], &[]);
    env.assert_matches(&py, &rs);

    // 7. Claim closed thread refused
    let (py, rs) = env.run_both(&["claim", "生命週期"], &[]);
    env.assert_matches(&py, &rs);

    // 8. Write again after done is allowed (done resets claimed markers)
    let (py, rs) = env.run_both(
        &["write", "生命週期", "新的一輪"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_claim_own_thread_by_session_name() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");
    env.setup_session("session-author", "my-worker", None);

    // Write note with session
    let (py, rs) = env.run_both(
        &["write", "自動接手", "我的工作線進度"],
        &[("CLAUDE_CODE_SESSION_ID", "session-author")],
    );
    env.assert_matches(&py, &rs);

    // Clear session, new session ID but same session name
    env.setup_session("session-after-clear", "my-worker", None);

    // Claim with NO arguments: should automatically pick up "自動接手"
    let (py, rs) = env.run_both(&["claim"], &[("CLAUDE_CODE_SESSION_ID", "session-after-clear")]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_claim_own_thread_missing_session_name() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Claim without session name configured
    let (py, rs) = env.run_both(&["claim"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_claim_nonexistent() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(&["claim", "根本不存在"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_list_missing_project_directory() {
    let env = TestEnv::new();
    let missing_path = env.py_work.join("no_such_directory");
    let missing_str = missing_path.to_str().unwrap();

    let (py, rs) = env.run_both(&["list", missing_str], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_archive_finished_on_list() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Pre-populate old notes in both environments (Safety Scenario 4)
    let now = chrono::Utc::now();
    let old_time = (now - chrono::Duration::days(45)).format("%Y-%m-%dT%H:%M:%SZ").to_string();
    let active_time = (now - chrono::Duration::hours(1)).format("%Y-%m-%dT%H:%M:%SZ").to_string();

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();

        // An old note untouched for 45 days
        let old_note = format!("---\nthread: 陳年舊事\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: {old_time}\nupdated: {old_time}\n---\n\n這是舊的\n");
        fs::write(hdir.join("陳年舊事.md"), old_note).unwrap();

        // An active note
        let active_note = format!("---\nthread: 現行線\nproject: my-org--my-repo\nstate: open\nauthor: user\ncreated: {active_time}\nupdated: {active_time}\n---\n\n現行進度\n");
        fs::write(hdir.join("現行線.md"), active_note).unwrap();
    }

    // List: triggers archive_finished
    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);

    // Verify 陳年舊事 moved to archive in both
    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        assert!(!hdir.join("陳年舊事.md").exists());
        assert!(hdir.join("archive").join("陳年舊事.md").is_file());
        assert!(hdir.join("現行線.md").is_file());
    }
}

#[test]
fn test_compare_archive_collision_naming() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let now = chrono::Utc::now();
    let old_time = (now - chrono::Duration::days(45)).format("%Y-%m-%dT%H:%M:%SZ").to_string();
    let date_stamp = &old_time[..10];

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        let adir = hdir.join("archive");
        fs::create_dir_all(&adir).unwrap();

        // Already archived first copy
        let n1 = "---\nthread: 撞名線\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: 2020-01-01T10:00:00Z\nupdated: 2020-01-01T10:00:00Z\n---\n\nfirst archived\n";
        fs::write(adir.join("撞名線.md"), n1).unwrap();
        // Already archived collision copy with date
        let n2 = "---\nthread: 撞名線\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: 2020-01-15T10:00:00Z\nupdated: 2020-01-15T10:00:00Z\n---\n\nsecond archived\n";
        fs::write(adir.join(format!("撞名線.{date_stamp}.md")), n2).unwrap();

        // New note that is ready to archive with same updated date
        let old_note = format!("---\nthread: 撞名線\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: {old_time}\nupdated: {old_time}\n---\n\n這是第三次撞名\n");
        fs::write(hdir.join("撞名線.md"), old_note).unwrap();
    }

    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);

    for data in [&env.py_data, &env.rs_data] {
        let adir = data.join("memory").join("handoff").join("archive");
        assert!(adir.join("撞名線.md").is_file());
        assert!(adir.join(format!("撞名線.{date_stamp}.md")).is_file());
        assert!(adir.join(format!("撞名線.{date_stamp}-2.md")).is_file());
    }
}

#[test]
fn test_compare_crlf_and_missing_created_notes_in_list_and_claim() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();

        // 1. CRLF note (Safety Scenario 1)
        let crlf = "---\r\nthread: 換行測試\r\nproject: my-org--my-repo\r\nstate: open\r\nauthor: user\r\ncreated: 2026-11-02T10:00:00Z\r\nupdated: 2026-11-02T10:00:00Z\r\n---\r\n\r\n## Next\r\n- CRLF 待辦項目\r\n";
        fs::write(hdir.join("換行測試.md"), crlf).unwrap();

        // 2. Missing created field note (Safety Scenario 2)
        let missing_created = "---\nthread: 舊筆記缺created\nproject: my-org--my-repo\nstate: open\nauthor: user\nupdated: 2026-11-02T10:00:00Z\n---\n\n## Next\n- 缺少 created 欄位測試\n";
        fs::write(hdir.join("舊筆記缺created.md"), missing_created).unwrap();
    }

    // List them
    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);

    // Claim CRLF note
    let (py, rs) = env.run_both(&["claim", "換行測試"], &[]);
    env.assert_matches(&py, &rs);

    // Claim missing created note
    let (py, rs) = env.run_both(&["claim", "舊筆記缺created"], &[]);
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_crlf_and_missing_created_done_and_rewrite() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();

        // CRLF note (Safety Scenario 1)
        let crlf = "---\r\nthread: 換行結案線\r\nproject: my-org--my-repo\r\nstate: open\r\nauthor: user\r\ncreated: 2026-11-02T10:00:00Z\r\nupdated: 2026-11-02T10:00:00Z\r\n---\r\n\r\n## Next\r\n- CRLF 結案項目\r\n";
        fs::write(hdir.join("換行結案線.md"), crlf).unwrap();

        // Missing created note (Safety Scenario 2)
        let legacy = "---\nthread: 缺created結案線\nproject: my-org--my-repo\nstate: open\nauthor: user\nupdated: 2026-11-02T10:00:00Z\n---\n\n## Next\n- 缺少 created 欄位\n";
        fs::write(hdir.join("缺created結案線.md"), legacy).unwrap();
    }

    // 1. Done on CRLF note
    let (py, rs) = env.run_both(&["done", "換行結案線"], &[]);
    env.assert_matches(&py, &rs);

    // 2. Done on missing created note
    let (py, rs) = env.run_both(&["done", "缺created結案線"], &[]);
    env.assert_matches(&py, &rs);

    // 3. Rewrite over missing created note (which is now done): created should be preserved
    let (py, rs) = env.run_both(
        &["write", "缺created結案線", "新進度重寫"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_force_flag_positions() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Author writes note
    let (py, rs) = env.run_both(
        &["write", "旗標位置", "第一版"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);

    // Someone else claims it
    let (py, rs) = env.run_both(
        &["claim", "旗標位置"],
        &[("CLAUDE_CODE_SESSION_ID", "taker-sess")],
    );
    env.assert_matches(&py, &rs);

    // Author overwrites with -f at end (Safety Scenario 3)
    let (py, rs) = env.run_both(
        &["write", "旗標位置", "第二版旗標在後", "-f"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);

    // Taker claims it again
    let (py, rs) = env.run_both(
        &["claim", "旗標位置"],
        &[("CLAUDE_CODE_SESSION_ID", "taker-sess")],
    );
    env.assert_matches(&py, &rs);

    // Author overwrites with --force in front
    let (py, rs) = env.run_both_raw(
        &["--force", "memory", "handoff", "write", "旗標位置", "第三版旗標在前"],
        &[("CLAUDE_CODE_SESSION_ID", "author-sess")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_claimed_note_taker_can_overwrite_without_force() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Session 1 writes note
    let (py, rs) = env.run_both(
        &["write", "接手人繼續寫", "作者第一版"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-author")],
    );
    env.assert_matches(&py, &rs);

    // Session 2 setup session name and claims note
    env.setup_session("sess-taker-1", "worker-bob", None);
    let (py, rs) = env.run_both(
        &["claim", "接手人繼續寫"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-taker-1")],
    );
    env.assert_matches(&py, &rs);

    // Session 2 overwrites its OWN claimed thread without --force: allowed! (Safety Scenario 3)
    let (py, rs) = env.run_both(
        &["write", "接手人繼續寫", "接手者自己寫第二版"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-taker-1")],
    );
    env.assert_matches(&py, &rs);

    // Session 2 claims it again
    let (py, rs) = env.run_both(
        &["claim", "接手人繼續寫"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-taker-1")],
    );
    env.assert_matches(&py, &rs);

    // After /clear, session ID changes to sess-taker-2, but session name remains "worker-bob"
    env.setup_session("sess-taker-2", "worker-bob", None);
    let (py, rs) = env.run_both(
        &["write", "接手人繼續寫", "清空後同名session寫第三版"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-taker-2")],
    );
    env.assert_matches(&py, &rs);

    // Session 2 claims it again
    let (py, rs) = env.run_both(
        &["claim", "接手人繼續寫"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-taker-2")],
    );
    env.assert_matches(&py, &rs);

    // Stranger with different session ID and no matching session name tries to overwrite: refused! (Safety Scenario 3)
    let (py, rs) = env.run_both(
        &["write", "接手人繼續寫", "陌生人試圖搶線"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-stranger")],
    );
    env.assert_matches(&py, &rs);
}

#[test]
fn test_compare_list_other_project_path() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let other_dir_py = env.py_work.join("other_project");
    let other_dir_rs = env.rs_work.join("other_project");
    fs::create_dir_all(&other_dir_py).unwrap();
    fs::create_dir_all(&other_dir_rs).unwrap();

    Command::new("git").arg("init").current_dir(&other_dir_py).output().unwrap();
    Command::new("git").args(["remote", "add", "origin", "https://github.com/other-org/other-repo.git"]).current_dir(&other_dir_py).output().unwrap();

    Command::new("git").arg("init").current_dir(&other_dir_rs).output().unwrap();
    Command::new("git").args(["remote", "add", "origin", "https://github.com/other-org/other-repo.git"]).current_dir(&other_dir_rs).output().unwrap();

    // In current project, write note
    let (py, rs) = env.run_both(&["write", "本專案線", "內容A"], &[]);
    env.assert_matches(&py, &rs);

    // In other project, write note
    for (data, _other_work) in [(&env.py_data, &other_dir_py), (&env.rs_data, &other_dir_rs)] {
        let note = "---\nthread: 別專案線\nproject: other-org--other-repo\nstate: open\nauthor: user\ncreated: 2026-11-02T10:00:00Z\nupdated: 2026-11-02T10:00:00Z\n---\n\n內容B\n";
        fs::write(data.join("memory").join("handoff").join("別專案線.md"), note).unwrap();
    }

    // List other project via argument
    let py_path_arg = other_dir_py.to_str().unwrap();
    let rs_path_arg = other_dir_rs.to_str().unwrap();

    let py_bin = python_executable();
    let rs_bin = PathBuf::from(env!("CARGO_BIN_EXE_acg"));
    let root = repo_root();

    let clean_path = env.clean_path();

    let mut py_cmd = Command::new(&py_bin);
    py_cmd.args(["-m", "ai_config", "memory", "handoff", "list", py_path_arg]);
    py_cmd.current_dir(&env.py_work);
    py_cmd.env("PATH", &clean_path);
    py_cmd.env("AI_CONFIG_REPO", &env.py_data);
    py_cmd.env("HOME", &env.py_home);
    py_cmd.env("USERPROFILE", &env.py_home);
    py_cmd.env("PYTHONPATH", &root);
    py_cmd.env("AI_CONFIG_ENTRYPOINT", "./ai-config.sh");
    py_cmd.env("NO_COLOR", "1");
    let py_res = py_cmd.output().expect("Failed Python");

    let mut rs_cmd = Command::new(&rs_bin);
    rs_cmd.args(["memory", "handoff", "list", rs_path_arg]);
    rs_cmd.current_dir(&env.rs_work);
    rs_cmd.env("PATH", &clean_path);
    rs_cmd.env("AI_CONFIG_REPO", &env.rs_data);
    rs_cmd.env("HOME", &env.rs_home);
    rs_cmd.env("USERPROFILE", &env.rs_home);
    rs_cmd.env("AI_CONFIG_ENTRYPOINT", "./ai-config.sh");
    rs_cmd.env("NO_COLOR", "1");
    let rs_res = rs_cmd.output().expect("Failed Rust");

    env.assert_matches(&py_res, &rs_res);
    let py_out = String::from_utf8_lossy(&py_res.stdout).replace("\r\n", "\n");
    let rs_out = String::from_utf8_lossy(&rs_res.stdout).replace("\r\n", "\n");
    assert!(py_out.contains("別專案線") && !py_out.contains("本專案線"));
    assert!(rs_out.contains("別專案線") && !rs_out.contains("本專案線"));
}

#[test]
fn test_compare_path_containment_and_traversal_prevention() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // 1. Thread name with directory traversal: sanitized to clean filename inside handoff dir (Safety Scenario 5)
    let (py, rs) = env.run_both(
        &["write", "../../escaped_thread", "內容不受父層逃逸影響"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-one")],
    );
    env.assert_matches(&py, &rs);

    // 2. Thread name with slashes and colons: sanitized to clean filename (Safety Scenario 5)
    let (py, rs) = env.run_both(
        &["write", "nested/slash\\colon:thread", "路徑分隔符被轉換"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-one")],
    );
    env.assert_matches(&py, &rs);

    // 3. Thread name with only dots fallback to 'thread' (Safety Scenario 5)
    let (py, rs) = env.run_both(
        &["write", "..", "點點名稱退回thread"],
        &[("CLAUDE_CODE_SESSION_ID", "sess-one")],
    );
    env.assert_matches(&py, &rs);
}

#[cfg(unix)]
#[test]
fn test_compare_reparse_point_symlink_refusal() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Create a regular target outside handoff dir, and symlink to it inside handoff dir (Safety Scenario 5)
    for data in [&env.py_data, &env.rs_data] {
        let external = data.join("external_target.md");
        fs::write(&external, "---\nthread: symlink_thread\nproject: my-org--my-repo\nstate: open\nauthor: u\ncreated: 2026-11-02T10:00:00Z\nupdated: 2026-11-02T10:00:00Z\n---\n\n目標內容\n").unwrap();

        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let symlink_path = hdir.join("symlink_thread.md");
        std::os::unix::fs::symlink(&external, &symlink_path).unwrap();
    }

    // 1. List: symlink is ignored / skipped by both Python and Rust
    let (py_list, rs_list) = env.run_both(&["list"], &[]);
    env.assert_matches(&py_list, &rs_list);
    let py_stdout = String::from_utf8_lossy(&py_list.stdout);
    assert!(!py_stdout.contains("symlink_thread"));

    // 2. Claim symlinked note: refused with "Refusing reparse point:" and exit code 1
    let (py_claim, rs_claim) = env.run_both(&["claim", "symlink_thread"], &[]);
    env.assert_matches(&py_claim, &rs_claim);
    assert_eq!(py_claim.status.code(), Some(1));
    assert_eq!(rs_claim.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py_claim.stderr);
    let rs_err = String::from_utf8_lossy(&rs_claim.stderr);
    assert!(py_err.contains("Refusing reparse point:"));
    assert!(rs_err.contains("Refusing reparse point:"));

    // 3. Done symlinked note: refused with "Refusing reparse point:" and exit code 1
    let (py_done, rs_done) = env.run_both(&["done", "symlink_thread"], &[]);
    env.assert_matches(&py_done, &rs_done);
    assert_eq!(py_done.status.code(), Some(1));
    assert_eq!(rs_done.status.code(), Some(1));

    // 4. Write to symlinked note: refused with "Refusing reparse point:" and exit code 1
    let (py_write, rs_write) = env.run_both(&["write", "symlink_thread", "覆寫符號連結"], &[]);
    env.assert_matches(&py_write, &rs_write);
    assert_eq!(py_write.status.code(), Some(1));
    assert_eq!(rs_write.status.code(), Some(1));
}

#[cfg(unix)]
#[test]
fn test_compare_reparse_point_parent_refusal() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Replace handoff directory with a symlink to an external folder (Safety Scenario 5)
    for data in [&env.py_data, &env.rs_data] {
        let mem_dir = data.join("memory");
        fs::create_dir_all(&mem_dir).unwrap();
        let target_dir = data.join("real_handoff");
        fs::create_dir_all(&target_dir).unwrap();
        let handoff_symlink = mem_dir.join("handoff");
        std::os::unix::fs::symlink(&target_dir, &handoff_symlink).unwrap();
    }

    // Write: refused because handoff parent itself is a symlink/reparse point
    let (py, rs) = env.run_both(&["write", "安全測試線", "內容"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("Refusing reparse point"));
    assert!(rs_err.contains("Refusing reparse point"));
}

#[test]
fn test_rust_unimplemented_commands_error() {
    let env = TestEnv::new();
    let rs_bin = PathBuf::from(env!("CARGO_BIN_EXE_acg"));

    let unimplemented_cases = [
        ("status", "Command not yet implemented in Rust: status"),
        ("apply", "Command not yet implemented in Rust: apply"),
        ("init", "Command not yet implemented in Rust: init"),
        ("push", "Command not yet implemented in Rust: push"),
        ("pull", "Command not yet implemented in Rust: pull"),
        ("sync", "Command not yet implemented in Rust: sync"),
        ("project", "Command not yet implemented in Rust: project"),
        ("list", "Command not yet implemented in Rust: list"),
        ("package", "Command not yet implemented in Rust: package"),
        ("reset", "Command not yet implemented in Rust: reset"),
        ("deploy", "Command not yet implemented in Rust: deploy"),
    ];

    for (cmd, expected_msg) in unimplemented_cases {
        let output = Command::new(&rs_bin)
            .arg(cmd)
            .current_dir(&env.rs_work)
            .env("AI_CONFIG_REPO", &env.rs_data)
            .env("HOME", &env.rs_home)
            .env("NO_COLOR", "1")
            .output()
            .unwrap();

        assert_eq!(output.status.code(), Some(1), "Expected non-zero exit for {cmd}");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(expected_msg),
            "Expected stderr to contain '{expected_msg}', got: {stderr}"
        );
    }

    // Test unimplemented memory subcommands
    let memory_cases: [(&[&str], &str); 3] = [
        (&["memory"], "Command not yet implemented in Rust: memory"),
        (&["memory", "status"], "Command not yet implemented in Rust: memory status"),
        (&["memory", "enable", "codex"], "Command not yet implemented in Rust: memory enable"),
    ];

    for (args, expected_msg) in memory_cases {
        let output = Command::new(&rs_bin)
            .args(args)
            .current_dir(&env.rs_work)
            .env("AI_CONFIG_REPO", &env.rs_data)
            .env("HOME", &env.rs_home)
            .env("NO_COLOR", "1")
            .output()
            .unwrap();

        assert_eq!(output.status.code(), Some(1), "Expected non-zero exit for {args:?}");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(expected_msg),
            "Expected stderr to contain '{expected_msg}', got: {stderr}"
        );
    }

    // Test memory handoff remind (remind is explicitly not done in stage 1)
    let output = Command::new(&rs_bin)
        .args(["memory", "handoff", "remind"])
        .current_dir(&env.rs_work)
        .env("AI_CONFIG_REPO", &env.rs_data)
        .env("HOME", &env.rs_home)
        .env("NO_COLOR", "1")
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1), "Expected non-zero exit for remind");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("Command not yet implemented in Rust: memory handoff remind"),
        "Expected stderr to contain 'Command not yet implemented in Rust: memory handoff remind', got: {stderr}"
    );
}

#[cfg(unix)]
#[test]
fn test_compare_archive_dir_is_symlink() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();

        // An old note ready to be archived (40 days old)
        let old_note = "---\nthread: 待封存\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: 2026-09-01T10:00:00Z\nupdated: 2026-09-01T10:00:00Z\n---\n\n舊筆記\n";
        fs::write(hdir.join("待封存.md"), old_note).unwrap();

        // Target external dir for symlink
        let external = data.join("external_archive_target");
        fs::create_dir_all(&external).unwrap();

        // archive directory is a symlink!
        let arch_symlink = hdir.join("archive");
        std::os::unix::fs::symlink(&external, &arch_symlink).unwrap();
    }

    // List triggers archive_finished, which should refuse reparse point parent and exit 1
    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("Refusing reparse point parent:"));
    assert!(rs_err.contains("Refusing reparse point parent:"));
}


#[test]
fn test_compare_claim_own_thread_named_elsewhere() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    // Session worker-alpha writes a note in a DIFFERENT project
    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let other_note = "---\nthread: 別處留下的線\nproject: other-org--other-repo\nstate: open\nauthor: u\nsession_name: worker-alpha\ncreated: 2026-11-02T10:00:00Z\nupdated: 2026-11-02T10:00:00Z\n---\n\n別處的筆記內容\n";
        fs::write(hdir.join("別處留下的線.md"), other_note).unwrap();
    }

    // Current session is worker-alpha in my-org--my-repo
    env.setup_session("sess-new-123", "worker-alpha", None);

    // Claim with NO argument: should find the thread in the other project and notify
    let (py, rs) = env.run_both(&["claim"], &[("CLAUDE_CODE_SESSION_ID", "sess-new-123")]);
    env.assert_matches(&py, &rs);
    let py_out = String::from_utf8_lossy(&py.stdout);
    assert!(py_out.contains("這條線記在專案 other-org--other-repo,不是這個 session 的專案"));
}

#[test]
fn test_compare_claim_own_thread_multiple_candidates_conflict() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let note1 = "---\nthread: 線一\nproject: my-org--my-repo\nstate: open\nauthor: u\nsession_name: busy-worker\ncreated: 2026-11-02T10:00:01Z\nupdated: 2026-11-02T10:00:01Z\n---\n\n進度一\n";
        let note2 = "---\nthread: 線二\nproject: my-org--my-repo\nstate: open\nauthor: u\nsession_name: busy-worker\ncreated: 2026-11-02T10:00:02Z\nupdated: 2026-11-02T10:00:02Z\n---\n\n進度二\n";
        fs::write(hdir.join("線一.md"), note1).unwrap();
        fs::write(hdir.join("線二.md"), note2).unwrap();
    }

    env.setup_session("sess-busy", "busy-worker", None);

    // Claim with NO arguments when multiple open threads match session_name: should error
    let (py, rs) = env.run_both(&["claim"], &[("CLAUDE_CODE_SESSION_ID", "sess-busy")]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("session「busy-worker」留下不只一條線:"));
    assert!(rs_err.contains("session「busy-worker」留下不只一條線:"));
}

#[test]
fn test_compare_claim_own_thread_no_matching_thread() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    env.setup_session("sess-empty", "lonely-worker", None);

    // Claim with NO arguments when no open threads match: should error
    let (py, rs) = env.run_both(&["claim"], &[("CLAUDE_CODE_SESSION_ID", "sess-empty")]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("沒有 session「lonely-worker」留下的工作線,請指定要認領的工作線"));
    assert!(rs_err.contains("沒有 session「lonely-worker」留下的工作線,請指定要認領的工作線"));
}

#[cfg(unix)]
#[test]
fn test_compare_symlinked_data_dir() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let py_symlink = env.py_data.parent().unwrap().join("py_data_symlink");
    let rs_symlink = env.rs_data.parent().unwrap().join("rs_data_symlink");
    std::os::unix::fs::symlink(&env.py_data, &py_symlink).unwrap();
    std::os::unix::fs::symlink(&env.rs_data, &rs_symlink).unwrap();

    // 1. Write through symlinked data repo
    let (py_write, rs_write) = env.run_both_with_data_repos(
        &["write", "符號連結線", "資料目錄經過symlink依然正常解析"],
        &py_symlink,
        &rs_symlink,
        &[("CLAUDE_CODE_SESSION_ID", "author-sym")],
    );
    env.assert_matches_extra(&py_write, &rs_write, &[&py_symlink, &rs_symlink]);
    assert_eq!(py_write.status.code(), Some(0));
    assert_eq!(rs_write.status.code(), Some(0));

    // 2. List through symlinked data repo
    let (py_list, rs_list) = env.run_both_with_data_repos(
        &["list"],
        &py_symlink,
        &rs_symlink,
        &[],
    );
    env.assert_matches_extra(&py_list, &rs_list, &[&py_symlink, &rs_symlink]);
    assert_eq!(py_list.status.code(), Some(0));
    assert_eq!(rs_list.status.code(), Some(0));
    let py_out = String::from_utf8_lossy(&py_list.stdout);
    assert!(py_out.contains("符號連結線"));

    // 3. Claim through symlinked data repo
    let (py_claim, rs_claim) = env.run_both_with_data_repos(
        &["claim", "符號連結線"],
        &py_symlink,
        &rs_symlink,
        &[("CLAUDE_CODE_SESSION_ID", "taker-sym")],
    );
    env.assert_matches_extra(&py_claim, &rs_claim, &[&py_symlink, &rs_symlink]);
    assert_eq!(py_claim.status.code(), Some(0));
    assert_eq!(rs_claim.status.code(), Some(0));

    // 4. Done through symlinked data repo
    let (py_done, rs_done) = env.run_both_with_data_repos(
        &["done", "符號連結線"],
        &py_symlink,
        &rs_symlink,
        &[("CLAUDE_CODE_SESSION_ID", "taker-sym")],
    );
    env.assert_matches_extra(&py_done, &rs_done, &[&py_symlink, &rs_symlink]);
    assert_eq!(py_done.status.code(), Some(0));
    assert_eq!(rs_done.status.code(), Some(0));
}

#[cfg(windows)]
fn create_junction(link: &Path, target: &Path) {
    let status = Command::new("cmd")
        .args(["/C", "mklink", "/J", link.to_str().unwrap(), target.to_str().unwrap()])
        .status()
        .expect("Failed to execute mklink /J");
    assert!(status.success(), "mklink /J failed");
}

#[cfg(windows)]
#[test]
fn test_compare_junction_parent_refusal() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let mem_dir = data.join("memory");
        fs::create_dir_all(&mem_dir).unwrap();
        let target_dir = data.join("real_handoff");
        fs::create_dir_all(&target_dir).unwrap();
        let handoff_junction = mem_dir.join("handoff");
        create_junction(&handoff_junction, &target_dir);
    }

    let (py, rs) = env.run_both(&["write", "安全測試線", "內容"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
}

#[cfg(windows)]
#[test]
fn test_compare_archive_dir_is_junction() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let now = chrono::Utc::now();
    let old_time = (now - chrono::Duration::days(45)).format("%Y-%m-%dT%H:%M:%SZ").to_string();

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let old_note = format!("---\nthread: 舊線\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: {old_time}\nupdated: {old_time}\n---\n\n舊筆記\n");
        fs::write(hdir.join("舊線.md"), old_note).unwrap();

        let external = data.join("external_archive_target");
        fs::create_dir_all(&external).unwrap();

        let arch_junction = hdir.join("archive");
        create_junction(&arch_junction, &external);
    }

    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
}

#[cfg(windows)]
#[test]
fn test_compare_junction_data_dir() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let py_junction = env.py_data.parent().unwrap().join("py_data_junction");
    let rs_junction = env.rs_data.parent().unwrap().join("rs_data_junction");
    create_junction(&py_junction, &env.py_data);
    create_junction(&rs_junction, &env.rs_data);

    let (py_write, rs_write) = env.run_both_with_data_repos(
        &["write", "節點連結線", "資料目錄經過junction依然正常解析"],
        &py_junction,
        &rs_junction,
        &[("CLAUDE_CODE_SESSION_ID", "author-junc")],
    );
    env.assert_matches_extra(&py_write, &rs_write, &[&py_junction, &rs_junction]);
    assert_eq!(py_write.status.code(), Some(0));
    assert_eq!(rs_write.status.code(), Some(0));

    let (py_list, rs_list) = env.run_both_with_data_repos(
        &["list"],
        &py_junction,
        &rs_junction,
        &[],
    );
    env.assert_matches_extra(&py_list, &rs_list, &[&py_junction, &rs_junction]);
    assert_eq!(py_list.status.code(), Some(0));
    assert_eq!(rs_list.status.code(), Some(0));
}

#[test]
fn test_compare_archive_dir_is_file_ignored_in_list() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let now = chrono::Utc::now();
    let old_time = (now - chrono::Duration::days(45)).format("%Y-%m-%dT%H:%M:%SZ").to_string();

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let old_note = format!("---\nthread: 舊線\nproject: my-org--my-repo\nstate: done\nauthor: user\ncreated: {old_time}\nupdated: {old_time}\n---\n\n舊筆記\n");
        fs::write(hdir.join("舊線.md"), old_note).unwrap();

        // archive is a regular file, not a dir
        fs::write(hdir.join("archive"), "a regular file, not a dir").unwrap();
    }

    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
}

#[test]
fn test_compare_corrupt_config_stops_with_error() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let bad_config = env.py_home.join("bad_config.json");
    fs::write(&bad_config, "{invalid_json: true").unwrap();

    let extra_env = [("AI_CONFIG_CONFIG", bad_config.to_str().unwrap()), ("AI_CONFIG_REPO", "")];
    let (py, rs) = env.run_both_raw(&["list"], &extra_env);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("Cannot read configuration file"));
    assert!(rs_err.contains("Cannot read configuration file"));
    let py_out = String::from_utf8_lossy(&py.stdout);
    let rs_out = String::from_utf8_lossy(&rs.stdout);
    assert!(py_out.contains("setup to replace the invalid configuration"));
    assert!(rs_out.contains("setup to replace the invalid configuration"));
}

#[test]
fn test_compare_missing_claude_directory_in_data_repo() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let empty_py_data = env.py_home.join("empty_data");
    let empty_rs_data = env.rs_home.join("empty_data");
    fs::create_dir_all(&empty_py_data).unwrap();
    fs::create_dir_all(&empty_rs_data).unwrap();

    let (py, rs) = env.run_both_with_data_repos(
        &["list"],
        &empty_py_data,
        &empty_rs_data,
        &[],
    );
    env.assert_matches_extra(&py, &rs, &[&empty_py_data, &empty_rs_data]);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("Repository configuration directory not found"));
    assert!(rs_err.contains("Repository configuration directory not found"));
}

#[test]
fn test_compare_claim_whitespace_fails() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    let (py, rs) = env.run_both(&["claim", "   "], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(1));
    assert_eq!(rs.status.code(), Some(1));
    let py_err = String::from_utf8_lossy(&py.stderr);
    let rs_err = String::from_utf8_lossy(&rs.stderr);
    assert!(py_err.contains("沒有這則交接:   "));
    assert!(rs_err.contains("沒有這則交接:   "));
}

#[test]
fn test_compare_empty_thread_field() {
    let env = TestEnv::new();
    env.init_git_repo("https://github.com/my-org/my-repo.git");

    for data in [&env.py_data, &env.rs_data] {
        let hdir = data.join("memory").join("handoff");
        fs::create_dir_all(&hdir).unwrap();
        let note = "---\nthread:\nproject: my-org--my-repo\nstate: open\nauthor: u\ncreated: 2026-11-05T10:00:00Z\nupdated: 2026-11-05T10:00:00Z\n---\n\n內容\n";
        fs::write(hdir.join("空工作線.md"), note).unwrap();
    }

    let (py, rs) = env.run_both(&["list"], &[]);
    env.assert_matches(&py, &rs);
    assert_eq!(py.status.code(), Some(0));
    assert_eq!(rs.status.code(), Some(0));
}

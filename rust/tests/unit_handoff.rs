use acg::handoff::*;
use acg::safety::*;

#[test]
fn test_slug_generation() {
    assert_eq!(slug("記憶改善"), "記憶改善");
    assert_eq!(slug("含 空格 的線"), "含 空格 的線");
    assert_eq!(slug("hello/world\\test:foo*bar?baz\"qux<one>two|three"), "hello-world-test-foo-bar-baz-qux-one-two-three");
    assert_eq!(slug("   ..trim-me--   "), "trim-me");
    assert_eq!(slug("..."), "thread");
    assert_eq!(slug(""), "thread");
    assert_eq!(slug("   "), "thread");
    assert_eq!(slug("."), "thread");
    assert_eq!(slug(".."), "thread");
}

#[test]
fn test_short_id() {
    assert_eq!(short_id("d4b49a91-38e2-4f31-900a-24d0ae63b153", 8), "d4b49a91");
    assert_eq!(short_id("session-one", 8), "session");
    assert_eq!(short_id("session_one", 8), "session");
    assert_eq!(short_id("abc", 8), "abc");
    assert_eq!(short_id("12345678", 8), "12345678");
}

#[test]
fn test_parse_and_render_roundtrip() {
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("test.md");

    let note = Handoff {
        path: note_path.clone(),
        thread: "測試線".to_string(),
        project: "owner--repo".to_string(),
        state: "open".to_string(),
        author: "session-1".to_string(),
        claimed_by: "".to_string(),
        session_name: "test-sess".to_string(),
        claimed_name: "".to_string(),
        created: "2026-11-02T10:00:00Z".to_string(),
        updated: "2026-11-02T10:00:00Z".to_string(),
        body: "第一行進度\n第二行說明".to_string(),
    };

    let rendered = render(&note);
    std::fs::write(&note_path, &rendered).unwrap();

    let parsed = parse(&note_path).unwrap().expect("Should parse");
    assert_eq!(parsed.thread, note.thread);
    assert_eq!(parsed.project, note.project);
    assert_eq!(parsed.state, note.state);
    assert_eq!(parsed.author, note.author);
    assert_eq!(parsed.claimed_by, note.claimed_by);
    assert_eq!(parsed.session_name, note.session_name);
    assert_eq!(parsed.claimed_name, note.claimed_name);
    assert_eq!(parsed.created, note.created);
    assert_eq!(parsed.updated, note.updated);
    assert_eq!(parsed.body, note.body);
}

#[test]
fn test_parse_crlf_tolerance() {
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("crlf.md");

    let text = "---\r\nthread: 跨平台\r\nproject: proj\r\nstate: open\r\nauthor: user\r\ncreated: 2026-11-02T10:00:00Z\r\nupdated: 2026-11-02T10:00:00Z\r\n---\r\n\r\n內容\r\n";
    std::fs::write(&note_path, text).unwrap();

    let parsed = parse(&note_path).unwrap().expect("Should parse CRLF note");
    assert_eq!(parsed.thread, "跨平台");
    assert_eq!(parsed.project, "proj");
    assert_eq!(parsed.body, "內容");
}

#[test]
fn test_parse_missing_created_fallback_to_updated() {
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("legacy.md");

    let text = "---\nthread: 舊線\nproject: proj\nstate: open\nauthor: user\nupdated: 2026-10-01T12:00:00Z\n---\n\n舊內容\n";
    std::fs::write(&note_path, text).unwrap();

    let parsed = parse(&note_path).unwrap().expect("Should parse legacy note");
    assert_eq!(parsed.created, "2026-10-01T12:00:00Z");
    assert_eq!(parsed.updated, "2026-10-01T12:00:00Z");
}

#[test]
fn test_parse_legacy_claimed_state_becomes_open() {
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("claimed.md");

    let text = "---\nthread: 持有中\nproject: proj\nstate: claimed\nauthor: user\nupdated: 2026-10-01T12:00:00Z\n---\n\n內容\n";
    std::fs::write(&note_path, text).unwrap();

    let parsed = parse(&note_path).unwrap().expect("Should parse claimed note");
    assert_eq!(parsed.state, "open");
}

#[test]
fn test_parse_empty_field_does_not_swallow_next_line() {
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("empty_author.md");

    let text = "---\nthread: 無人署名\nproject: proj\nstate: open\nauthor: \ncreated: 2026-11-02T10:00:00Z\nupdated: 2026-11-02T10:00:00Z\n---\n\n內容\n";
    std::fs::write(&note_path, text).unwrap();

    let parsed = parse(&note_path).unwrap().expect("Should parse note with empty author");
    assert_eq!(parsed.author, "");
    assert_eq!(parsed.created, "2026-11-02T10:00:00Z");
}

#[test]
fn test_summary_extraction() {
    let body_with_next = "## Goal\n做完 X\n\n## Next\n- 補上聲紋註冊\n- 調 bitrate";
    assert_eq!(summary(body_with_next, 70), "還剩:補上聲紋註冊");

    let body_with_bold = "## Next\n1. **優先：push 並開 PR。** 五個 commit 還在本機。\n";
    assert_eq!(summary(body_with_bold, 70), "還剩:1. 優先：push 並開 PR。 五個 commit 還在本機。");

    let body_with_unknowns = "## Unknowns\n- 待確認 API 格式\n";
    assert_eq!(summary(body_with_unknowns, 70), "未定:待確認 API 格式");

    let body_heading_fallback = "## 這條線在做什麼\n\n追 MVP114 的 KPI 差異\n";
    assert_eq!(summary(body_heading_fallback, 70), "追 MVP114 的 KPI 差異");

    let body_plain = "第一行就是摘要\n後面還有別的";
    assert_eq!(summary(body_plain, 70), "第一行就是摘要");
}

#[test]
fn test_assert_plain_path() {
    let tmp = tempfile::tempdir().unwrap();
    let canonical_tmp = acg::paths::resolve_like_python(tmp.path());
    let file_path = canonical_tmp.join("regular.txt");
    std::fs::write(&file_path, "hello").unwrap();

    assert!(assert_plain_path(&file_path, Some(false)).is_ok());
    assert!(assert_plain_path(&file_path, Some(true)).is_err());
    assert!(assert_plain_path(&canonical_tmp, Some(true)).is_ok());

    #[cfg(unix)]
    {
        let link_path = canonical_tmp.join("symlink.txt");
        std::os::unix::fs::symlink(&file_path, &link_path).unwrap();
        assert!(assert_plain_path(&link_path, None).is_err());
    }
}

#[test]
fn test_strip_verbatim_prefix() {
    use acg::paths::strip_verbatim_prefix;
    use std::path::Path;

    assert_eq!(
        strip_verbatim_prefix(Path::new(r"\\?\C:\Users\foo")),
        Path::new(r"C:\Users\foo")
    );
    assert_eq!(
        strip_verbatim_prefix(Path::new(r"\\?\UNC\server\share\foo")),
        Path::new(r"\\server\share\foo")
    );
    assert_eq!(
        strip_verbatim_prefix(Path::new("/tmp/normal/path")),
        Path::new("/tmp/normal/path")
    );
}

#[test]
fn test_resolve_like_python() {
    use acg::paths::resolve_like_python;
    let tmp = tempfile::tempdir().unwrap();

    // Resolving an existing path
    let existing = tmp.path().join("existing");
    std::fs::create_dir_all(&existing).unwrap();
    let resolved = resolve_like_python(&existing);
    assert!(resolved.is_absolute());
    assert!(resolved.exists());

    // Resolving a non-existent child under an existing parent
    let nonexistent = existing.join("a").join("b").join("c");
    let resolved_nonexistent = resolve_like_python(&nonexistent);
    assert!(resolved_nonexistent.is_absolute());
    assert!(resolved_nonexistent.ends_with("a/b/c") || resolved_nonexistent.ends_with(r"a\b\c"));

    // Resolving with dotdot in non-existent tail
    let with_dotdot = existing.join("a").join("..").join("b");
    let resolved_dotdot = resolve_like_python(&with_dotdot);
    assert!(resolved_dotdot.ends_with("b"));
    assert!(!resolved_dotdot.to_string_lossy().contains(".."));

    #[cfg(unix)]
    {
        let symlink_path = tmp.path().join("symlink_to_existing");
        std::os::unix::fs::symlink(&existing, &symlink_path).unwrap();

        let resolved_sym = resolve_like_python(&symlink_path);
        assert_eq!(resolved_sym, resolved);

        let sub_sym = symlink_path.join("sub_file");
        let resolved_sub = resolve_like_python(&sub_sym);
        assert_eq!(resolved_sub, resolved.join("sub_file"));
    }
}

#[test]
fn test_short_id_unicode_characters() {
    assert_eq!(short_id("測試用工作階段名稱", 8), "測試用工作階段名稱".chars().take(8).collect::<String>());
    assert_eq!(short_id("測試-工作階段", 8), "測試-工作階段");
    assert_eq!(short_id("測試工作階段名稱超出長度", 8), "測試工作階段名稱");
    assert_eq!(short_id("測試_工作_階段_超長", 8), "測試_工作_階段");
    assert_eq!(short_id("短", 8), "短");
    assert_eq!(short_id("一二三四五六七八", 8), "一二三四五六七八");
}

#[test]
fn test_timestamp_strict_utc_format() {
    assert!(parse_timestamp("2026-11-05T12:00:00Z").is_some());
    // Offsets must be rejected (matching Python %Y-%m-%dT%H:%M:%SZ)
    assert!(parse_timestamp("2026-11-05T12:00:00+08:00").is_none());
    assert!(parse_timestamp("2026-11-05T12:00:00-05:00").is_none());
    // Subseconds must be rejected
    assert!(parse_timestamp("2026-11-05T12:00:00.123Z").is_none());
    assert!(parse_timestamp("2026-11-05 12:00:00").is_none());
    assert!(parse_timestamp("invalid").is_none());
}

#[cfg(unix)]
#[test]
fn test_umask_applied_on_new_file() {
    use std::os::unix::fs::PermissionsExt;
    let tmp = tempfile::tempdir().unwrap();
    let note_path = tmp.path().join("umask_test.md");

    struct UmaskGuard(u32);
    impl Drop for UmaskGuard {
        fn drop(&mut self) {
            unsafe { libc::umask(self.0 as libc::mode_t); }
        }
    }
    let old = unsafe { libc::umask(0o077) };
    let _guard = UmaskGuard(old as u32);

    write_text_atomic(&note_path, "content").unwrap();
    let meta = std::fs::metadata(&note_path).unwrap();
    let mode = meta.permissions().mode() & 0o777;
    assert_eq!(mode, 0o600, "Expected mode 0600 under umask 077, got {:o}", mode);
}

#[test]
fn test_summary_heading_regex_and_empty_first_line() {
    let body1 = "   ## Not a heading\nSome text";
    assert_eq!(summary(body1, 70), "## Not a heading");

    let body2 = "## Next\n\n* Real item";
    assert_eq!(summary(body2, 70), "還剩:Real item");
}

#[test]
fn test_corrupt_config_returns_err() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = tmp.path().join("config.json");
    std::fs::write(&cfg, "{corrupted json").unwrap();
    std::env::set_var("AI_CONFIG_CONFIG", cfg.to_str().unwrap());
    std::env::remove_var("AI_CONFIG_REPO");

    let result = acg::paths::data_dir();
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("Cannot read configuration file"));

    std::env::remove_var("AI_CONFIG_CONFIG");
}

#[test]
fn test_rust_corrupted_note_refused_in_write_claim_done() {
    let tmp = tempfile::tempdir().unwrap();
    let data_repo = tmp.path().join("data");
    let hdir = data_repo.join("memory").join("handoff");
    std::fs::create_dir_all(&hdir).unwrap();
    std::env::set_var("AI_CONFIG_REPO", &data_repo);

    std::fs::write(hdir.join("壞線.md"), [0xff, 0xfe, 0x00, 0x00, 0x80, 0x81]).unwrap();

    // 1. load_all skips corrupted note
    let all = load_all(None).unwrap();
    assert!(all.is_empty());

    // 2. write without force refuses
    let w_err = write("壞線", "不加force應該拒絕", None, false).unwrap_err();
    assert_eq!(w_err, "交接格式不正確:壞線");

    // 3. claim refuses
    let c_err = claim(Some("壞線")).unwrap_err();
    assert_eq!(c_err, "交接格式不正確:壞線");

    // 4. done refuses
    let d_err = done("壞線").unwrap_err();
    assert_eq!(d_err, "交接格式不正確:壞線");

    // 5. write with force succeeds
    let note = write("壞線", "加force覆寫", None, true).unwrap();
    assert_eq!(note.thread, "壞線");
    assert_eq!(note.body, "加force覆寫");

    std::env::remove_var("AI_CONFIG_REPO");
}

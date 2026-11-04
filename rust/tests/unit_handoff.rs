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
    let file_path = tmp.path().join("regular.txt");
    std::fs::write(&file_path, "hello").unwrap();

    assert!(assert_plain_path(&file_path, Some(false)).is_ok());
    assert!(assert_plain_path(&file_path, Some(true)).is_err());
    assert!(assert_plain_path(tmp.path(), Some(true)).is_ok());

    #[cfg(unix)]
    {
        let link_path = tmp.path().join("symlink.txt");
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

"""Subprocess contracts for local skill installation and complete removal."""

import os
from pathlib import Path

import pytest
from test_apply_projection import run_ai_config, write
from test_share import make_repo

REPO_ROOTS = (
    "claude/skills",
    "claude/shared/both",
    "claude/shared/codex",
    "claude/shared/agy",
    "codex/skills",
    "agy/skills",
)
LIVE_ROOTS = (
    ".claude/skills",
    ".agents/skills",
    ".codex/skills",
    ".gemini/config/skills",
    ".gemini/antigravity-cli/skills",
    ".gemini/antigravity/skills",
)


def source_skill(tmp_path: Path, name: str = "demo") -> Path:
    source = tmp_path / "downloaded-folder"
    write(
        source / "SKILL.md", f"---\nname: {name}\ndescription: Demo skill\n---\nBody\n"
    )
    return source


def roots(repo: Path, home: Path) -> list[Path]:
    return [repo / item for item in REPO_ROOTS] + [home / item for item in LIVE_ROOTS]


def assert_error(result, *words: str) -> None:
    output = (result.stdout + result.stderr).lower()
    assert result.returncode == 1, output
    assert any(word.lower() in output for word in words), output


def test_add_copies_complete_tree_without_running_scripts(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    write(source / "assets/deep/template.txt", "template\n")
    write(source / "notes.txt", "notes\n")
    sentinel = tmp_path / "script-executed"
    write(
        source / "scripts/install.py",
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
    )
    write(source / "scripts/install.sh", "#!/bin/sh\nexit 79\n")

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert result.returncode == 0, result.stderr + result.stdout
    assert not sentinel.exists()
    for root in (repo / "claude/skills", home / ".claude/skills"):
        for relative in (
            "SKILL.md",
            "assets/deep/template.txt",
            "notes.txt",
            "scripts/install.py",
            "scripts/install.sh",
        ):
            assert (root / "demo" / relative).read_bytes() == (
                source / relative
            ).read_bytes()
        assert not (root / source.name).exists()


def test_add_excludes_credentials_and_metadata_at_any_depth(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    excluded = (
        "auth.json",
        ".credentials.json",
        "gdrive_token.json",
        "google_accounts.json",
        "oauth_creds.json",
        "trustedFolders.json",
    )
    for prefix in ("", "references/deep/"):
        for name in excluded:
            write(source / f"{prefix}{name}", "private\n")
        write(source / f"{prefix}.git/config", "private metadata\n")
        write(source / f"{prefix}.ai-config/state.json", "private state\n")
    write(source / "references/guide.md", "safe\n")

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert result.returncode == 0, result.stderr + result.stdout
    for root in (repo / "claude/skills/demo", home / ".claude/skills/demo"):
        assert (root / "references/guide.md").read_text() == "safe\n"
        assert {
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        } == {
            "SKILL.md",
            "references/guide.md",
        }
    assert (source / "auth.json").read_text() == "private\n"


@pytest.mark.parametrize("location", ["repo", "live"])
@pytest.mark.parametrize("flags", [(), ("--force",)])
def test_add_never_overwrites(tmp_path: Path, location: str, flags: tuple) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    existing = repo / "claude/skills" if location == "repo" else home / ".claude/skills"
    write(existing / "demo/SKILL.md", "old\n")

    result = run_ai_config(repo, home, *flags, "skill", "add", str(source))

    assert_error(result, "exist", "overwrite")
    assert (existing / "demo/SKILL.md").read_text() == "old\n"
    other = (
        home / ".claude/skills/demo"
        if location == "repo"
        else repo / "claude/skills/demo"
    )
    assert not other.exists()


@pytest.mark.parametrize("kind", ["missing", "file", "no-skill"])
def test_add_rejects_invalid_sources(tmp_path: Path, kind: str) -> None:
    repo, home = make_repo(tmp_path)
    source = tmp_path / "invalid-source"
    if kind == "file":
        write(source, "not a directory\n")
    elif kind == "no-skill":
        source.mkdir()

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert_error(result, "directory", "not found", "exist", "skill.md")
    assert not (repo / "claude/skills").exists()
    assert not (home / ".claude/skills").exists()


@pytest.mark.parametrize(
    "frontmatter",
    [
        "# No frontmatter\n",
        "---\nname: demo\n---\nBody\n",
        "---\ndescription: Demo\n---\nBody\n",
        "---\nname: demo\ndescription: Demo\n",
        "---\nname: demo\ndescription: ''\n---\n",
    ],
)
def test_add_rejects_invalid_frontmatter(tmp_path: Path, frontmatter: str) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    write(source / "SKILL.md", frontmatter)

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert_error(result, "frontmatter", "name", "description", "skill.md")
    assert not (repo / "claude/skills").exists()
    assert not (home / ".claude/skills").exists()


@pytest.mark.parametrize(
    "name", ["../escape", "nested/name", "CON", "nul.txt", ".hidden"]
)
@pytest.mark.parametrize("action", ["add", "remove"])
def test_rejects_unsafe_names(tmp_path: Path, name: str, action: str) -> None:
    repo, home = make_repo(tmp_path)
    argument = str(source_skill(tmp_path, name)) if action == "add" else name

    result = run_ai_config(repo, home, "--force", "skill", action, argument)

    assert_error(result, "name", "invalid", "unsafe")
    assert not (repo / "claude/skills").exists()
    assert not (home / ".claude/skills").exists()


@pytest.mark.parametrize(
    "args,input_text",
    [
        (("skill", "remove", "demo"), "y\n"),
        (("--force", "skill", "remove", "demo"), ""),
        (("skill", "remove", "demo", "-f"), ""),
    ],
)
def test_remove_deletes_all_exact_copies_and_preserves_others(
    tmp_path: Path,
    args: tuple,
    input_text: str,
) -> None:
    repo, home = make_repo(tmp_path)
    all_roots = roots(repo, home)
    for root in all_roots:
        write(root / "demo/SKILL.md", "remove\n")
        write(root / "demo-extra/SKILL.md", "keep\n")
    plugin = home / ".claude/plugins/marketplaces/example/skills/demo/SKILL.md"
    write(plugin, "plugin owned\n")

    result = run_ai_config(repo, home, *args, input_text=input_text)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "demo" in result.stdout
    assert "[y/n]" in result.stdout.lower()
    for root in all_roots:
        assert str(root / "demo").replace("\\", "/") in result.stdout.replace("\\", "/")
        assert not (root / "demo").exists()
        assert (root / "demo-extra/SKILL.md").read_text() == "keep\n"
    assert plugin.read_text() == "plugin owned\n"


@pytest.mark.parametrize("answer", ["", "n\n"])
def test_remove_eof_and_no_leave_everything_unchanged(
    tmp_path: Path, answer: str
) -> None:
    repo, home = make_repo(tmp_path)
    for root in roots(repo, home):
        write(root / "demo/SKILL.md", "keep\n")

    result = run_ai_config(repo, home, "skill", "remove", "demo", input_text=answer)

    assert_error(result, "cancel", "confirm", "aborted")
    assert "[y/n]" in result.stdout.lower()
    for root in roots(repo, home):
        assert (root / "demo/SKILL.md").read_text() == "keep\n"


def test_remove_missing_is_successful_noop(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)

    result = run_ai_config(repo, home, "skill", "remove", "missing", input_text="")

    assert result.returncode == 0, result.stderr + result.stdout
    assert any(
        word in result.stdout.lower() for word in ("not found", "no ", "nothing")
    )
    assert "[y/n]" not in result.stdout.lower()


@pytest.mark.parametrize("location", ["repo", "live"])
def test_remove_refuses_regenerating_agent_before_mutations(
    tmp_path: Path, location: str
) -> None:
    repo, home = make_repo(tmp_path)
    for root in roots(repo, home):
        write(root / "demo/SKILL.md", "keep\n")
    agent = repo / "claude/agents" if location == "repo" else home / ".claude/agents"
    write(agent / "demo.md", "agent definition\n")

    result = run_ai_config(repo, home, "--force", "skill", "remove", "demo")

    assert_error(result, "agent", "regenerat")
    assert (agent / "demo.md").read_text() == "agent definition\n"
    for root in roots(repo, home):
        assert (root / "demo/SKILL.md").read_text() == "keep\n"


def test_remove_rejects_file_target_before_deleting_valid_targets(
    tmp_path: Path,
) -> None:
    repo, home = make_repo(tmp_path)
    write(repo / "claude/skills/demo/SKILL.md", "keep\n")
    write(home / ".gemini/antigravity/skills/demo", "file\n")

    result = run_ai_config(repo, home, "--force", "skill", "remove", "demo")

    assert_error(result, "directory", "file", "unsafe")
    assert (repo / "claude/skills/demo/SKILL.md").read_text() == "keep\n"
    assert (home / ".gemini/antigravity/skills/demo").read_text() == "file\n"


@pytest.mark.skipif(os.name == "nt", reason="Unix symlink safety contract")
@pytest.mark.parametrize("linked", ["target", "root", "ancestor"])
def test_remove_rejects_untrusted_links_before_any_deletion(
    tmp_path: Path, linked: str
) -> None:
    repo, home = make_repo(tmp_path)
    write(repo / "claude/skills/demo/SKILL.md", "keep\n")
    outside = tmp_path / "outside"
    link = {
        "target": home / ".agents/skills/demo",
        "root": home / ".agents/skills",
        "ancestor": home / ".agents",
    }[linked]
    suffix = {
        "target": "SKILL.md",
        "root": "demo/SKILL.md",
        "ancestor": "skills/demo/SKILL.md",
    }[linked]
    write(outside / suffix, "outside\n")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)

    result = run_ai_config(repo, home, "--force", "skill", "remove", "demo")

    assert_error(result, "link", "unsafe", "redirect", "reparse")
    assert (repo / "claude/skills/demo/SKILL.md").read_text() == "keep\n"
    assert (outside / suffix).read_text() == "outside\n"
    assert link.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="Unix legacy root links")
def test_remove_accepts_known_legacy_links_and_deduplicates(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    canonical = (home / ".agents/skills", home / ".gemini/config/skills")
    for root in canonical:
        write(root / "demo/SKILL.md", "remove\n")
        write(root / "other/SKILL.md", "keep\n")
    for legacy, target in (
        (home / ".codex/skills", canonical[0]),
        (home / ".gemini/antigravity-cli/skills", canonical[1]),
        (home / ".gemini/antigravity/skills", canonical[1]),
    ):
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.symlink_to(target, target_is_directory=True)

    result = run_ai_config(repo, home, "--force", "skill", "remove", "demo")

    assert result.returncode == 0, result.stderr + result.stdout
    for root in canonical:
        assert not (root / "demo").exists()
        assert (root / "other/SKILL.md").read_text() == "keep\n"


@pytest.mark.parametrize("args", [("skill",), ("skill", "guide")])
def test_guide_works_without_setup(tmp_path: Path, args: tuple) -> None:
    repo, home = make_repo(tmp_path)
    (repo / "claude").rmdir()

    result = run_ai_config(repo, home, *args)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "skill add" in result.stdout
    assert "skill remove" in result.stdout
    assert "acg install <skill>" not in result.stdout
    other = run_ai_config(repo, home, "skill", "guide")
    assert other.returncode == 0
    assert result.stdout == other.stdout


def test_help_documents_skill_management(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)

    result = run_ai_config(repo, home, "help")

    assert result.returncode == 0
    assert "skill add" in result.stdout
    assert "skill remove" in result.stdout


def test_add_respects_configured_repository(tmp_path: Path, monkeypatch) -> None:
    repo, home = make_repo(tmp_path)
    configured = tmp_path / "configured-repository"
    (configured / "claude").mkdir(parents=True)
    monkeypatch.setenv("AI_CONFIG_REPO", str(configured))
    source = source_skill(tmp_path)

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert result.returncode == 0, result.stderr + result.stdout
    assert (configured / "claude/skills/demo/SKILL.md").read_bytes() == (
        source / "SKILL.md"
    ).read_bytes()
    assert (home / ".claude/skills/demo/SKILL.md").is_file()
    assert not (repo / "claude/skills").exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix symlink safety contract")
def test_add_rejects_linked_destination_before_copying(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / ".claude/skills"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert_error(result, "link", "unsafe", "reparse")
    assert not (repo / "claude/skills/demo").exists()
    assert not (outside / "demo").exists()
    assert link.is_symlink()


def test_removed_skill_does_not_return_after_apply(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    for args in (("skill", "add", str(source)), ("apply", "--category", "skills")):
        result = run_ai_config(repo, home, *args)
        assert result.returncode == 0, result.stderr + result.stdout
    for path in (
        home / ".agents/skills/demo/SKILL.md",
        home / ".gemini/config/skills/demo/SKILL.md",
    ):
        assert path.is_file()

    removed = run_ai_config(repo, home, "skill", "remove", "demo", "--force")
    assert removed.returncode == 0, removed.stderr + removed.stdout
    reapplied = run_ai_config(repo, home, "apply", "--category", "skills")

    assert reapplied.returncode == 0, reapplied.stderr + reapplied.stdout
    for root in roots(repo, home):
        assert not (root / "demo").exists()


@pytest.mark.parametrize("answer", ["n\n", "y\n"])
def test_remove_updates_exact_marker_entries_only_when_confirmed(
    tmp_path: Path, answer: str
) -> None:
    repo, home = make_repo(tmp_path)
    write(home / ".agents/skills/demo/SKILL.md", "managed\n")
    markers = []
    for root in (home / ".agents/skills", home / ".gemini/config/skills"):
        for name in (".ai-config-managed", ".ai-config-known-unmanaged"):
            marker = root / name
            write(marker, "demo\ndemo-extra\nother\n")
            markers.append(marker)

    result = run_ai_config(repo, home, "skill", "remove", "demo", input_text=answer)

    assert "[y/n]" in result.stdout.lower()
    accepted = answer == "y\n"
    assert result.returncode == (0 if accepted else 1), result.stderr + result.stdout
    for marker in markers:
        expected = "demo-extra\nother\n" if accepted else "demo\ndemo-extra\nother\n"
        assert marker.read_text() == expected
    assert (home / ".agents/skills/demo/SKILL.md").exists() is not accepted


@pytest.mark.skipif(os.name == "nt", reason="Unix symlink safety contract")
def test_add_rejects_nested_source_link(tmp_path: Path) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    outside = tmp_path / "outside.md"
    write(outside, "outside\n")
    linked = source / "references/linked.md"
    linked.parent.mkdir()
    linked.symlink_to(outside)

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert_error(result, "link", "unsafe", "reparse")
    assert not (repo / "claude/skills/demo").exists()
    assert not (home / ".claude/skills/demo").exists()
    assert outside.read_text() == "outside\n"


@pytest.mark.parametrize(
    "frontmatter",
    [
        'name: "demo"\ndescription: >-\n  Demo skill\n  with details.',
        'name: "demo" # comment\ndescription: | # comment\n  # heading',
    ],
)
def test_add_accepts_quoted_name_and_block_description(
    tmp_path: Path,
    frontmatter: str,
) -> None:
    repo, home = make_repo(tmp_path)
    source = source_skill(tmp_path)
    write(
        source / "SKILL.md",
        f"---\n{frontmatter}\n---\nBody\n",
    )

    result = run_ai_config(repo, home, "skill", "add", str(source))

    assert result.returncode == 0, result.stderr + result.stdout
    for dest in (repo / "claude/skills/demo", home / ".claude/skills/demo"):
        assert (dest / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()

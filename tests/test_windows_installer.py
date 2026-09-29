import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Native Windows contract",
)


def _powershell_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def test_powershell_installer_places_standalone_binary(tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    standalone = tmp_path / "ai-config-source.exe"
    standalone.write_bytes(b"standalone-binary")
    bin_dir = tmp_path / "bin"
    destination = bin_dir / "ai-config.exe"
    launcher = bin_dir / "ai-config"
    acg_launcher = bin_dir / "acg"
    acg_command = bin_dir / "acg.cmd"
    bin_dir.mkdir()
    destination.write_bytes(b"old-binary")

    env = os.environ.copy()
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(bin_dir)
    env["AI_CONFIG_SKIP_PATH_UPDATE"] = "1"
    env["AI_CONFIG_SKIP_COMPLETION"] = "1"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install.ps1"),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert destination.read_bytes() == b"standalone-binary"
    # 沒在執行的舊檔換掉後當場刪除,不留 .old
    assert not list(bin_dir.glob("ai-config.exe.old-*"))
    # 啟動器要帶上自己的名字,提示訊息才會叫人打 acg 而不是 ai-config
    assert launcher.read_bytes() == (
        b'#!/usr/bin/env bash\n'
        b'AI_CONFIG_ENTRYPOINT=ai-config exec "$(dirname -- "$0")/ai-config.exe" "$@"\n'
    )
    assert acg_launcher.read_bytes() == (
        b'#!/usr/bin/env bash\n'
        b'AI_CONFIG_ENTRYPOINT=acg exec "$(dirname -- "$0")/ai-config.exe" "$@"\n'
    )
    assert acg_command.read_bytes() == (
        b'@echo off\r\nsetlocal\r\nset "AI_CONFIG_ENTRYPOINT=acg"\r\n'
        b'"%~dp0ai-config.exe" %*\r\n'
    )
    assert "Python" not in result.stdout


def test_completion_profile_is_idempotent_and_unicode_safe(tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    standalone = tmp_path / "ai-config-source.exe"
    standalone.write_bytes(b"standalone-binary")
    profile = tmp_path / "使用者 設定" / "profile.ps1"
    profile.parent.mkdir()
    profile.write_text("# 使用者設定\n", encoding="utf-8-sig")
    completion = tmp_path / "補全 scripts" / "completion.ps1"
    completion.parent.mkdir()
    completion.write_text(
        "$global:AiConfigCompletionLoaded = 'loaded'\n",
        encoding="ascii",
    )

    env = os.environ.copy()
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(tmp_path / "bin")
    env["AI_CONFIG_SKIP_PATH_UPDATE"] = "1"
    env["AI_CONFIG_SKIP_COMPLETION"] = "1"
    installer = _powershell_literal(REPO_ROOT / "install.ps1")
    profile_literal = _powershell_literal(profile)
    completion_literal = _powershell_literal(completion)
    command = (
        f". {installer}; "
        f"Update-CompletionProfile {profile_literal} {completion_literal}; "
        f"Update-CompletionProfile {profile_literal} {completion_literal}; "
        f". {profile_literal}; "
        "if ($global:AiConfigCompletionLoaded -ne 'loaded') { exit 23 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    profile_bytes = profile.read_bytes()
    assert profile_bytes.startswith(b"\xef\xbb\xbf")
    profile_text = profile_bytes.decode("utf-8-sig")
    assert "# 使用者設定" in profile_text
    assert profile_text.count("# >>> ai-config completion >>>") == 1
    assert str(completion) in profile_text


def test_powershell_installer_builds_the_version_layout(tmp_path: Path) -> None:
    """Two updates ran on Windows without ever creating a version directory.

    install.sh grew the layout and install.ps1 did not, so the exe on PATH
    was overwritten in place -- on the one platform where replacing a
    running file is exactly what cannot be done.
    """
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    standalone = tmp_path / "ai-config-source.exe"
    standalone.write_bytes(b"standalone-binary")
    bin_dir = tmp_path / "bin"
    share_dir = tmp_path / "share"
    bin_dir.mkdir()

    env = os.environ.copy()
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(bin_dir)
    env["AI_CONFIG_SHARE_DIR"] = str(share_dir)
    env["AI_CONFIG_SKIP_PATH_UPDATE"] = "1"
    env["AI_CONFIG_SKIP_COMPLETION"] = "1"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install.ps1"),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    # The stub cannot report a version, so it lands under the definite name
    staged = share_dir / "versions" / "unversioned" / "ai-config.exe"
    assert staged.read_bytes() == b"standalone-binary"
    assert (bin_dir / "ai-config.exe").read_bytes() == b"standalone-binary"
    assert (share_dir / "versions" / "active").read_text(encoding="utf-8").strip() == (
        "unversioned"
    )


def test_powershell_installer_replaces_a_running_executable(tmp_path: Path) -> None:
    """`acg update` runs the installer while its own exe is still executing.

    Windows will not overwrite a running exe, which is why update used to
    hand off to a background PowerShell and exit, leaving the user with no
    progress and no result. Renaming a running exe is allowed, so the
    installer moves it aside and writes the new one in its place.
    """
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    standalone = tmp_path / "ai-config-source.exe"
    standalone.write_bytes(b"standalone-binary")
    bin_dir = tmp_path / "bin"
    share_dir = tmp_path / "share"
    bin_dir.mkdir()
    (share_dir / "versions").mkdir(parents=True)
    # 已經是版本目錄的佈局,跳過收編舊檔(那一步會對 ping 等 30 秒)
    (share_dir / "versions" / "active").write_text("1.0.0", encoding="utf-8")
    destination = bin_dir / "ai-config.exe"
    ping = Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE"
    shutil.copyfile(ping, destination)
    running = subprocess.Popen(
        [str(destination), "-n", "60", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        env = os.environ.copy()
        env["AI_CONFIG_BINARY_PATH"] = str(standalone)
        env["AI_CONFIG_BIN_DIR"] = str(bin_dir)
        env["AI_CONFIG_SHARE_DIR"] = str(share_dir)
        env["AI_CONFIG_SKIP_PATH_UPDATE"] = "1"
        env["AI_CONFIG_SKIP_COMPLETION"] = "1"
        result = subprocess.run(
            [
                powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(REPO_ROOT / "install.ps1"),
            ],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env=env, check=False,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert destination.read_bytes() == b"standalone-binary"
        assert running.poll() is None, "換檔不能中斷正在執行的那一份"
        # 還在執行的舊檔刪不掉,留著等下次啟動清
        assert len(list(bin_dir.glob("ai-config.exe.old-*"))) == 1
        assert not (bin_dir / "ai-config.exe.new").exists()
    finally:
        running.kill()
        running.wait()


def _run_installer(powershell: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_ROOT / "install.ps1"),
        ],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=env, check=False,
    )


def test_powershell_installer_puts_the_launcher_on_path(tmp_path: Path) -> None:
    """A onedir build goes into its version directory; PATH gets the launcher.

    The copy on PATH used to be the real onefile exe, replaced in place, and
    a running onefile build reads its own modules from that path: 1.0.98's
    update died there after printing "Update complete".
    """
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    def build(body: bytes) -> Path:
        root = tmp_path / f"build-{body.decode()}" / "ai-config"
        (root / "_internal").mkdir(parents=True)
        (root / "ai-config.exe").write_bytes(body)
        (root / "_internal" / "python312.dll").write_bytes(b"runtime")
        return root

    launcher = tmp_path / "launcher.exe"
    launcher.write_bytes(b"launcher-binary")
    bin_dir = tmp_path / "bin"
    share_dir = tmp_path / "share"
    env = os.environ.copy()
    # 第一次從 zip 裝:正式下載走的就是這條
    archive = shutil.make_archive(
        str(tmp_path / "ai-config-windows-x86_64"), "zip",
        root_dir=build(b"first").parent, base_dir="ai-config",
    )
    env.update({
        "AI_CONFIG_BINARY_PATH": archive,
        "AI_CONFIG_LAUNCHER_PATH": str(launcher),
        "AI_CONFIG_BIN_DIR": str(bin_dir),
        "AI_CONFIG_SHARE_DIR": str(share_dir),
        "AI_CONFIG_VERSION": "v1.0.99",
        "AI_CONFIG_SKIP_PATH_UPDATE": "1",
        "AI_CONFIG_SKIP_COMPLETION": "1",
        "AI_CONFIG_READY_ATTEMPTS": "1",
    })

    result = _run_installer(powershell, env)

    assert result.returncode == 0, result.stderr + result.stdout
    version_root = share_dir / "versions" / "1.0.99" / "app"
    assert (version_root / "ai-config.exe").read_bytes() == b"first"
    assert (version_root / "_internal" / "python312.dll").is_file()
    # 舊版切換版本時找這個位置;onedir 不能放在這裡讓它單獨被複製走
    assert not (share_dir / "versions" / "1.0.99" / "ai-config.exe").exists()
    assert (bin_dir / "ai-config.exe").read_bytes() == b"launcher-binary"
    assert (share_dir / "versions" / "active").read_text(encoding="utf-8").strip() == "1.0.99"
    assert (share_dir / "launcher.sha256").is_file()

    # 再裝一次同一版:版本目錄整個換新,啟動器沒變就不動它
    env["AI_CONFIG_BINARY_PATH"] = str(build(b"second"))
    result = _run_installer(powershell, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (version_root / "ai-config.exe").read_bytes() == b"second"
    assert not list(bin_dir.glob("ai-config.exe.old-*"))
    assert not [p for p in (share_dir / "versions").iterdir() if p.name.startswith(".")]

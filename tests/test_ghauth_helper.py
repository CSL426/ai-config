"""The credential helper git calls, and its self-test."""

import io
import subprocess
from pathlib import Path

import pytest

from ai_config import (
    ghauth_access,
    ghauth_binding,
    ghauth_helper,
)


def test_credential_helper_answers_get_with_the_bound_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: f"tok-{account}")
    monkeypatch.setattr(
        ghauth_binding.sys, "stdin", io.StringIO("protocol=https\nhost=github.com\n\n")
    )
    assert ghauth_helper.credential_helper_main(["CSL426", "get"]) == 0
    assert capsys.readouterr().out == "username=CSL426\npassword=tok-CSL426\n"
    # store / erase 不做事,也不印
    assert ghauth_helper.credential_helper_main(["CSL426", "erase"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: "")
    monkeypatch.setattr(
        ghauth_binding.sys, "stdin", io.StringIO("protocol=https\nhost=github.com\n\n")
    )
    assert ghauth_helper.credential_helper_main(["CSL426", "get"]) == 1


def test_helper_answers_the_request_shape_git_sends_on_push(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # git 2.46+ 的 git-remote-https 會送重複的 capability[] 與 wwwauth[] 行;
    # `git credential fill` 不送,所以 fill 正常、push 卻拿不到憑證
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: "tok")
    monkeypatch.setattr(
        ghauth_binding.sys,
        "stdin",
        io.StringIO(
            "capability[]=authtype\ncapability[]=state\n"
            "protocol=https\nhost=github.com\n"
            'wwwauth[]=Basic realm="GitHub"\n\n'
        ),
    )
    assert ghauth_helper.credential_helper_main(["CSL426", "get"]) == 0
    assert capsys.readouterr().out == "username=CSL426\npassword=tok\n"

    # 一般鍵重複仍視為壞掉的請求
    monkeypatch.setattr(
        ghauth_binding.sys, "stdin", io.StringIO("protocol=https\nprotocol=https\nhost=github.com\n\n")
    )
    assert ghauth_helper.credential_helper_main(["CSL426", "get"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "credential_request",
    [
        "protocol=https\nhost=untrusted.example\n\n",
        "protocol=http\nhost=github.com\n\n",
        "protocol=https\nhost=github.com.evil.example\n\n",
        "protocol=https\nhost=github.com:8443\n\n",
        "protocol=https\nhost=github.com\nhost=untrusted.example\n\n",
        "protocol=https\n\n",
        "",
    ],
)
def test_helper_refuses_untrusted_requests_without_reading_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    credential_request: str,
) -> None:
    monkeypatch.setattr(ghauth_binding.sys, "stdin", io.StringIO(credential_request))

    def unexpected(account: str) -> str:
        pytest.fail("must not read a credential for an untrusted request")

    monkeypatch.setattr(ghauth_helper, "account_token", unexpected)
    assert ghauth_helper.credential_helper_main(["demo", "get"]) == 0
    assert capsys.readouterr().out == ""


def test_helper_self_test_goes_through_git_for_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        return subprocess.CompletedProcess(
            args, 0, stdout="username=x\npassword=y\n", stderr=""
        )

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)
    verdict = ghauth_helper.helper_self_test("x", tmp_path)
    assert seen == [["git", "-C", str(tmp_path), "credential", "fill"]]
    assert "正常" in verdict


def test_helper_self_test_distinguishes_a_working_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout="username=x\npassword=y\n", stderr=""
        )

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)
    assert "正常" in ghauth_helper.helper_self_test("x")

    def failing(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="acg credential helper: gh 沒有回傳 token\n"
        )

    monkeypatch.setattr(ghauth_access.subprocess, "run", failing)
    assert "gh 沒有回傳 token" in ghauth_helper.helper_self_test("x")


def test_helper_explains_a_missing_token_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import io

    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: "")
    monkeypatch.setattr(
        ghauth_binding.sys, "stdin", io.StringIO("protocol=https\nhost=github.com\n\n")
    )
    assert ghauth_helper.credential_helper_main(["CSL426", "get"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gh auth login" in captured.err and "CSL426" in captured.err

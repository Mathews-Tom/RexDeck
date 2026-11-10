"""Account-only launch deliberately verifies identity without Git discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from rexdeck.host import launch
from rexdeck.integrations import get


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        "[]",
        '{"account":null}',
        '{"account":""}',
        '{"account":"a b"}',
        '{"account":"reader","repo":"github.com/a/b"}',
        '{"account":"reader","host":"https://github.com"}',
        '{"account":"reader","host":"github.com/evil"}',
        '{"account":"reader","host":"github.example.com"}',
    ],
)
def test_account_selection_refuses_missing_or_ambiguous_context(
    raw: str | None,
) -> None:
    with pytest.raises(ValueError):
        launch.github_account_selection(raw)


def test_account_selection_defaults_host_without_requiring_worktree(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(launch.__file__)),
            "context",
            "github-account",
            str(tmp_path),
            '{"account":"reader"}',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    selected = json.loads(result.stdout)
    assert selected["cwd"] == str(tmp_path)
    assert json.loads(selected["selection"]) == {
        "account": "reader",
        "host": "github.com",
    }
    assert (
        launch.github_account_selection('{"account":"reader","host":"GITHUB.COM"}')[
            "host"
        ]
        == "github.com"
    )


def test_account_context_refuses_unowned_descriptor() -> None:
    with pytest.raises(ValueError, match="owned frontend"):
        replace(get("github"), context="github-account")


@pytest.mark.parametrize("login", ["reader", "different"])
def test_account_launch_verifies_before_marker_and_strips_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    login: str,
) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ['api', '--hostname', 'github.com', 'user', '--jq', '.login']\nprint({login!r})\n"
    )
    gh.chmod(0o755)
    marker = tmp_path / "marker.json"
    tool = tmp_path / "view"
    tool.write_text(
        f"#!{sys.executable}\nimport os,json,pathlib,sys\npathlib.Path({str(marker)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'env':{{k:os.environ.get(k) for k in ['GH_HOST','GH_PATH','GH_PROMPT_DISABLED','REXDECK_GITHUB_ACCOUNT','GH_REPO','GH_FORCE_TTY','CLICOLOR_FORCE']}}}}))\n"
    )
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.defpath)
    for key in ("GH_REPO", "GH_FORCE_TTY", "CLICOLOR_FORCE"):
        monkeypatch.setenv(key, "untrusted")
    focused: list[tuple[str, ...]] = []
    monkeypatch.setattr(launch, "restore_focus", lambda *args: focused.append(args))
    monkeypatch.setattr(launch, "acknowledge", lambda: None)
    result = launch.run(
        "github-account",
        "Inbox",
        "Inbox view",
        str(tool),
        "/rex",
        "unix:///lab.sock",
        "session",
        "origin",
        str(tmp_path),
        ("-I", "-m", "rexdeck.inbox", '{"account":"reader"}'),
    )
    assert focused == [("/rex", "unix:///lab.sock", "session", "origin")]
    if login != "reader":
        assert result == 1
        assert "account mismatch" in capsys.readouterr().err
        assert not marker.exists()
    else:
        assert result == 0
        seen = json.loads(marker.read_text())
        assert seen["argv"] == ["-I", "-m", "rexdeck.inbox"]
        assert seen["env"] == {
            "GH_HOST": "github.com",
            "GH_PATH": str(gh),
            "GH_PROMPT_DISABLED": "1",
            "REXDECK_GITHUB_ACCOUNT": "reader",
            "GH_REPO": None,
            "GH_FORCE_TTY": None,
            "CLICOLOR_FORCE": None,
        }
    assert os.environ["GH_REPO"] == "untrusted"


@pytest.mark.parametrize(
    "args",
    [
        (),
        ('{"account":"reader"}',),
        ("-m", "rexdeck.inbox", '{"account":"reader"}'),
        ("-I", "-m", "outside.module", '{"account":"reader"}'),
        ("-I", "-m", "rexdeck.inbox", "extra", '{"account":"reader"}'),
    ],
)
def test_account_launcher_refuses_non_owned_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    monkeypatch.setattr(launch, "restore_focus", lambda *unused: None)
    monkeypatch.setattr(launch, "acknowledge", lambda: None)
    assert (
        launch.run(
            "github-account",
            "Inbox",
            "Inbox",
            "/never-start",
            "/rex",
            "unix:///lab.sock",
            "session",
            "origin",
            str(tmp_path),
            args,
        )
        == 1
    )

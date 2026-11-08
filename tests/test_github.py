"""Explicit GitHub identity, failure visibility and upstream invocation boundaries."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from rexdeck.host import launch


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo 雪"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "remote", "add", "origin", "git@github.com:owner/project.git")
    return path


def choice(**values: str) -> str:
    return json.dumps({"account": "reader", **values})


def test_multiple_remotes_never_guess_fork_upstream_or_host(repo: Path) -> None:
    git(
        repo,
        "remote",
        "add",
        "upstream",
        "https://enterprise.example/team/upstream.git",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        launch.github_selection(str(repo), choice())
    # A named remote cannot pick the credential host; the operator must type it.
    with pytest.raises(ValueError, match="non-github.com host"):
        launch.github_selection(str(repo), choice(remote="upstream"))
    assert launch.github_selection(
        str(repo), choice(repo="enterprise.example/team/upstream")
    ) == {"repo": "enterprise.example/team/upstream", "account": "reader"}
    assert (
        launch.github_selection(str(repo), choice(repo="github.com/other/explicit"))[
            "repo"
        ]
        == "github.com/other/explicit"
    )
    with pytest.raises(ValueError, match="not both"):
        launch.github_selection(
            str(repo), choice(repo="github.com/a/b", remote="origin")
        )


def test_single_remote_requires_account_and_freezes_correct_identity(
    repo: Path,
) -> None:
    with pytest.raises(ValueError, match="account=LOGIN"):
        launch.github_selection(str(repo), None)
    assert launch.github_selection(str(repo), choice()) == {
        "repo": "github.com/owner/project",
        "account": "reader",
    }
    git(
        repo,
        "remote",
        "set-url",
        "origin",
        "ssh://git@enterprise.example/team/project.git",
    )
    for selected in (choice(), choice(remote="origin")):
        with pytest.raises(ValueError, match="non-github.com host"):
            launch.github_selection(str(repo), selected)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@github.com/a/b.git",
        "file:///tmp/project",
        "../project",
        "git@github-alias:a/b",
        "https://github.com/a/b/extra",
        "https://github.com/a/b?query",
        "https://github.com/a/.git",
        "https://github.com:8443/a/b",
    ],
)
def test_remote_refuses_credentials_aliases_and_ambiguous_paths(
    repo: Path, url: str
) -> None:
    git(repo, "remote", "set-url", "origin", url)
    with pytest.raises(ValueError):
        launch.github_selection(str(repo), choice())


def test_multiple_fetch_urls_and_unknown_remote_require_explicit_repo(
    repo: Path,
) -> None:
    git(
        repo,
        "remote",
        "set-url",
        "--add",
        "origin",
        "https://github.com/other/project.git",
    )
    with pytest.raises(ValueError, match="multiple fetch URLs"):
        launch.github_selection(str(repo), choice())
    with pytest.raises(ValueError, match="Unknown repository remote"):
        launch.github_selection(str(repo), choice(remote="absent"))


def test_context_refuses_repositoryless_even_with_explicit_repo(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(launch.__file__)),
            "context",
            "github-worktree",
            str(tmp_path),
            choice(repo="github.com/owner/project"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not a git repository" in result.stderr.lower()
    assert result.stdout == ""


def test_context_freezes_selection_independently_of_later_remote_changes(
    repo: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(launch.__file__)),
            "context",
            "github-worktree",
            str(repo),
            choice(remote="origin"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    selected = json.loads(json.loads(result.stdout)["selection"])
    git(repo, "remote", "set-url", "origin", "https://github.com/other/repo.git")
    assert (
        launch.github_selection(str(repo), json.dumps(selected))["repo"]
        == "github.com/owner/project"
    )


def test_config_layers_repo_settings_without_overwriting_them(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = repo / ".gh-dash.yml"
    original = "theme: {}\nkeybindings: {}\n"
    config.write_text(original)
    monkeypatch.delenv("GH_DASH_CONFIG", raising=False)
    selected = launch.github_selection(str(repo), choice())
    overlay = launch.github_config(str(repo), selected)
    assert overlay["include"] == [str(config)]
    assert overlay["prSections"] == [
        {"title": "github.com/owner/project as reader", "filters": "repo:owner/project"}
    ]
    assert overlay["issuesSections"] == overlay["prSections"]
    assert "theme" not in overlay and "keybindings" not in overlay
    assert config.read_text() == original
    monkeypatch.setenv("GH_DASH_CONFIG", "custom.yml")
    assert launch.github_config(str(repo), selected)["include"] == [
        str(repo / "custom.yml")
    ]


def test_config_follows_invoking_subdirectory_not_repository_root(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_DASH_CONFIG", raising=False)
    (repo / ".gh-dash.yml").write_text("keybindings: {}\n")
    sub = repo / "sub"
    sub.mkdir()
    selected = launch.github_selection(str(sub), choice())
    assert launch.github_config(str(sub), selected)["include"] == []
    config = sub / ".gh-dash.yaml"
    config.write_text("keybindings: {}\n")
    assert launch.github_config(str(sub), selected)["include"] == [str(config)]
    preferred = sub / ".gh-dash.yml"
    preferred.write_text("keybindings: {}\n")
    assert launch.github_config(str(sub), selected)["include"] == [str(preferred)]


@pytest.fixture
def helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invocation.json"
    gh = bin_dir / "gh"
    gh.write_text(f"""#!{sys.executable}
import os, sys
if os.environ.get("TEST_AUTH_FAILURE"):
    print("authentication required", file=sys.stderr); sys.exit(4)
if sys.argv[4] == "user":
    print(os.environ.get("TEST_LOGIN", "reader"))
else:
    if os.environ.get("TEST_REPO_FAILURE"):
        print("repository inaccessible (HTTP 404)", file=sys.stderr); sys.exit(1)
    print(os.environ.get("TEST_REPOSITORY", "owner/project"))
""")
    tool = bin_dir / "gh-dash"
    tool.write_text(f"""#!{sys.executable}
import json, os, pathlib, sys
config = pathlib.Path(sys.argv[2])
pathlib.Path({str(marker)!r}).write_text(json.dumps({{
    "argv": sys.argv[1:], "config": json.loads(config.read_text()),
    "host": os.environ["GH_HOST"], "repo": os.environ["GH_REPO"],
    "gh": os.environ["GH_PATH"], "cwd": os.getcwd(),
}}))
sys.exit(int(os.environ.get("TEST_TOOL_EXIT", "0")))
""")
    gh.chmod(0o755)
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("FF_MOCK_DATA", raising=False)
    monkeypatch.delenv("GH_DASH_CONFIG", raising=False)
    return tool, marker


@pytest.mark.parametrize("failure", ["auth", "account", "redirect", "api", "exit"])
def test_launch_errors_stay_visible_and_restore_exact_origin(
    repo: Path,
    helpers: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    tool, marker = helpers
    if failure == "auth":
        monkeypatch.setenv("TEST_AUTH_FAILURE", "1")
    elif failure == "api":
        monkeypatch.setenv("TEST_REPO_FAILURE", "1")
    elif failure == "account":
        monkeypatch.setenv("TEST_LOGIN", "different")
    elif failure == "redirect":
        monkeypatch.setenv("TEST_REPOSITORY", "renamed/project")
    else:
        monkeypatch.setenv("TEST_TOOL_EXIT", "9")
    restored: list[tuple[str, ...]] = []
    acknowledged: list[bool] = []
    monkeypatch.setattr(launch, "restore_focus", lambda *args: restored.append(args))
    monkeypatch.setattr(launch, "acknowledge", lambda: acknowledged.append(True))
    code = launch.run(
        "github-worktree",
        "GitHub",
        "gh-dash",
        str(tool),
        "/rex",
        "unix:///private.sock",
        "session",
        "exact-origin",
        str(repo),
        args=(choice(repo="github.com/owner/project"),),
    )
    assert code != 0
    assert restored == [("/rex", "unix:///private.sock", "session", "exact-origin")]
    assert acknowledged == [True]
    error = capsys.readouterr().err
    assert {
        "auth": "authentication required",
        "api": "repository inaccessible (HTTP 404)",
        "account": "account mismatch",
        "redirect": "redirected",
        "exit": "exited 9",
    }[failure] in error
    assert marker.exists() == (failure == "exit")


def test_run_passes_explicit_identity_without_changing_parent_or_persisting_config(
    repo: Path,
    helpers: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, marker = helpers
    monkeypatch.setenv("GH_HOST", "wrong.example")
    monkeypatch.setenv("GH_REPO", "wrong/other")
    monkeypatch.setattr(launch, "restore_focus", lambda *args: None)
    assert (
        launch.run(
            "github-worktree",
            "GitHub",
            "gh-dash",
            str(tool),
            "/rex",
            "unix:///private.sock",
            "session",
            "origin",
            str(repo),
            args=(choice(repo="github.com/owner/project"),),
        )
        == 0
    )
    record = json.loads(marker.read_text())
    assert (record["host"], record["repo"], record["cwd"]) == (
        "github.com",
        "github.com/owner/project",
        str(repo),
    )
    assert record["argv"][0] == "--config"
    assert not Path(record["argv"][1]).exists()
    assert os.environ["GH_HOST"] == "wrong.example"
    assert os.environ["GH_REPO"] == "wrong/other"


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGTERM])
def test_terminating_signal_cleans_up_overlay(
    repo: Path,
    helpers: tuple[Path, Path],
    signum: int,
) -> None:
    tool, _marker = helpers
    tool.write_text(f"""#!{sys.executable}
import signal, sys
print(sys.argv[2], flush=True)
while True:
    signal.pause()
""")
    driver = f"""
import sys
sys.path.insert(0, {str(Path(launch.__file__).parents[2])!r})
from rexdeck.host import launch
launch.restore_focus = lambda *args: None
launch.acknowledge = lambda: None
raise SystemExit(launch.run(
    "github-worktree", "GitHub", "gh-dash", {str(tool)!r}, "/rex",
    "unix:///private.sock", "session", "origin", {str(repo)!r},
    args=({choice(repo="github.com/owner/project")!r},)))
"""
    with subprocess.Popen(
        [sys.executable, "-c", driver],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=10), "tool did not report its overlay"
            config = Path(process.stdout.readline().strip())
            assert config.is_file()
            process.send_signal(signum)
            _stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 128 + signum
            assert f"interrupted by signal {signum}" in stderr
            assert not config.parent.exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

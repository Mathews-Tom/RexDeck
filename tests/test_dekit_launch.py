from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from rexdeck.host import launch


@pytest.mark.parametrize(
    "selected", [None, "missing.yaml", "other.yaml", "dekit.yml", "."]
)
def test_config_refusals_precede_any_tool_execution(
    tmp_path: Path, selected: str | None
) -> None:
    (tmp_path / "other.yaml").write_text("tasks: {}\n")
    (tmp_path / "dekit.yml").write_text("tasks: {}\n")
    with pytest.raises((ValueError, OSError)):
        launch.resolve("dekit-config", str(tmp_path), selected)


def test_config_selection_is_explicit_and_canonical(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    project.mkdir()
    config = project / "dekit.yaml"
    config.write_text("tasks: {}\n")
    for selection in ("project with spaces/dekit.yaml", str(config), config.as_uri()):
        assert launch.resolve("dekit-config", str(tmp_path), selection) == str(
            config.resolve()
        )
    with pytest.raises(ValueError, match="selected file"):
        launch.resolve("directory", str(tmp_path), str(config))


@pytest.mark.parametrize("control", ["\x1b", "\n", "\x7f", "\x9b", "\u202e", "\u00a0"])
def test_config_path_cannot_inject_the_displayed_stop_command(
    tmp_path: Path, control: str
) -> None:
    project = tmp_path / ("hostile" + control)
    project.mkdir()
    config = project / "dekit.yaml"
    config.write_text("tasks: {}\n")
    with pytest.raises(ValueError, match="terminal control characters"):
        launch.resolve("dekit-config", str(tmp_path), str(config))


@pytest.mark.parametrize("control", ["\x1b", "\n", "\x7f", "\x9b", "\u202e", "\u00a0"])
def test_executable_path_cannot_inject_the_displayed_stop_command(
    tmp_path: Path, control: str
) -> None:
    with pytest.raises(ValueError, match="terminal control characters"):
        launch.dekit_run(f"/hostile{control}/dekit", str(tmp_path / "dekit.yaml"), ())


def test_assignment_like_tool_path_is_refused_before_launch(tmp_path: Path) -> None:
    tool = "/qualified=other/dekit"
    with pytest.raises(ValueError, match="must not contain '='"):
        launch.dekit_run(tool, str(tmp_path / "dekit.yaml"), ())
    with pytest.raises(ValueError, match="must not contain '='"):
        launch.dekit_stop_command(tool, str(tmp_path))


@pytest.mark.parametrize(
    "state", ["running", "absent", "failed", "timeout", "malformed", "recursive"]
)
def test_exit_report_is_scoped_visible_and_escaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    root = str(tmp_path / "space ; ' project")
    calls: list[list[str]] = []

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:4] == ["/qualified/dekit", "-C", root, "--json"]
        assert options["timeout"] == 10
        assert options["cwd"] == root
        if state == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        if argv[4:] == ["runner", "status"]:
            payload: object = {"status": state, "socket": "/run/dekit/evil\x1b.sock"}
        else:
            assert state == "running" and argv[4:] == ["ls"]
            payload = {"tasks": [{"path": "evil\x1b]0;title\x07", "state": "running"}]}
        output = "[]" if state == "malformed" else json.dumps(payload)
        if state == "recursive":
            output = "[" * 2000 + "]" * 2000
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    launch.dekit_report("/qualified/dekit", root)
    output = capsys.readouterr().out
    assert shlex.join(["/qualified/dekit", "-C", root, "down"]) in output
    assert "\x1b" not in output and "\x07" not in output
    if state not in ("malformed", "recursive", "timeout"):
        assert "Runner socket:" in output and "/run/dekit/evil\\u001b.sock" in output
    if state == "running":
        assert "Runner still running" in output
        assert '"state": "running"' in output and "\\u001b" in output
        assert len(calls) == 2
    elif state == "absent":
        assert "Runner stopped; no tasks remain" in output
        assert len(calls) == 1  # Never `ls` an absent runner: no daemon is started.
    else:
        assert "Cannot determine remaining tasks" in output
        assert "Runner stopped; no tasks remain" not in output


@pytest.mark.parametrize(
    "failure", ["selection", "active", "version", "starting", "args", "none", "absent"]
)
def test_runner_qualification_precedes_launch_and_report_survives_tui_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    tool = tmp_path / "dekit"
    tool.touch()
    config = tmp_path / "dekit.yaml"
    config.write_text("tasks: {}\n")
    qualified = str(tool.resolve())
    root = str(tmp_path)
    events: list[str] = []
    status: dict[str, object] = {
        "status": "running",
        "selected_binary": qualified,
        "binary": qualified,
        "version": "0.10.3",
    }
    if failure == "selection":
        status["selected_binary"] = "/unqualified/dekit"
    elif failure == "active":
        status["binary"] = "/unqualified/dekit"
    elif failure == "version":
        status["version"] = "0.10.2"
    elif failure == "starting":
        status["status"] = "starting"
    elif failure == "absent":
        status = {"status": "absent", "selected_binary": qualified}

    def query(tool_arg: str, root_arg: str, *args: str) -> dict[str, object]:
        assert (tool_arg, root_arg, args) == (str(tool), root, ("runner", "status"))
        return status

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv == [str(tool), "--version"]:
            assert failure != "absent", "absent runners must not probe --version"
            assert options["cwd"] == root
            return subprocess.CompletedProcess(argv, 0, "dekit 0.10.3\n", "")
        assert argv == [str(tool), "-C", root]
        assert options["cwd"] == root
        events.append("tui")
        raise OSError("terminal disappeared")

    def report(tool_arg: str, root_arg: str) -> None:
        assert (tool_arg, root_arg) == (str(tool), root)
        events.append("report")

    monkeypatch.setattr(launch, "dekit_query", query)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(launch, "dekit_report", report)
    with pytest.raises((ValueError, OSError)):
        launch.dekit_run(str(tool), str(config), ("up",) if failure == "args" else ())
    assert events == (["tui", "report"] if failure in ("none", "absent") else [])


@pytest.mark.parametrize("outcome", [0, 1, "error"])
def test_one_acknowledgement_follows_report_and_error_before_exact_origin_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: int | str,
) -> None:
    config = tmp_path / "dekit.yaml"
    config.write_text("tasks: {}\n")
    events: list[str] = []

    def run_tool(tool: str, target: str, args: tuple[str, ...]) -> int:
        assert target == str(config.resolve()) and args == ()
        events.append("report")
        if outcome == "error":
            raise OSError("terminal disappeared")
        assert isinstance(outcome, int)
        return outcome

    def restore(rex: str, server: str, session: str, origin: str) -> None:
        assert (rex, server, session, origin) == (
            "/rex",
            "unix:///lab.sock",
            "session-a",
            "origin-a",
        )
        events.append("restore")

    def acknowledge() -> None:
        output = capsys.readouterr()
        if outcome == 1:
            assert "dekit exited 1" in output.err
        elif outcome == "error":
            assert "cannot launch dekit: terminal disappeared" in output.err
        events.append("acknowledge")

    monkeypatch.setattr(launch, "dekit_run", run_tool)
    monkeypatch.setattr(launch, "restore_focus", restore)
    monkeypatch.setattr(launch, "acknowledge", acknowledge)
    assert launch.run(
        "dekit-config",
        "Processes",
        "dekit",
        "/dekit",
        "/rex",
        "unix:///lab.sock",
        "session-a",
        "origin-a",
        str(config),
    ) == (0 if outcome == 0 else 1)
    assert events == ["report", "acknowledge", "restore"]


def test_dekit_subprocesses_do_not_inherit_pane_or_runner_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "dekit"
    tool.touch()
    config = tmp_path / "dekit.yaml"
    config.write_text("tasks: {}\n")
    inherited = {
        "REX_SESSION": "old-session",
        "REX_BLOCK": "old-block",
        "REX_CLIENT": "old-client",
        "DEKIT_RUNNER_ROOT": "/other/project",
        "DEKIT_RUNNER_KIND": "host",
        "DEKIT_FUTURE_OPTION": "unsafe",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "temporary"))
    calls: list[list[str]] = []

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        environment = options["env"]
        assert isinstance(environment, dict)
        assert not any(name in environment for name in inherited)
        assert not any(name.startswith("DEKIT_") for name in environment)
        assert environment["XDG_CONFIG_HOME"] == str(tmp_path / "config")
        assert environment["XDG_RUNTIME_DIR"] == str(tmp_path / "runtime")
        assert environment["TMPDIR"] == str(tmp_path / "temporary")
        assert options["cwd"] == str(tmp_path)
        if argv == [str(tool), "--version"]:
            return subprocess.CompletedProcess(argv, 0, "dekit 0.10.3\n", "")
        if argv == [str(tool), "-C", str(tmp_path)]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[:4] == [str(tool), "-C", str(tmp_path), "--json"]
        payload: object = (
            {
                "status": "running",
                "selected_binary": str(tool.resolve()),
                "binary": str(tool.resolve()),
                "version": "0.10.3",
                "socket": str(tmp_path / "runtime/dekit/project.sock"),
            }
            if argv[4:] == ["runner", "status"]
            else {"tasks": []}
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)
    assert launch.dekit_run(str(tool), str(config), ()) == 0
    assert calls == [
        [str(tool), "-C", str(tmp_path), "--json", "runner", "status"],
        [str(tool), "--version"],
        [str(tool), "-C", str(tmp_path)],
        [str(tool), "-C", str(tmp_path), "--json", "runner", "status"],
        [str(tool), "-C", str(tmp_path), "--json", "ls"],
    ]


@pytest.mark.parametrize(
    "routing", [(), ("XDG_RUNTIME_DIR",), ("TMPDIR",), ("XDG_RUNTIME_DIR", "TMPDIR")]
)
@pytest.mark.parametrize("pasting_has_extra_routing", [False, True])
def test_stop_command_preserves_only_set_runtime_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routing: tuple[str, ...],
    pasting_has_extra_routing: bool,
) -> None:
    values = {
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime ; ' space"),
        "TMPDIR": str(tmp_path / "tmp $ space"),
    }
    for name, value in values.items():
        if name in routing:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    tool = tmp_path / "dekit"
    tool.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "print(json.dumps({key: os.environ.get(key) "
        "for key in ('XDG_RUNTIME_DIR', 'TMPDIR')}))\n"
    )
    tool.chmod(0o755)
    command = launch.dekit_stop_command(str(tool), str(tmp_path))
    assert shlex.split(command) == [
        "env",
        *(part for name in values if name not in routing for part in ("-u", name)),
        *(f"{name}={values[name]}" for name in routing),
        str(tool),
        "-C",
        str(tmp_path),
        "down",
    ]
    pasting_environment = dict(os.environ)
    for name in values:
        if pasting_has_extra_routing:
            pasting_environment[name] = "/other-terminal/runtime"
        else:
            pasting_environment.pop(name, None)
    result = subprocess.run(
        shlex.split(command),
        env=pasting_environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        name: value if name in routing else None for name, value in values.items()
    }


@pytest.mark.parametrize("variable", ["XDG_RUNTIME_DIR", "TMPDIR"])
@pytest.mark.parametrize("control", ["\x9b", "\u202e"])
def test_runtime_routing_cannot_inject_the_displayed_command(
    monkeypatch: pytest.MonkeyPatch, variable: str, control: str
) -> None:
    monkeypatch.setenv(variable, "/unsafe" + control)
    with pytest.raises(ValueError, match="terminal control characters"):
        launch.dekit_environment()

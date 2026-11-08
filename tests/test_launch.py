from __future__ import annotations

import errno
import json
import os
import pty
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rexdeck.host import launch

LAUNCHER = Path(launch.__file__)


@pytest.mark.parametrize("representation", ["path", "uri", "localhost", "hostname"])
def test_cwd_decodes_local_spaces_unicode_and_literal_uri_characters(
    tmp_path: Path, representation: str
) -> None:
    cwd = tmp_path / "雪 space % # ? ; $(touch nope)"
    cwd.mkdir()
    value = str(cwd)
    if representation != "path":
        value = cwd.as_uri()
        if representation == "localhost":
            value = value.replace("file://", "file://localhost", 1)
        elif representation == "hostname":
            value = value.replace("file://", f"file://{socket.gethostname()}", 1)
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "context", "directory", value],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cwd": str(cwd.resolve()),
        "target": str(cwd.resolve()),
    }
    assert not (tmp_path / "nope").exists()


@pytest.mark.parametrize("authority", ["", "localhost", "LOCALHOST", "LOCAL-HOST"])
def test_local_file_uri_does_not_require_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str
) -> None:
    def unavailable_dns() -> str:
        raise OSError("DNS unavailable")

    monkeypatch.setattr(socket, "gethostname", lambda: "local-host")
    monkeypatch.setattr(socket, "getfqdn", unavailable_dns)
    uri = tmp_path.as_uri().replace("file://", f"file://{authority}", 1)
    assert launch.directory(uri) == str(tmp_path.resolve())


def test_file_uri_accepts_local_fqdn_without_accepting_remote_or_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "local-host")
    monkeypatch.setattr(socket, "getfqdn", lambda: "local-host.example")
    uri = tmp_path.as_uri().replace("file://", "file://LOCAL-HOST.EXAMPLE", 1)
    assert launch.directory(uri) == str(tmp_path.resolve())
    for authority in (
        "remote.example",
        "user@local-host.example",
        "local-host.example:123",
    ):
        with pytest.raises(ValueError, match="Remote or credentialed"):
            launch.directory(
                tmp_path.as_uri().replace("file://", f"file://{authority}", 1)
            )


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "~/project",
        "https://localhost/tmp",
        "ssh://localhost/tmp",
        "file://remote.invalid/tmp",
        "file://user@localhost/tmp",
        "file://localhost:123/tmp",
        "file:///tmp?",
        "file:///tmp#",
        "file:///tmp/%",
        "file:///tmp/%FF",
        "file:///tmp/%00",
        "file:///tmp\n",
        "file:////remote/share",
        "//remote/share",
    ],
)
def test_cwd_refuses_untrusted_or_ambiguous_input(value: str) -> None:
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "context", "directory", value],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr
    assert "Traceback" not in result.stderr


def test_cwd_refuses_missing_file_nul_and_invalid_unicode(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("not a directory", encoding="utf-8")
    for value in (str(target), str(tmp_path / "missing"), "\x00", "/\udcff"):
        with pytest.raises((OSError, ValueError)):
            launch.directory(value)


def test_cwd_reports_real_symlink_loop_without_traceback(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "context", "directory", str(loop)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "invalid invoking context" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.fixture
def tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Executable local consumers with persistent pane state, never a Rex server."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "blocks": ["origin-a", "origin-b", "replacement"],
                "focused": "files",
                "server": "unix:///private server.sock",
                "session": "session-exact",
            }
        ),
        encoding="utf-8",
    )
    rex = tmp_path / "rex tool"
    rex.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"path = Path({str(state)!r})\n"
        "state = json.loads(path.read_text())\n"
        "args = sys.argv[1:]\n"
        "if args[:6] != ['-S', state['server'], '--autostart=false', "
        "'--timeout=10s', 'api', 'call']:\n"
        "    sys.exit('Wrong server or unsupported command')\n"
        "if args[6] != 'session.focus_block':\n"
        "    sys.exit('Only exact identity focus is supported')\n"
        "target = json.loads(args[7])\n"
        "if target['session_id'] != state['session']:\n"
        "    sys.exit('Wrong session')\n"
        "if any(key in os.environ for key in ('REX_SESSION', 'REX_BLOCK', 'REX_CLIENT')):\n"
        "    sys.exit('Inherited target leaked')\n"
        "if state.get('failure'):\n"
        "    sys.exit('Private transport or focus denied')\n"
        "if target['block_id'] not in state['blocks']:\n"
        "    sys.exit('Original pane no longer exists')\n"
        "if state.get('interrupt_focus'):\n"
        "    import signal\n"
        "    os.kill(os.getppid(), signal.SIGINT)\n"
        "    sys.exit(0)\n"
        "state['focused'] = target['block_id']\n"
        "path.write_text(json.dumps(state))\n"
        "print('{}')\n",
        encoding="utf-8",
    )
    yazi = tmp_path / "yazi tool"
    yazi.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"path = Path({str(state)!r})\n"
        "state = json.loads(path.read_text())\n"
        "state['tty'] = [os.isatty(fd) for fd in (0, 1, 2)]\n"
        "state['argv'] = sys.argv[1:]\n"
        "state['cwd'] = os.getcwd()\n"
        "state['config'] = os.environ.get('YAZI_CONFIG_HOME')\n"
        "state['path'] = os.environ.get('PATH')\n"
        "if state.get('probe_helper'):\n"
        "    import subprocess\n"
        "    state['helper'] = subprocess.check_output(['preload-helper'], text=True)\n"
        "state['focused'] = 'origin-b'\n"
        "if state.get('remove_origin'):\n"
        "    state['blocks'].remove(state['remove_origin'])\n"
        "path.write_text(json.dumps(state))\n"
        "if state.get('interrupt_yazi'):\n"
        "    import signal\n"
        "    os.kill(os.getppid(), signal.SIGINT)\n"
        "    sys.exit(0)\n"
        "if state.get('signal'):\n"
        "    os.kill(os.getpid(), state['signal'])\n"
        "sys.exit(state.get('exit', 0))\n",
        encoding="utf-8",
    )
    for executable in (rex, yazi):
        executable.chmod(0o700)
    return yazi, rex, state


def start(
    tools: tuple[Path, Path, Path],
    cwd: Path,
    origin: str = "origin-a",
    args: tuple[str, ...] = (),
) -> tuple[subprocess.Popen[bytes], int]:
    yazi, rex, _ = tools
    master, slave = pty.openpty()
    environment = dict(os.environ)
    environment.update(
        YAZI_CONFIG_HOME=str(cwd / "user config"),
        REX_SESSION="unrelated-session",
        REX_BLOCK="unrelated-block",
        REX_CLIENT="unrelated-client",
    )
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                str(LAUNCHER),
                "run",
                "directory",
                "Files",
                "Yazi",
                str(yazi),
                str(rex),
                "unix:///private server.sock",
                "session-exact",
                origin,
                str(cwd),
                *args,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
        )
    finally:
        os.close(slave)
    return child, master


def terminal_output(master: int, marker: bytes | None = None) -> bytes:
    result = b""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], deadline - time.monotonic())
        if not ready:
            break
        try:
            chunk = os.read(master, 4096)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        result += chunk
        if marker is not None and marker in result:
            return result
    if marker is not None:
        raise AssertionError(f"Missing terminal marker {marker!r}: {result!r}")
    return result


def cleanup(child: subprocess.Popen[bytes], master: int) -> None:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)
    os.close(master)


@pytest.mark.parametrize("origin", ["origin-a", "origin-b"])
def test_real_launcher_inherits_tty_and_config_then_restores_frozen_origin(
    tmp_path: Path, tools: tuple[Path, Path, Path], origin: str
) -> None:
    cwd = tmp_path / "雪 space ; $()"
    cwd.mkdir()
    child, master = start(tools, cwd, origin)
    try:
        output = terminal_output(master)
        assert child.wait(timeout=5) == 0, output
        state = json.loads(tools[2].read_text())
        assert state["tty"] == [True, True, True]
        assert state["cwd"] == str(cwd.resolve())
        assert state["config"] == str(cwd / "user config")
        assert state["focused"] == origin
    finally:
        cleanup(child, master)


def test_descriptor_args_follow_fixed_fields_verbatim_without_a_shell(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    """Integration argv reaches the tool after the nine launch fields, unexpanded."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    args = ("--client", "omp", "two words", "$(touch pwned)", "-")
    child, master = start(tools, cwd, args=args)
    try:
        output = terminal_output(master)
        assert child.wait(timeout=5) == 0, output
        state = json.loads(tools[2].read_text())
        assert state["argv"] == list(args)
        assert state["focused"] == "origin-a"
        assert not (cwd / "pwned").exists()
    finally:
        cleanup(child, master)


@pytest.mark.parametrize(
    "failure", ["origin_closed", "transport", "missing_rex", "denied_rex"]
)
def test_focus_errors_stay_visible_without_adopting_another_pane(
    tmp_path: Path, tools: tuple[Path, Path, Path], failure: str
) -> None:
    state = json.loads(tools[2].read_text())
    if failure == "origin_closed":
        state["remove_origin"] = "origin-a"
    elif failure == "transport":
        state["failure"] = True
    elif failure == "missing_rex":
        tools[1].unlink()
    else:
        tools[1].chmod(0o600)
    tools[2].write_text(json.dumps(state), encoding="utf-8")
    child, master = start(tools, tmp_path)
    try:
        output = terminal_output(master, b"Press Enter")
        assert b"cannot return focus" in output
        assert child.poll() is None, "Error disappeared before TTY acknowledgement"
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
        os.write(master, b"\n")
        assert child.wait(timeout=5) == 1
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
        assert b"Traceback" not in output
    finally:
        cleanup(child, master)


@pytest.mark.parametrize("exit_code", [7, -15])
def test_yazi_failure_preserves_exit_and_acknowledges_before_returning_focus(
    tmp_path: Path, tools: tuple[Path, Path, Path], exit_code: int
) -> None:
    state = json.loads(tools[2].read_text())
    if exit_code < 0:
        state["signal"] = -exit_code
    else:
        state["exit"] = exit_code
    expected_exit = exit_code if exit_code > 0 else 128 - exit_code
    tools[2].write_text(json.dumps(state), encoding="utf-8")
    child, master = start(tools, tmp_path)
    try:
        output = terminal_output(master, b"Press Enter")
        assert f"Yazi exited {expected_exit}".encode() in output
        assert child.poll() is None
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
        os.write(master, b"\n")
        assert child.wait(timeout=5) == expected_exit
        assert json.loads(tools[2].read_text())["focused"] == "origin-a"
    finally:
        cleanup(child, master)


def test_missing_yazi_reports_failure_before_restoring_origin(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    tools[0].unlink()
    child, master = start(tools, tmp_path)
    try:
        output = terminal_output(master, b"Press Enter")
        assert b"cannot launch Yazi" in output
        assert child.poll() is None
        os.write(master, b"\n")
        assert child.wait(timeout=5) == 1
        assert json.loads(tools[2].read_text())["focused"] == "origin-a"
    finally:
        cleanup(child, master)


def test_real_symlink_loop_acknowledges_before_restoring_origin(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    child, master = start(tools, loop)
    try:
        output = terminal_output(master, b"Press Enter")
        assert b"cannot launch Yazi" in output
        assert b"Traceback" not in output
        assert child.poll() is None
        state = json.loads(tools[2].read_text())
        assert "cwd" not in state, "Yazi must not start with an invalid directory"
        assert state["focused"] == "files"
        os.write(master, b"\n")
        assert child.wait(timeout=5) == 1
        assert json.loads(tools[2].read_text())["focused"] == "origin-a"
    finally:
        cleanup(child, master)


@pytest.mark.parametrize("command", ["cwd", "run"])
def test_python312_symlink_loop_reaches_existing_error_boundary(
    tmp_path: Path,
    tools: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    loop = tmp_path / "loop"
    original_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        if path == loop:
            raise RuntimeError(f"Symlink loop from {str(path)!r}")
        return original_resolve(path, strict=strict)

    acknowledgements: list[bool] = []
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(launch, "acknowledge", lambda: acknowledgements.append(True))
    args = [str(LAUNCHER), "run" if command == "run" else "context", "directory"]
    if command == "run":
        args += [
            "Files",
            "Yazi",
            str(tools[0]),
            str(tools[1]),
            "unix:///private server.sock",
            "session-exact",
            "origin-a",
        ]
    monkeypatch.setattr(sys, "argv", [*args, str(loop)])
    assert launch.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Symlink loop" in captured.err
    assert "Traceback" not in captured.err
    state = json.loads(tools[2].read_text())
    assert "cwd" not in state
    if command == "run":
        assert "cannot launch Yazi" in captured.err
        assert acknowledgements == [True]
        assert state["focused"] == "origin-a"
    else:
        assert "invalid invoking context" in captured.err
        assert acknowledgements == []
        assert state["focused"] == "files"


def test_focus_interrupt_reports_unknown_outcome_and_waits_for_acknowledgement(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    state = json.loads(tools[2].read_text())
    state["interrupt_focus"] = True
    tools[2].write_text(json.dumps(state), encoding="utf-8")
    child, master = start(tools, tmp_path)
    try:
        output = terminal_output(master, b"Press Enter")
        assert b"focus outcome is unknown" in output
        assert b"origin-a" in output
        assert b"No replacement pane was selected" in output
        assert b"Yazi interrupted" not in output
        assert b"Traceback" not in output
        assert child.poll() is None, "Focus interrupt vanished before acknowledgement"
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
        os.write(master, b"\n")
        assert child.wait(timeout=5) == 1
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
    finally:
        cleanup(child, master)


def test_yazi_interrupt_still_acknowledges_then_restores_origin(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    state = json.loads(tools[2].read_text())
    state["interrupt_yazi"] = True
    tools[2].write_text(json.dumps(state), encoding="utf-8")
    child, master = start(tools, tmp_path)
    try:
        output = terminal_output(master, b"Press Enter")
        assert b"Yazi interrupted" in output
        assert b"focus outcome is unknown" not in output
        assert child.poll() is None
        assert json.loads(tools[2].read_text())["focused"] == "origin-b"
        os.write(master, b"\n")
        assert child.wait(timeout=5) == 130
        assert json.loads(tools[2].read_text())["focused"] == "origin-a"
    finally:
        cleanup(child, master)


def test_native_action_refuses_invalid_context_before_opening_panes() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "luajit",
            str(root / "tests/lua/native_files.lua"),
            str(LAUNCHER.parent),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "selection",
    ["none", "absolute", "relative", "uri", "uppercase", "directory", "symlink"],
)
def test_document_context_resolves_target_and_workdir(
    tmp_path: Path, selection: str
) -> None:
    invoking = tmp_path / "invoking"
    invoking.mkdir()
    folder = invoking / "nested.md"
    folder.mkdir()
    document = folder / ("雪 notes.MD" if selection == "uppercase" else "雪 notes.md")
    document.write_text("# Markdown\n", encoding="utf-8")
    target = invoking if selection == "none" else document
    selected = str(document)
    if selection == "relative":
        selected = str(document.relative_to(invoking))
    elif selection == "uri":
        selected = document.as_uri()
    elif selection == "directory":
        target = folder
        selected = str(folder)
    elif selection == "symlink":
        link = invoking / "linked.md"
        link.symlink_to(document)
        selected = str(link)
    argv = [sys.executable, str(LAUNCHER), "context", "document", str(invoking)]
    if selection != "none":
        argv.append(selected)
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cwd": str((target if target.is_dir() else target.parent).resolve()),
        "target": str(target.resolve()),
    }


@pytest.mark.parametrize("selected", ["", "\x00", "notes.txt", "missing.md"])
def test_document_refuses_invalid_selection(tmp_path: Path, selected: str) -> None:
    (tmp_path / "notes.txt").write_text("not Markdown", encoding="utf-8")
    with pytest.raises((OSError, ValueError)):
        launch.resolve("document", str(tmp_path), selected)


def test_reported_cwd_naming_a_markdown_file_is_not_a_document(
    tmp_path: Path,
) -> None:
    """A terminal-reported cwd must be a directory; only a selection names a file."""
    document = tmp_path / "x.md"
    document.write_text("# not a cwd\n", encoding="utf-8")
    for raw in (str(document), document.as_uri()):
        with pytest.raises(ValueError, match="not a directory"):
            launch.resolve("document", raw)
    assert launch.resolve("document", str(tmp_path), str(document)) == str(
        document.resolve()
    )


@pytest.mark.parametrize("context", ["directory", "git-worktree"])
def test_nondocument_context_refuses_selection(tmp_path: Path, context: str) -> None:
    with pytest.raises(ValueError, match="does not accept a selected file"):
        launch.resolve(context, str(tmp_path), "notes.md")


def test_document_run_passes_arguments_and_target_in_parent_directory(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    tool, rex, state_path = tools
    target = tmp_path / "雪 notes.md"
    target.write_text("# Markdown\n", encoding="utf-8")
    assert (
        launch.run(
            "document",
            "Markdown",
            "markdown-reader",
            str(tool),
            str(rex),
            "unix:///private server.sock",
            "session-exact",
            "origin-a",
            str(target),
            ("--theme", "dark"),
        )
        == 0
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["argv"] == ["--theme", "dark", str(target.resolve())]
    assert state["cwd"] == str(target.parent.resolve())
    assert state["focused"] == "origin-a"


@pytest.mark.parametrize(
    ("suffix", "header"),
    [
        (".PDF", b"%PDF-1.4\n"),
        (".jpg", b"\xff\xd8\xff"),
        (".jpeg", b"\xff\xd8\xff"),
        (".png", b"\x89PNG\r\n\x1a\n"),
        (".svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
    ],
)
@pytest.mark.parametrize("representation", ["absolute", "relative", "uri"])
def test_media_explicit_local_selection(
    tmp_path: Path, suffix: str, header: bytes, representation: str
) -> None:
    target = tmp_path / ("雪 ; $(touch nope)" + suffix)
    target.write_bytes(header)
    selected = (
        target.name
        if representation == "relative"
        else target.as_uri()
        if representation == "uri"
        else str(target)
    )
    assert launch.resolve("media", str(tmp_path), selected) == str(target.resolve())
    assert not (tmp_path / "nope").exists()


@pytest.mark.parametrize("suffix", [".pdf", ".jpg", ".png", ".svg"])
def test_media_mislabeled_files_fail_before_launch(tmp_path: Path, suffix: str) -> None:
    target = tmp_path / ("corrupt" + suffix)
    target.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid .* file signature"):
        launch.resolve("media", str(tmp_path), str(target))


def test_media_requires_file_and_checks_resolved_suffix(tmp_path: Path) -> None:
    unsupported = tmp_path / "untrusted.html"
    unsupported.write_text("<svg/>", encoding="utf-8")
    link = tmp_path / "misleading.svg"
    link.symlink_to(unsupported)
    for selected in (None, "", str(tmp_path), str(link)):
        with pytest.raises(ValueError):
            launch.resolve("media", str(tmp_path), selected)


def test_media_launch_passes_one_absolute_target_and_restores_origin(
    tmp_path: Path, tools: tuple[Path, Path, Path]
) -> None:
    tool, rex, state_path = tools
    target = tmp_path / "雪 ; $(touch nope).png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert (
        launch.run(
            "media",
            "Viewer",
            "Yazi",
            str(tool),
            str(rex),
            "unix:///private server.sock",
            "session-exact",
            "origin-a",
            str(target),
        )
        == 0
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["argv"] == [str(target.resolve())]
    assert state["cwd"] == str(target.parent.resolve())
    assert state["focused"] == "origin-a"


@pytest.mark.parametrize("plugin", ["files", "git", "markdown", "usage", "viewer"])
def test_tool_path_excludes_untrusted_entries_without_changing_launch_contract(
    tmp_path: Path,
    tools: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    plugin: str,
) -> None:
    from rexdeck.integrations import get

    integration = get(plugin)
    tool, rex, state_path = tools
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    relative = tmp_path / "relative"
    relative.mkdir()
    for folder, marker in (
        (tmp_path, "cwd"),
        (relative, "relative"),
        (trusted, "trusted"),
    ):
        helper = folder / "preload-helper"
        helper.write_text(f"#!/bin/sh\nprintf {marker}\n", encoding="utf-8")
        helper.chmod(0o700)
    state = json.loads(state_path.read_text())
    state["probe_helper"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    target = tmp_path
    if plugin == "markdown":
        target = tmp_path / "selected.md"
        target.write_text("# Markdown\n", encoding="utf-8")
    elif plugin == "viewer":
        target = tmp_path / "selected.png"
        target.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000d4944415408d763606068f80f000204018088089b4a"
                "0000000049454e44ae426082"
            )
        )
    untrusted_path = f":.:relative:{trusted}:/usr/bin:relative:"
    monkeypatch.setenv("PATH", untrusted_path)
    monkeypatch.setenv("YAZI_CONFIG_HOME", str(tmp_path / "user config"))
    assert (
        launch.run(
            integration.context,
            integration.title,
            integration.tool_label,
            str(tool),
            str(rex),
            "unix:///private server.sock",
            "session-exact",
            "origin-a",
            str(target),
            integration.args,
        )
        == 0
    )
    state = json.loads(state_path.read_text())
    assert state["path"] == f"{trusted}:/usr/bin"
    assert state["helper"] == "trusted"
    assert state["argv"] == list(integration.args) + (
        [str(target.resolve())] if plugin in ("markdown", "viewer") else []
    )
    assert state["cwd"] == str(tmp_path.resolve())
    assert state["config"] == str(tmp_path / "user config")
    assert state["focused"] == "origin-a"
    assert os.environ["PATH"] == untrusted_path


@pytest.mark.parametrize("path", [":.:relative", ""])
def test_tool_path_defaults_to_system_when_no_absolute_entries(
    tmp_path: Path,
    tools: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    tool, rex, state_path = tools
    monkeypatch.setenv("PATH", path)
    assert (
        launch.run(
            "directory",
            "Files",
            "Yazi",
            str(tool),
            str(rex),
            "unix:///private server.sock",
            "session-exact",
            "origin-a",
            str(tmp_path),
        )
        == 0
    )
    state = json.loads(state_path.read_text())
    assert state["path"] == os.defpath
    assert state["focused"] == "origin-a"
    assert os.environ["PATH"] == path

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

from rexdeck.plugins.files import launch

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
        [sys.executable, str(LAUNCHER), "cwd", value],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cwd": str(cwd.resolve())}
    assert not (tmp_path / "nope").exists()


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
        [sys.executable, str(LAUNCHER), "cwd", value],
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
        "if 'REX_SESSION' in os.environ or 'REX_BLOCK' in os.environ:\n"
        "    sys.exit('Inherited target leaked')\n"
        "if state.get('failure'):\n"
        "    sys.exit('Private transport or focus denied')\n"
        "if target['block_id'] not in state['blocks']:\n"
        "    sys.exit('Original pane no longer exists')\n"
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
        "state['cwd'] = os.getcwd()\n"
        "state['config'] = os.environ.get('YAZI_CONFIG_HOME')\n"
        "state['focused'] = 'origin-b'\n"
        "if state.get('remove_origin'):\n"
        "    state['blocks'].remove(state['remove_origin'])\n"
        "path.write_text(json.dumps(state))\n"
        "if state.get('signal'):\n"
        "    os.kill(os.getpid(), state['signal'])\n"
        "sys.exit(state.get('exit', 0))\n",
        encoding="utf-8",
    )
    for executable in (rex, yazi):
        executable.chmod(0o700)
    return yazi, rex, state


def start(
    tools: tuple[Path, Path, Path], cwd: Path, origin: str = "origin-a"
) -> tuple[subprocess.Popen[bytes], int]:
    yazi, rex, _ = tools
    master, slave = pty.openpty()
    environment = dict(os.environ)
    environment.update(
        YAZI_CONFIG_HOME=str(cwd / "user config"),
        REX_SESSION="unrelated-session",
        REX_BLOCK="unrelated-block",
    )
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                str(LAUNCHER),
                "run",
                str(yazi),
                str(rex),
                "unix:///private server.sock",
                "session-exact",
                origin,
                str(cwd),
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

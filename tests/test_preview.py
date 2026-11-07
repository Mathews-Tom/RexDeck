from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
from pathlib import Path

import preview
import pytest


@pytest.mark.parametrize("failure", ["missing", "denied"])
def test_focus_transport_failure_waits_for_tty_acknowledgement(
    tmp_path: Path, failure: str
) -> None:
    target = tmp_path / "preview.txt"
    target.write_text("preview\n", encoding="utf-8")
    rex = tmp_path / "rex"
    if failure == "denied":
        rex.write_text("not executable", encoding="utf-8")
        rex.chmod(0o600)
    master, slave = pty.openpty()
    child = subprocess.Popen(
        [
            sys.executable,
            preview.__file__,
            "view",
            str(rex),
            "server",
            "session",
            "origin",
            "/usr/bin/true",
            str(tmp_path),
            str(target),
        ],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(slave)
    try:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready, "Preview did not offer recovery on its TTY"
        os.read(child.stdout.fileno(), 4096)
        assert child.poll() is None, "Preview exited before acknowledgement"
        os.write(master, b"\n")
        _, errors = child.communicate(timeout=5)
        assert child.returncode == 0
        assert str(rex).encode() in errors
        assert b"Traceback" not in errors
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)


def test_successful_view_returns_without_acknowledgement(tmp_path: Path) -> None:
    target = tmp_path / "preview.txt"
    target.write_text("preview\n", encoding="utf-8")
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            [
                sys.executable,
                preview.__file__,
                "view",
                "/usr/bin/true",
                "server",
                "session",
                "origin",
                "/usr/bin/true",
                str(tmp_path),
                str(target),
            ],
            stdin=slave,
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0
    finally:
        os.close(slave)
        os.close(master)

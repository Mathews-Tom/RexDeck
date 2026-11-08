"""Start unmodified Yazi in a native Rex terminal and restore its exact origin."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def directory(raw: str) -> str:
    """Decode only an explicit absolute local directory, never a fallback."""
    if not raw or "\x00" in raw:
        raise ValueError(
            "An explicit local directory without NUL characters is required"
        )
    raw.encode("utf-8", errors="strict")
    if raw.lower().startswith("file:"):
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError("File URI contains unescaped control characters")
        uri = urlsplit(raw)
        authority = uri.netloc.casefold()
        if (
            authority not in ("", "localhost")
            and authority != socket.gethostname().casefold()
            and authority != socket.getfqdn().casefold()
        ):
            raise ValueError("Remote or credentialed file URI is not supported")
        if "?" in raw or "#" in raw:
            raise ValueError("File URI must not contain a query or fragment")
        if re.search(r"%(?![0-9a-fA-F]{2})", uri.path):
            raise ValueError("File URI contains invalid percent encoding")
        raw = unquote(uri.path, encoding="utf-8", errors="strict")
    if "\x00" in raw or not raw.startswith("/") or raw.startswith("//"):
        raise ValueError("An absolute local directory is required")
    try:
        path = Path(raw).resolve(strict=True)
    except RuntimeError as error:
        # Python 3.12 reports symlink loops as RuntimeError rather than OSError.
        raise ValueError(str(error)) from error
    if not path.is_dir():
        raise ValueError("The invoking terminal's cwd is not a directory")
    return str(path)


def acknowledge() -> None:
    if sys.stdin.isatty():
        try:
            input("Press Enter to close RexDeck Files: ")
        except (EOFError, KeyboardInterrupt):
            pass


def restore_focus(rex: str, server: str, session: str, origin: str) -> None:
    """Use API identities, not CLI label/suffix/directional target resolution."""
    environment = dict(os.environ)
    environment.pop("REX_SESSION", None)
    environment.pop("REX_BLOCK", None)
    environment.pop("REX_CLIENT", None)
    result = subprocess.run(
        [
            rex,
            "-S",
            server,
            "--autostart=false",
            "--timeout=10s",
            "api",
            "call",
            "session.focus_block",
            json.dumps({"session_id": session, "block_id": origin}),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Rex exited {result.returncode}: {result.stderr.strip()}")


def run(yazi: str, rex: str, server: str, session: str, origin: str, cwd: str) -> int:
    exit_code = 1
    try:
        if not all((server, session, origin)):
            raise ValueError(
                "Explicit Rex server, session and origin identities are required"
            )
        if not Path(yazi).is_absolute() or not Path(rex).is_absolute():
            raise ValueError("Absolute Yazi and Rex executable paths are required")
        checked_cwd = directory(cwd)
        # Inherit all three pane streams. Yazi requires a TTY on each one.
        # No configuration environment or key bindings are overridden.
        exit_code = subprocess.run([yazi], cwd=checked_cwd, check=False).returncode
        if exit_code < 0:
            exit_code = 128 - exit_code
        if exit_code:
            print(f"RexDeck Files: Yazi exited {exit_code}", file=sys.stderr)
            acknowledge()
    except KeyboardInterrupt:
        exit_code = 130
        print("RexDeck Files: Yazi interrupted", file=sys.stderr)
        acknowledge()
    except (OSError, ValueError) as error:
        print(f"RexDeck Files: cannot launch Yazi: {error}", file=sys.stderr)
        acknowledge()
    try:
        if not all((server, session, origin)) or not Path(rex).is_absolute():
            raise ValueError(
                "Cannot restore focus without explicit Rex origin and executable"
            )
        restore_focus(rex, server, session, origin)
    except KeyboardInterrupt:
        print(
            f"RexDeck Files: return focus to original pane {origin!r} interrupted; "
            "focus outcome is unknown. No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(
            f"RexDeck Files: cannot return focus to original pane {origin!r}: {error}. "
            "No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    return exit_code


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "cwd":
        try:
            print(json.dumps({"cwd": directory(args[1])}))
            return 0
        except (OSError, ValueError) as error:
            print(f"RexDeck Files: invalid invoking cwd: {error}", file=sys.stderr)
            return 1
    if len(args) == 7 and args[0] == "run":
        return run(*args[1:])
    print("Use cwd RAW_PWD or run YAZI REX SERVER SESSION ORIGIN CWD", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

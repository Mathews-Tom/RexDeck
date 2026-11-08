"""Start one unmodified terminal tool in a native Rex pane and restore its origin."""

from __future__ import annotations

import json
import os
import re
import shutil
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


def git_directory(raw: str) -> str:
    """Keep cwd-based Git discovery; reject inherited repository redirection."""
    overrides = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
    selected = sorted(
        key
        for key in os.environ
        if key in overrides or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    )
    if selected:
        raise ValueError("Ambiguous inherited Git override: " + ", ".join(selected))
    cwd = directory(raw)
    search_path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
        if Path(entry).is_absolute()
    )
    git = shutil.which("git", path=search_path) if search_path else None
    if git is None:
        raise ValueError(
            "Required executable not found in absolute PATH directories: git"
        )
    result = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Git exited {result.returncode}")
    if result.stdout.strip() != "true":
        raise ValueError("Invoking directory is not a non-bare Git worktree")
    return cwd


# Mirrors rexdeck.integrations.CONTEXTS; this file runs standalone under ``python -I``.
CONTEXTS = {"directory": directory, "git-worktree": git_directory}


def acknowledge() -> None:
    if sys.stdin.isatty():
        try:
            input("Press Enter to close RexDeck: ")
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


def resolve(context: str, raw: str) -> str:
    """Resolve one declared context; arbitrary cwd never selects a file or account."""
    if context not in CONTEXTS:
        raise ValueError(f"Unknown integration context: {context!r}")
    return CONTEXTS[context](raw)


def run(
    context: str,
    title: str,
    label: str,
    tool: str,
    rex: str,
    server: str,
    session: str,
    origin: str,
    cwd: str,
    args: tuple[str, ...] = (),
) -> int:
    """Launch one upstream tool at its validated context, then restore origin focus."""
    exit_code = 1
    try:
        if not all((server, session, origin)):
            raise ValueError(
                "Explicit Rex server, session and origin identities are required"
            )
        if not Path(tool).is_absolute() or not Path(rex).is_absolute():
            raise ValueError("Absolute tool and Rex executable paths are required")
        checked_cwd = resolve(context, cwd)
        # Inherit the actual pane TTY and the tool's normal configuration.
        exit_code = subprocess.run(
            [tool, *args], cwd=checked_cwd, check=False
        ).returncode
        if exit_code < 0:
            exit_code = 128 - exit_code
        if exit_code:
            print(f"RexDeck {title}: {label} exited {exit_code}", file=sys.stderr)
            acknowledge()
    except KeyboardInterrupt:
        exit_code = 130
        print(f"RexDeck {title}: {label} interrupted", file=sys.stderr)
        acknowledge()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"RexDeck {title}: cannot launch {label}: {error}", file=sys.stderr)
        acknowledge()
    try:
        if not all((server, session, origin)) or not Path(rex).is_absolute():
            raise ValueError(
                "Cannot restore focus without explicit Rex origin and executable"
            )
        restore_focus(rex, server, session, origin)
    except KeyboardInterrupt:
        print(
            f"RexDeck {title}: return focus to original pane {origin!r} interrupted; "
            "focus outcome is unknown. No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(
            f"RexDeck {title}: cannot return focus to original pane {origin!r}: {error}. "
            "No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    return exit_code


def main() -> int:
    """Dispatch standalone context validation and native pane launch commands."""
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "context":
        try:
            print(json.dumps({"cwd": resolve(args[1], args[2])}))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"RexDeck: invalid invoking cwd: {error}", file=sys.stderr)
            return 1
    if len(args) >= 10 and args[0] == "run":
        context, title, label, tool, rex, server, session, origin, cwd = args[1:10]
        return run(
            context,
            title,
            label,
            tool,
            rex,
            server,
            session,
            origin,
            cwd,
            args=tuple(args[10:]),
        )
    print(
        "Use context CONTEXT RAW_PWD or run CONTEXT TITLE LABEL TOOL REX SERVER "
        "SESSION ORIGIN CWD [ARG...]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

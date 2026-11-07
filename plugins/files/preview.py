"""Independent text pager and process-generation identity for owned previews."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from filesystem import read_text_file


def process_identity(pid: int) -> str:
    if pid <= 0:
        raise ValueError("A positive child PID is required")
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "comm="],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    identity = result.stdout.strip()
    if result.returncode or not identity:
        raise RuntimeError("Preview child process is no longer identifiable")
    return identity


def view(arguments: list[str]) -> int:
    rex, server, session, origin, bat, root, filename = arguments
    exit_code = 1
    try:
        target, content = read_text_file(root, filename)
        env = dict(os.environ)
        env["BAT_PAGER"] = "less --RAW-CONTROL-CHARS"
        env["LESSHISTFILE"] = "-"
        exit_code = subprocess.run(
            [
                bat,
                "--no-config",
                "--paging=always",
                "--color=always",
                "--style=numbers,header",
                "--file-name",
                target["path"],
                "--",
                "-",
            ],
            env=env,
            input=content,
            check=False,
        ).returncode
        if exit_code:
            print(f"Text viewer exited {exit_code}", file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Cannot preview selected file: {error}", file=sys.stderr)
    finally:
        if exit_code and sys.stdin.isatty():
            input("Press Enter to return to the project terminal: ")
        focus_error: str | None = None
        try:
            focused = subprocess.run(
                [
                    rex,
                    "-S",
                    server,
                    "--autostart=false",
                    "focus",
                    "-s",
                    session,
                    origin,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if focused.returncode:
                focus_error = (
                    f"Rex exited {focused.returncode}: {focused.stderr.strip()}"
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            focus_error = f"{type(error).__name__}: {error}"
        if focus_error is not None:
            print(
                f"Cannot return focus to the project terminal: {focus_error}. "
                "Unrelated panes left intact",
                file=sys.stderr,
            )
            if sys.stdin.isatty():
                input("Press Enter to close this preview: ")
    return exit_code


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "identity":
        try:
            print(json.dumps({"ok": True, "identity": process_identity(int(args[1]))}))
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(json.dumps({"ok": False, "error": str(error)[:256]}))
            return 1
    if len(args) == 8 and args[0] == "view":
        return view(args[1:])
    print(
        "Use identity PID or view REX SERVER SESSION ORIGIN BAT ROOT FILE",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

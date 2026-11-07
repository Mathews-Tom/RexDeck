"""Explicit repository-local setup and lifecycle; no launch-time downloads."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rexdeck.lifecycle import (
    Setup,
    apply_setup,
    disable,
    inspect_setup,
    start,
    status,
    stop,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "inspect",
        "apply",
        "start",
        "status",
        "stop",
        "disable",
    ):
        command = subcommands.add_parser(name)
        command.add_argument("--workspace", type=Path, required=True)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--server", required=True)
        command.add_argument(
            "--real-server",
            action="store_true",
            help="Explicitly opt in to the user's real Rex server; requires owner approval in campaign automation",
        )
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    try:
        workspace = args.workspace.absolute()
        setup = Setup(
            project,
            workspace,
            args.config.absolute(),
            args.server,
            real_server=args.real_server,
        )
        actions = {
            "inspect": inspect_setup,
            "apply": apply_setup,
            "start": start,
            "status": status,
            "stop": stop,
            "disable": disable,
        }
        result = actions[args.command](setup)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        state = result.get("state")
        if state in {"conflict", "failed", "fault"}:
            return 1
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            json.dumps({"state": "failed", "error": str(error)}, ensure_ascii=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

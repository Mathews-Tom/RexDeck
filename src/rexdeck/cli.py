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
from rexdeck.runtime import prepare_runtime, verify_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "prepare-runtime",
        "verify-runtime",
        "inspect",
        "apply",
        "start",
        "status",
        "stop",
        "disable",
    ):
        command = subcommands.add_parser(name)
        command.add_argument("--workspace", type=Path, required=True)
        if name not in ("prepare-runtime", "verify-runtime"):
            command.add_argument("--config", type=Path, required=True)
            command.add_argument("--server", required=True)
            command.add_argument(
                "--real-server",
                action="store_true",
                help="Explicitly opt in to the user's real Rex server; requires owner approval in campaign automation",
            )
        if name == "apply":
            command.add_argument("--reseed", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    try:
        workspace = args.workspace.absolute()
        if args.command in ("prepare-runtime", "verify-runtime"):
            operation = (
                prepare_runtime if args.command == "prepare-runtime" else verify_runtime
            )
            runtime = operation(project, workspace)
            result: dict[str, object] = {
                "state": "runtime-verified",
                "hub": str(runtime.hub),
                "view": str(runtime.view),
                "sdk": str(runtime.sdk),
            }
        else:
            setup = Setup(
                project,
                workspace,
                args.config.absolute(),
                args.server,
                real_server=args.real_server,
            )
            if args.command == "apply":
                result = apply_setup(setup, reseed=args.reseed)
            else:
                actions = {
                    "inspect": inspect_setup,
                    "start": start,
                    "status": status,
                    "stop": stop,
                    "disable": disable,
                }
                result = actions[args.command](setup)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        state = result.get("state")
        if state in {"conflict", "failed", "starting"}:
            return 1
        if state == "host-ready":
            feature = result.get("feature")
            if not isinstance(feature, dict) or feature.get("state") != "disabled":
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

"""One-time native Rex target approval and the Files enable/disable lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Never

from rexdeck import lifecycle, settings

_FAILURES = {"conflict", "failed", "fault"}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        print(json.dumps({"state": "failed", "error": message}), file=sys.stderr)
        raise SystemExit(2)


def _discover(config: Path) -> str:
    # REX_SERVER is honored only here, never by subsequent saved-target commands.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"REX_SESSION", "REX_BLOCK", "REX_CLIENT"}
    }
    env["REX_CONFIG"] = str(config)
    argv = [lifecycle._executable("rex"), "--autostart=false", "--timeout", "3s"]
    if "REX_SERVER" in env:
        # Native `server status` needs -S even when REX_SERVER is present.
        lifecycle._socket_path(env["REX_SERVER"])
        argv += ["-S", env["REX_SERVER"]]
    result = lifecycle._run([*argv, "server", "status", "--json"], env)
    if result.code != 0:
        raise RuntimeError(
            "Cannot discover the Rex server; start Rex or pass setup --server UNIX_URL: "
            + result.output.decode(errors="replace").strip()
        )
    value = lifecycle._decode(result.output, "Rex server discovery")
    server = value.get("endpoint")
    if (
        not isinstance(server, str)
        or not server
        or server != server.strip()
        or not isinstance(value.get("instance_id"), str)
        or not value["instance_id"]
    ):
        raise RuntimeError("Rex server discovery lacks endpoint/instance identity")
    lifecycle._socket_path(server)
    return server


def _selected_status(setup: lifecycle.Setup) -> None:
    current = lifecycle._server_status(setup, selected=True)
    if current is None:
        raise RuntimeError("Selected Rex server is not running")
    endpoint = current.get("endpoint")
    if not isinstance(endpoint, str) or (
        lifecycle._socket_path(endpoint) != lifecycle._socket_path(setup.server)
    ):
        raise RuntimeError("Selected server answered with a different socket endpoint")


def _inspect(target: settings.SavedTarget) -> dict[str, object]:
    setup = target.setup()
    _selected_status(setup)
    result = lifecycle.inspect_setup(setup)
    if result.get("state") in _FAILURES:
        return result
    prerequisites = result.get("prerequisites")
    if isinstance(prerequisites, dict) and prerequisites.get("error"):
        raise RuntimeError(str(prerequisites["error"]))
    return result


def _configure(args: argparse.Namespace, path: Path) -> dict[str, object]:
    old = settings.load(path)
    # Validate both XDG roots even when a saved workspace is reused.
    workspace_default = settings.default_workspace()
    settings.xdg_path("XDG_RUNTIME_DIR", Path("/tmp"))
    if (
        old is not None
        and args.config is None
        and args.server is None
        and args.workspace is None
    ):
        if args.real_server and not old.real_server:
            raise RuntimeError("--real-server does not match the saved isolated target")
        result = _inspect(old)
        if result.get("state") in _FAILURES:
            return result
        return {"state": "configured", "settings": str(path), **old.document()}

    if args.config is not None:
        config = args.config
    elif old is not None:
        config = old.config
    elif "REX_CONFIG" in os.environ:
        configured = os.environ["REX_CONFIG"]
        if not configured or "\x00" in configured:
            raise RuntimeError("REX_CONFIG must name a nonempty config path")
        config = Path(configured)
    else:
        config = (
            settings.xdg_path("XDG_CONFIG_HOME", Path.home() / ".config")
            / "rex/init.lua"
        )
    config = Path(os.path.abspath(config))
    config_target = config.resolve(strict=True)
    if not config_target.is_file():
        raise RuntimeError("Selected Rex config must be a regular file")
    workspace = (
        args.workspace.resolve()
        if args.workspace is not None
        else old.workspace
        if old is not None
        else workspace_default
    )
    data_root = old.data_root if old is not None else settings.default_data_root()
    server = (
        args.server
        if args.server is not None
        else (old.server if old is not None else _discover(config))
    )
    candidate = settings.SavedTarget(
        workspace=workspace,
        data_root=data_root,
        config=config,
        config_target=config_target,
        server=server,
        real_server=False,
    )
    selected_setup = candidate.setup()
    _selected_status(selected_setup)
    is_real = lifecycle._is_real_endpoint(selected_setup)
    if args.real_server and not is_real:
        raise RuntimeError(
            "--real-server was selected for an isolated server; remove the flag"
        )
    approval_saved = old is not None and (
        old.config,
        old.config_target,
        old.workspace,
        old.data_root,
        old.server,
        old.real_server,
    ) == (config, config_target, workspace, data_root, server, is_real)
    interactive = sys.stdin.isatty()
    if interactive and not approval_saved:
        print(
            f"Rex config: {config}\nResolved config: {config_target}\nRex server: {server}",
            file=sys.stderr,
        )
    if is_real and not args.real_server and not approval_saved:
        if not interactive:
            raise RuntimeError(
                "Real Rex target requires explicit approval: run rexdeck setup interactively "
                "or pass setup --real-server for approved automation"
            )
        print(
            "Approve this real Rex target for later Files writes? Type yes: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        if sys.stdin.readline().strip() != "yes":
            raise RuntimeError("Real Rex target was not approved; nothing saved")
    candidate = settings.SavedTarget(
        workspace=workspace,
        data_root=data_root,
        config=config,
        config_target=config_target,
        server=server,
        real_server=is_real,
    )
    if old is not None and old != candidate:
        raise RuntimeError(
            "Cannot retarget existing saved Files ownership. Run rexdeck disable files "
            "for the saved target, then deliberately remove "
            f"{path} before setup with --workspace pointing to a fresh directory "
            "for the new target; preserve the old ownership workspace"
        )
    result = _inspect(candidate)
    if result.get("state") in _FAILURES:
        return result
    # Recheck the frozen symlink immediately before publishing the approved settings.
    candidate.setup()
    if candidate != old:
        settings.save(path, candidate)
    return {"state": "configured", "settings": str(path), **candidate.document()}


def main() -> int:
    parser = _Parser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    configure = subcommands.add_parser("setup")
    configure.add_argument("--config", type=Path)
    configure.add_argument("--server")
    configure.add_argument(
        "--workspace", type=Path, help="Reuse an existing native Files workspace"
    )
    configure.add_argument(
        "--real-server",
        action="store_true",
        help="Explicitly approve the real Rex target for subsequent Files writes",
    )
    for name in ("enable", "disable", "status"):
        command = subcommands.add_parser(name)
        command.add_argument(
            "plugin", choices=("files",), nargs="?" if name == "status" else None
        )
    args = parser.parse_args()
    try:
        path = settings.settings_path()
        if args.command == "setup":
            result = _configure(args, path)
        else:
            target = settings.load(path)
            if target is None:
                raise RuntimeError("No saved Rex target; run rexdeck setup first")
            setup = target.setup()
            if args.command == "enable":
                result = lifecycle.apply_setup(setup)
                if result.get("state") == "applied":
                    result = lifecycle.start(setup)
                elif result.get("state") not in _FAILURES:
                    raise RuntimeError(
                        "Files apply did not succeed; Files was not started"
                    )
            elif args.command == "disable":
                result = lifecycle.disable(setup)
            else:
                result = lifecycle.status(setup)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 1 if result.get("state") in _FAILURES else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            json.dumps({"state": "failed", "error": str(error)}, ensure_ascii=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

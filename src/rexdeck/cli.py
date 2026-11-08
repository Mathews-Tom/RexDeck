"""One-time native Rex target approval and independent integration lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Never

from rexdeck import dependencies, lifecycle, settings

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
    """Inspect the saved target without imposing plugin-specific prerequisites."""
    setup = target.setup()
    _selected_status(setup)
    return lifecycle.inspect_setup(setup, check_tools=False)


def _configure(args: argparse.Namespace, path: Path) -> dict[str, object]:
    """Approve and persist one target without retargeting existing ownership."""
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
    config = config.parent.resolve(strict=True) / config.name
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
    server = lifecycle._canonical_server(
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
            "Approve this real Rex target for later integration writes? Type yes: ",
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
            "Cannot retarget existing saved integration ownership. Disable every integration "
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


def _enable(path: Path, target: settings.SavedTarget, plugin: str) -> dict[str, object]:
    """Carry one executable identity through both locked ownership transactions."""
    setup = target.setup(plugin)
    _selected_status(setup)
    qualified_tool: lifecycle.QualifiedTool = dependencies.ensure(plugin)
    # Version probes run outside config locks; revalidate the saved target and
    # running server before publishing an integration.
    current = settings.load(path)
    if current is None or current != target:
        raise RuntimeError(
            "Saved Rex target changed during dependency preparation; no integration "
            f"was registered. Inspect {path}, then rerun rexdeck enable {plugin}"
        )
    setup = current.setup(plugin)
    _selected_status(setup)
    result = lifecycle.apply_setup(setup, expected_tool=qualified_tool)
    if result.get("state") == "applied":
        return lifecycle.start(setup, expected_tool=qualified_tool)
    if result.get("state") not in _FAILURES:
        raise RuntimeError(
            f"{plugin} apply did not succeed; integration was not started"
        )
    return result


def _plugins(target: settings.SavedTarget | None) -> dict[str, object]:
    """List available integrations without hiding sibling faults or requiring setup."""
    if target is None:
        return {
            "state": "not-configured",
            "plugins": {
                plugin: {"state": "not-configured"} for plugin in lifecycle.PLUGINS
            },
        }
    plugins: dict[str, dict[str, object]] = {}
    for plugin in lifecycle.PLUGINS:
        try:
            plugins[plugin] = lifecycle.status(target.setup(plugin))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            plugins[plugin] = {
                "state": "fault",
                "server": target.server,
                "error": f"Cannot inspect {plugin}: {error}",
            }
    failed = any(row.get("state") in _FAILURES for row in plugins.values())
    return {
        "state": "fault"
        if failed
        else "ready"
        if any(row.get("enabled") for row in plugins.values())
        else "disabled",
        "plugins": plugins,
    }


def main() -> int:
    """Dispatch lifecycle commands and isolate faults in aggregate status rows."""
    parser = _Parser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "plugins", help="List available plugins and their enabled/disabled state"
    )
    configure = subcommands.add_parser("setup")
    configure.add_argument("--config", type=Path)
    configure.add_argument("--server")
    configure.add_argument(
        "--workspace", type=Path, help="Reuse an existing native Files workspace"
    )
    configure.add_argument(
        "--real-server",
        action="store_true",
        help="Explicitly approve the real Rex target for subsequent integration writes",
    )
    for name in ("enable", "disable", "status"):
        command = subcommands.add_parser(name)
        command.add_argument(
            "plugin", choices=lifecycle.PLUGINS, nargs="?" if name == "status" else None
        )
    args = parser.parse_args()
    try:
        path = settings.settings_path()
        if args.command == "setup":
            result = _configure(args, path)
        elif args.command == "plugins":
            result = _plugins(settings.load(path))
        else:
            target = settings.load(path)
            if target is None:
                raise RuntimeError("No saved Rex target; run rexdeck setup first")
            if args.command == "enable":
                result = _enable(path, target, args.plugin)
            elif args.command == "disable":
                result = lifecycle.disable(target.setup(args.plugin))
            else:
                if args.plugin:
                    result = lifecycle.status(target.setup(args.plugin))
                else:
                    result = _plugins(target)
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

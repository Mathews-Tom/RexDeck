"""Explicit, removable native Rex action setup; no managed processes or runtime."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType
from typing import NoReturn, cast
from urllib.parse import quote, unquote

_BEGIN = b"-- BEGIN REXDECK (owned native Files loader)\n"
_END = b"-- END REXDECK\n"
_MAX_READ = 2 * 1024 * 1024
_COMMAND_WAIT = 8.0
PLUGINS = ("files", "git")


@dataclass(frozen=True)
class Setup:
    project: Path
    workspace: Path
    config: Path
    data_root: Path
    server: str
    real_server: bool = False
    plugin: str = "files"


@dataclass(frozen=True)
class _Paths:
    project: Path
    workspace: Path
    selected: Path
    config: Path
    data_root: Path
    module: Path
    receipt: Path
    plugin: str


@dataclass(frozen=True)
class _File:
    data: bytes | None
    mode: int


@dataclass(frozen=True)
class _Change:
    path: Path
    before: _File
    after: bytes | None
    loader: bool = False


@dataclass(frozen=True)
class _Result:
    code: int
    output: bytes


def _socket_path(endpoint: str) -> Path:
    if not endpoint.startswith("unix://"):
        raise RuntimeError("An explicit unix:// Rex socket endpoint is required")
    try:
        decoded = unquote(endpoint[len("unix://") :], errors="strict")
        path = Path(decoded)
        if not path.is_absolute() or "\x00" in decoded:
            raise RuntimeError("Rex Unix socket endpoint must name an absolute path")
        return path.resolve()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            f"Cannot resolve the selected Rex socket endpoint: {exc}"
        ) from exc


def _canonical_server(endpoint: str) -> str:
    return "unix://" + quote(str(_socket_path(endpoint)), safe="/")


def _validate_data_root(root: Path, plugin: str | None) -> None:
    """Reject redirection of shared parents and any selected deployment directory."""
    try:
        if (
            not root.is_absolute()
            or ".." in root.parts
            or "\x00" in str(root)
            or root.resolve() != root
        ):
            raise RuntimeError("Files data root must remain a resolved absolute path")
        paths: tuple[Path, ...] = (root, root / "plugins")
        if plugin is not None:
            paths += (root / "plugins" / plugin,)
        for path in paths:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeError(f"Files deployment directory is redirected: {path}")
    except OSError as exc:
        raise RuntimeError(f"Cannot validate Files data directory: {exc}") from exc


def _paths(setup: Setup) -> _Paths:
    """Resolve the selected target without consulting unused sibling deployments."""
    if setup.plugin not in PLUGINS:
        raise RuntimeError(f"Unknown integration: {setup.plugin}")
    if not setup.server.strip() or setup.server != setup.server.strip():
        raise RuntimeError("An explicit nonempty Rex server endpoint is required")
    socket = _socket_path(setup.server)
    if not setup.real_server and not any(
        socket.is_relative_to(root.resolve())
        for root in (Path("/tmp"), Path("/private/tmp"))
    ):
        raise RuntimeError(
            "Isolated endpoints must be Unix sockets under /tmp or /private/tmp; "
            "use --real-server only for an explicitly approved real-server application"
        )
    try:
        selected = setup.config.absolute()
        config = selected.resolve(strict=True)
        if not config.is_file():
            raise RuntimeError(f"Selected Rex config is not a regular file: {config}")
        project = setup.project.resolve(strict=True)
        workspace = setup.workspace.resolve()
        if setup.plugin != "files":
            workspace = workspace / setup.plugin
            if workspace.is_symlink():
                raise RuntimeError(f"Integration workspace is redirected: {workspace}")
        if workspace.exists() and not workspace.is_dir():
            raise RuntimeError(f"Setup workspace must be a directory: {workspace}")
    except OSError as exc:
        raise RuntimeError(f"Cannot resolve the explicit setup paths: {exc}") from exc
    _validate_data_root(setup.data_root, setup.plugin)
    return _Paths(
        project,
        workspace,
        selected,
        config,
        setup.data_root,
        setup.data_root / f"plugins/{setup.plugin}/rexdeck_{setup.plugin}.lua",
        workspace / "setup.json",
        setup.plugin,
    )


def _env(setup: Setup) -> dict[str, str]:
    result = {
        k: v
        for k, v in os.environ.items()
        if k not in {"REX_SESSION", "REX_BLOCK", "REX_CLIENT", "REX_SERVER"}
    }
    result["REX_CONFIG"] = str(setup.config.absolute())
    return result


def _executable(name: str) -> str:
    search_path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
        if Path(entry).is_absolute()
    )
    found = shutil.which(name, path=search_path) if search_path else None
    if found is None:
        if name == "rex":
            raise RuntimeError(
                "Rex is an externally installed prerequisite and was not found on PATH; "
                "install Rex using its supported distribution, make rex available on PATH, "
                "start its server, then rerun rexdeck setup. RexDeck never installs Rex"
            )
        raise RuntimeError(
            f"Required executable not found: {name}; install it before applying or starting"
        )
    return str(Path(found).absolute())


def _tools(tool: str = "yazi") -> dict[str, str]:
    """Bind the current interpreter, Rex and selected upstream tool to paths."""
    python = Path(sys.executable).absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError(f"Current Python interpreter is not executable: {python}")
    return {
        "python": str(python),
        "rex": _executable("rex"),
        tool: _executable(tool),
    }


def _selected_tools(setup: Setup) -> dict[str, str]:
    """Resolve only the executables required by the selected integration."""
    return _tools() if setup.plugin == "files" else _tools("lazygit")


def _require_qualified_tool(
    setup: Setup, tools: dict[str, str], expected_tool: str | None
) -> None:
    """Check the exact binding being persisted, without resolving PATH again."""
    tool = "yazi" if setup.plugin == "files" else "lazygit"
    if expected_tool is not None and tools[tool] != expected_tool:
        raise RuntimeError(
            f"{tool} executable changed after qualification "
            f"({expected_tool} -> {tools[tool]}); rerun rexdeck enable {setup.plugin}"
        )


def _run(
    argv: list[str],
    env: dict[str, str],
    *,
    timeout: float = _COMMAND_WAIT,
    inherit_stderr: bool = False,
) -> _Result:
    """Bound captured output/lifetime; optionally leave diagnostics on the terminal."""
    child: subprocess.Popen[bytes] | None = None
    try:
        with _defer_cancellation():
            child = subprocess.Popen(
                argv,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=None if inherit_stderr else subprocess.STDOUT,
            )
        assert child.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Command timed out: {argv[0]} {' '.join(argv[1:])}"
                    )
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, min(65536, _MAX_READ + 1 - len(output)))
                    if not chunk:
                        selector.unregister(child.stdout)
                    else:
                        output.extend(chunk)
                        if len(output) > _MAX_READ:
                            raise RuntimeError(
                                f"Command output exceeds {_MAX_READ} bytes: {argv[0]}"
                            )
        return _Result(
            child.wait(timeout=max(0.01, deadline - time.monotonic())), bytes(output)
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        with _defer_cancellation():
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
        if isinstance(exc, (RuntimeError, KeyboardInterrupt)):
            raise
        raise RuntimeError(f"Command failed: {argv[0]}: {exc}") from exc
    finally:
        if child is not None and child.stdout is not None:
            child.stdout.close()


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RuntimeError(f"Expected a JSON object in {context}")
    return cast(dict[str, object], value)


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError(f"Expected a JSON list in {context}")
    return cast(list[object], value)


def _decode(data: bytes, context: str) -> dict[str, object]:
    try:
        value: object = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"Invalid JSON in {context}: {exc}") from exc
    return _object(value, context)


def _json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _rex(setup: Setup, *args: str) -> dict[str, object]:
    result = _run(
        [
            _executable("rex"),
            "-S",
            setup.server,
            "--autostart=false",
            "--timeout",
            "3s",
            *args,
        ],
        _env(setup),
    )
    if result.code != 0:
        raise RuntimeError(
            f"rex {' '.join(args)} failed ({result.code}): "
            f"{result.output.decode(errors='replace').strip()}"
        )
    return _decode(result.output, f"rex {' '.join(args)}")


def _server_status(setup: Setup, *, selected: bool) -> dict[str, object] | None:
    argv = [_executable("rex"), "--autostart=false", "--timeout", "3s"]
    if selected:
        argv += ["-S", setup.server]
    result = _run([*argv, "server", "status", "--json"], _env(setup))
    if result.code != 0:
        detail = result.output.decode(errors="replace")
        if not selected and "rex server is not running" in detail:
            return None
        raise RuntimeError(
            f"Cannot prove selected/default Rex endpoint isolation: {detail.strip()}"
        )
    value = _decode(result.output, "Rex server status")
    if any(
        not isinstance(value.get(key), str) or not value[key]
        for key in ("endpoint", "instance_id")
    ):
        raise RuntimeError("Rex server status lacks endpoint/instance identity")
    return value


def _is_real_endpoint(setup: Setup) -> bool:
    selected = _socket_path(setup.server)
    known = [
        Path.home() / "Library/Application Support/rex/server.sock",
        Path("/run/user") / str(os.getuid()) / "rex/server.sock",
    ]
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and Path(xdg).is_absolute():
        known.append(Path(xdg) / "rex/server.sock")
    is_real = selected in [path.resolve() for path in known]
    is_real = is_real or str(selected).endswith(
        "/Library/Application Support/rex/server.sock"
    )
    is_real = is_real or (
        selected.name == "server.sock"
        and selected.parent.name == "rex"
        and selected.parent.parent.parent == Path("/run/user")
        and selected.parent.parent.name.isdecimal()
    )
    if not is_real:
        default = _server_status(setup, selected=False)
        current = _server_status(setup, selected=True)
        assert current is not None
        if _socket_path(cast(str, current["endpoint"])) != selected:
            raise RuntimeError(
                "Selected server answered with a different socket endpoint"
            )
        if default is not None:
            is_real = (
                _socket_path(cast(str, default["endpoint"])) == selected
                or default["instance_id"] == current["instance_id"]
            )
    return is_real


def _endpoint_policy(setup: Setup) -> None:
    is_real = _is_real_endpoint(setup)
    if is_real and not setup.real_server:
        raise RuntimeError(
            "Selected endpoint is the real Rex server; explicit --real-server approval is required"
        )
    keymap = _rex(setup, "api", "call", "client.keymap", "{}")
    config = keymap.get("config")
    path = config.get("path") if isinstance(config, dict) else None
    if not isinstance(path, str) or not Path(path).is_absolute() or "\x00" in path:
        raise RuntimeError("Running Rex server did not identify its config path")
    if Path(path).resolve(strict=True) != setup.config.resolve(strict=True):
        raise RuntimeError(
            f"Selected Rex config does not match the running server config: {path}"
        )


def _check(setup: Setup) -> None:
    result = _rex(setup, "config", "check", "--json")
    if result.get("diagnostics"):
        raise RuntimeError(f"Selected Rex config was rejected: {result['diagnostics']}")


def _reload(setup: Setup) -> None:
    result = _rex(setup, "config", "reload", "--json")
    if result.get("diagnostics"):
        raise RuntimeError(
            f"Selected Rex config reload was rejected: {result['diagnostics']}"
        )


def _file(path: Path) -> _File:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(
                f"Refusing nonregular or symlinked owned artifact: {path}"
            )
        with path.open("rb") as stream:
            data = stream.read(_MAX_READ + 1)
        if len(data) > _MAX_READ:
            raise RuntimeError(f"Owned file exceeds {_MAX_READ} bytes: {path}")
        return _File(data, stat.S_IMODE(info.st_mode))
    except FileNotFoundError:
        return _File(None, 0o600)
    except OSError as exc:
        raise RuntimeError(f"Cannot read {path}: {exc}") from exc


def _replace(
    path: Path, before: _File, after: bytes | None, *, mode: int | None = None
) -> None:
    if _file(path) != before:
        raise RuntimeError(
            f"Concurrent edit or mode change at {path}; nothing overwritten"
        )
    if after is None:
        path.unlink()
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), before.mode if mode is None else mode)
        if _file(path) != before:
            raise RuntimeError(f"Concurrent edit at {path}; nothing overwritten")
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeError(f"Cannot atomically write {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _digest(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def _lua(value: str) -> str:
    """Quote UTF-8 bytes using Lua escapes, never JSON-only Unicode escapes."""
    return (
        '"'
        + "".join(
            chr(byte) if 32 <= byte < 127 and byte not in (34, 92) else f"\\{byte:03d}"
            for byte in value.encode("utf-8")
        )
        + '"'
    )


def _loader(paths: _Paths) -> bytes:
    """Build the tracked loader while preserving native Files loader identity."""
    # Rex tracks required modules. Restore both its cache and the user's search path.
    if ";" in str(paths.module.parent) or "?" in str(paths.module.parent):
        raise RuntimeError(
            "Rex cannot track a module in a directory containing ';' or '?'"
        )
    search = _lua(str(paths.module.parent / "?.lua") + ";")
    module = _lua(paths.module.stem)
    begin = (
        _BEGIN
        if paths.plugin == "files"
        else b"-- BEGIN REXDECK (owned native Git loader)\n"
    )
    end = _END if paths.plugin == "files" else b"-- END REXDECK git\n"
    return (
        begin
        + (
            f"do local previous, cached = package.path, package.loaded[{module}]; "
            f"package.path = {search} .. previous; package.loaded[{module}] = nil; "
            f"local ok, err = pcall(require, {module}); "
            f"package.loaded[{module}] = cached; package.path = previous; "
            f"if not ok then error('RexDeck {'Files' if paths.plugin == 'files' else 'Git'} unavailable: ' .. tostring(err)) end end\n"
        ).encode()
        + end
    )


def _expect_action(setup: Setup, enabled: bool) -> None:
    """Require exact owned action identity or verified absence after reload."""
    title = "Files" if setup.plugin == "files" else "Git"
    listing = _rex(setup, "actions", "--json")
    actions = [
        _object(row, "Rex action")
        for row in _list(listing.get("actions"), "Rex actions")
    ]
    matches = [row for row in actions if row.get("name") == f"rexdeck_{setup.plugin}"]
    if not enabled:
        if matches:
            raise RuntimeError(
                f"Native {title} action remains registered or is foreign"
            )
        return
    if len(matches) != 1:
        raise RuntimeError(
            f"Native {title} action is missing or ambiguous after config reload"
        )
    action = matches[0]
    if (
        action.get("kind") != "lua"
        or action.get("title") != f"RexDeck {title}"
        or action.get("category") != "RexDeck"
    ):
        raise RuntimeError(
            f"{title} action identity is not the owned native Lua action"
        )
    source = action.get("source")
    if not isinstance(source, str):
        raise RuntimeError(f"{title} action has no native source identity")
    filename, separator, line = source.rpartition(":")
    paths = _paths(setup)
    owned_sources = {str(paths.module)} | {
        str(paths.module.relative_to(parent))
        for parent in (paths.selected.parent, paths.config.parent)
        if paths.module.is_relative_to(parent)
    }
    if not separator or not line.isdecimal() or filename not in owned_sources:
        raise RuntimeError(
            f"{title} action is registered from an unexpected config source"
        )


def _without_loader(
    data: bytes, loader: bytes, *, remove_separator: bool = False
) -> bytes:
    """Remove one exact loader, preserving foreign content and separator ownership."""
    lines = loader.splitlines(keepends=True)
    if (
        data.count(lines[0]) != 1
        or data.count(lines[-1]) != 1
        or data.count(loader) != 1
    ):
        raise RuntimeError(
            "RexDeck loader changed or is ambiguous; preserve it for manual reconciliation"
        )
    prefix, _, tail = data.partition(loader)
    if remove_separator and not tail and prefix.endswith(b"\n"):
        prefix = prefix[:-1]
    return prefix + tail


def _sources(paths: _Paths) -> tuple[bytes, bytes]:
    source = _file(paths.project / "plugins/files/main.lua").data
    launcher = _file(paths.project / "plugins/files/launch.py").data
    if not source or not launcher:
        raise RuntimeError("Canonical native Files main.lua and launch.py are required")
    try:
        source.decode("utf-8")
        launcher.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError("Native Files module and launcher must be UTF-8") from exc
    return source, launcher


def _module(
    setup: Setup, source: bytes, launcher: bytes, tools: dict[str, str], enabled: bool
) -> bytes:
    """Embed canonical action and launcher code for one selected integration."""
    opts = tools | {
        "server": setup.server,
        "launcher_code": launcher.decode("utf-8"),
        "plugin": setup.plugin,
    }
    quoted = ", ".join(f"{key} = {_lua(value)}" for key, value in sorted(opts.items()))
    return (
        b"-- Generated from canonical plugins/files/main.lua by RexDeck.\n"
        b"local install = (function()\n"
        + source
        + b"\nend)()\n"
        + f"if {'true' if enabled else 'false'} then install({{{quoted}}}) end\n".encode()
    )


def _receipt(paths: _Paths, setup: Setup) -> dict[str, object] | None:
    """Read a receipt only if its schema, target and ownership fields are valid."""
    snapshot = _file(paths.receipt)
    if snapshot.data is None:
        return None
    result = _decode(snapshot.data, str(paths.receipt))
    expected: dict[str, object] = {
        "version": 3,
        "kind": f"native-{setup.plugin}",
        "workspace": str(paths.workspace),
        "data_root": str(paths.data_root),
        "config": str(paths.selected),
        "config_target": str(paths.config),
        "server": setup.server,
        "real_server": setup.real_server,
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise RuntimeError(
            "Unknown/old receipt or different setup identity; use a fresh native workspace"
        )
    # Package location is provenance, not ownership: upgrades may remove the old root.
    project = result.get("project")
    if (
        not isinstance(project, str)
        or not Path(project).is_absolute()
        or "\x00" in project
    ):
        raise RuntimeError("Setup receipt has malformed project provenance")
    for key in ("applied", "enabled"):
        if not isinstance(result.get(key), bool):
            raise RuntimeError(f"Setup receipt has no explicit {key} state")
    if result["enabled"] and not result["applied"]:
        raise RuntimeError("Unapplied receipt cannot enable Files")
    for key in ("module_sha256", "source_sha256", "launcher_sha256"):
        value = result.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise RuntimeError(f"Setup receipt has malformed {key}")
    mode = result.get("module_mode")
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0o7777:
        raise RuntimeError("Setup receipt has malformed module_mode")
    if result.get("loader_separator") not in ("", "\n"):
        raise RuntimeError("Setup receipt has malformed loader_separator")
    tools = _object(result.get("tools"), "receipt tools")
    tool = "yazi" if setup.plugin == "files" else "lazygit"
    if set(tools) != {"python", "rex", tool} or any(
        not isinstance(value, str) or not Path(value).is_absolute() or "\x00" in value
        for value in tools.values()
    ):
        raise RuntimeError("Setup receipt has malformed tools")
    conflicts = _list(result.get("conflicts"), "receipt conflicts")
    if any(not isinstance(item, str) for item in conflicts):
        raise RuntimeError("Setup receipt has malformed conflicts")
    return result


def _owned_config(paths: _Paths, receipt: dict[str, object]) -> None:
    module = _file(paths.module)
    if (
        _digest(module.data) != receipt["module_sha256"]
        or module.mode != receipt["module_mode"]
    ):
        raise RuntimeError(f"Owned native module changed or is missing: {paths.module}")
    data = _file(paths.config).data
    if data is None:
        raise RuntimeError(f"Selected config disappeared: {paths.config}")
    _without_loader(data, _loader(paths))


def _current_bundle(setup: Setup, paths: _Paths, receipt: dict[str, object]) -> None:
    """Require owned artifacts to match the current package and executable paths."""
    _owned_config(paths, receipt)
    source, launcher = _sources(paths)
    tools = _selected_tools(setup)
    if (
        _digest(source) != receipt["source_sha256"]
        or _digest(launcher) != receipt["launcher_sha256"]
        or tools != receipt["tools"]
        or _digest(_module(setup, source, launcher, tools, bool(receipt["enabled"])))
        != receipt["module_sha256"]
    ):
        raise RuntimeError(
            f"Canonical {setup.plugin} source, launcher or tool paths changed; "
            f"run rexdeck enable {setup.plugin} to refresh the owned integration"
        )


@contextmanager
def _defer_cancellation() -> Iterator[None]:
    """Finish an ownership handoff or recovery before delivering cancellation."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signals: tuple[int, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {signum: signal.getsignal(signum) for signum in signals}
    interrupted: int | None = None

    def defer(signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        if interrupted is None and previous[signum] != signal.SIG_IGN:
            interrupted = signum

    try:
        for signum in signals:
            signal.signal(signum, defer)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if interrupted is not None:
            handler = previous[interrupted]
            if callable(handler):
                handler(interrupted, None)
            elif handler == signal.SIG_DFL:
                raise KeyboardInterrupt(f"Received signal {interrupted}")


def _rollback(changes: list[_Change], data_root: Path, plugin: str) -> list[str]:
    """Restore owned preimages without overwriting concurrent edits."""
    conflicts: list[str] = []
    for change in reversed(changes):
        try:
            if change.path.is_relative_to(data_root):
                _validate_data_root(data_root, plugin)
            current = _file(change.path)
            if current == change.before:
                continue
            if change.loader and current.data is not None and change.after is not None:
                before_data = change.before.data or b""
                if change.after.startswith(before_data):
                    added = change.after[len(before_data) :]
                    restored = _without_loader(
                        current.data,
                        added.removeprefix(b"\n"),
                        remove_separator=added.startswith(b"\n"),
                    )
                elif current.data.startswith(change.after):
                    restored = before_data + current.data[len(change.after) :]
                else:
                    raise RuntimeError(
                        f"Rollback preserves concurrent config edits: {change.path}"
                    )
                _replace(change.path, current, restored)
            else:
                if current.data != change.after or (
                    change.after is not None and current.mode != change.before.mode
                ):
                    raise RuntimeError(
                        f"Rollback preserves concurrently changed artifact: {change.path}"
                    )
                _replace(
                    change.path, current, change.before.data, mode=change.before.mode
                )
        except (RuntimeError, OSError) as exc:
            conflicts.append(str(exc))
    return conflicts


def _recover_changes(
    setup: Setup,
    changes: list[_Change],
    operation: str,
    cause: RuntimeError | OSError | KeyboardInterrupt,
) -> NoReturn:
    """Recover owned preimages and surface failure or cancellation with conflicts."""
    conflicts: list[str] = []
    cancellation: KeyboardInterrupt | None = None
    try:
        with _defer_cancellation():
            conflicts = _rollback(changes, setup.data_root, setup.plugin)
            try:
                _reload(setup)
            except RuntimeError as reload_error:
                conflicts.append(f"Rollback config reload failed: {reload_error}")
    except KeyboardInterrupt as interrupted:
        cancellation = interrupted
    suffix = (
        f"; recovery needs judgment: {'; '.join(conflicts)}"
        if conflicts
        else "; owned changes rolled back"
    )
    if isinstance(cause, KeyboardInterrupt):
        cause.add_note(f"{operation} cancelled{suffix}")
        raise cause
    if cancellation is not None:
        cancellation.add_note(f"{operation} cancelled after failure: {cause}{suffix}")
        raise cancellation from cause
    raise RuntimeError(f"{operation} failed: {cause}{suffix}") from cause


@contextmanager
def _operation_lock(paths: _Paths) -> Iterator[None]:
    """Serialize config, deployment-root and workspace mutations without waiting."""
    config_descriptor: int | None = None
    deployment_descriptor: int | None = None
    try:
        config_descriptor = os.open(
            paths.config.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fcntl.flock(config_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another RexDeck lifecycle operation is using the shared config"
            ) from exc
        _validate_data_root(paths.data_root, paths.plugin)
        paths.data_root.mkdir(parents=True, exist_ok=True)
        _validate_data_root(paths.data_root, paths.plugin)
        deployment_descriptor = os.open(
            paths.data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fcntl.flock(deployment_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another RexDeck lifecycle operation is using the Files data root"
            ) from exc
        descriptor = os.open(
            paths.workspace / "lifecycle.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "r+b") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RuntimeError("Lifecycle lock is not a regular file")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Another RexDeck lifecycle operation is in progress; inspect status"
                ) from exc
            terminating = (signal.SIGTERM, signal.SIGHUP)
            previous = (
                {signum: signal.getsignal(signum) for signum in terminating}
                if threading.current_thread() is threading.main_thread()
                else {}
            )

            def interrupt(signum: int, _frame: FrameType | None) -> None:
                raise KeyboardInterrupt(f"Received signal {signum}")

            try:
                for signum in previous:
                    signal.signal(signum, interrupt)
                yield
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise RuntimeError(f"Cannot lock the owned lifecycle operation: {exc}") from exc
    finally:
        if deployment_descriptor is not None:
            os.close(deployment_descriptor)
        if config_descriptor is not None:
            os.close(config_descriptor)


def _transaction(
    setup: Setup,
    paths: _Paths,
    changes: list[_Change],
    receipt: dict[str, object],
    before: _File,
    operation: str,
) -> None:
    """Publish owned changes and receipt only after native reload verification."""
    for change in changes:
        if change.after is not None and len(change.after) > _MAX_READ:
            raise RuntimeError(
                f"Planned owned file exceeds {_MAX_READ} bytes: {change.path}"
            )
    applied: list[_Change] = []
    try:
        for change in changes:
            _validate_data_root(paths.data_root, paths.plugin)
            if change.path == paths.module and change.after is not None:
                paths.module.parent.mkdir(parents=True, exist_ok=True)
                _validate_data_root(paths.data_root, paths.plugin)
            if paths.selected.resolve(strict=True) != paths.config:
                raise RuntimeError(
                    "Selected Rex config symlink changed during operation"
                )
            applied.append(change)
            _replace(change.path, change.before, change.after)
        _validate_data_root(paths.data_root, paths.plugin)
        if paths.selected.resolve(strict=True) != paths.config:
            raise RuntimeError(
                "Selected Rex config symlink changed before config validation"
            )
        _check(setup)
        _reload(setup)
        _validate_data_root(paths.data_root, paths.plugin)
        if paths.selected.resolve(strict=True) != paths.config:
            raise RuntimeError("Selected Rex config symlink changed during reload")
        if receipt["applied"]:
            _current_bundle(setup, paths, receipt)
        _expect_action(setup, bool(receipt["enabled"]))
        publication = _Change(paths.receipt, before, _json(receipt))
        applied.append(publication)
        _replace(publication.path, publication.before, publication.after)
    except (RuntimeError, OSError, KeyboardInterrupt) as exc:
        _recover_changes(setup, applied, operation, exc)


def _sibling(setup: Setup) -> tuple[_Paths, Setup]:
    """Resolve the other integration only when its loader needs ownership checks."""
    sibling = replace(setup, plugin="git" if setup.plugin == "files" else "files")
    return _paths(sibling), sibling


def _unowned(paths: _Paths, setup: Setup) -> None:
    """Reject foreign selected artifacts and validate any existing sibling loader."""
    # Only a validated sibling receipt can account for another loader.
    for path in (
        paths.module,
        paths.workspace / "hub.json",
        paths.workspace / "hub-state",
        paths.workspace / "runtime",
        paths.config.parent / "rexdeck_palette.lua",
    ):
        if path.exists() or path.is_symlink():
            raise RuntimeError(
                f"Unowned existing artifact: {path}; use a fresh native workspace"
            )
    data = _file(paths.config).data or b""
    if b"-- BEGIN REXDECK" not in data and b"-- END REXDECK" not in data:
        return
    sibling, sibling_setup = _sibling(setup)
    receipt = _receipt(sibling, sibling_setup)
    if receipt is not None and receipt["applied"]:
        _owned_config(sibling, receipt)
        data = _without_loader(data, _loader(sibling))
    if b"-- BEGIN REXDECK" in data or b"-- END REXDECK" in data:
        raise RuntimeError("Unowned or old RexDeck loader; nothing changed")


def inspect_setup(setup: Setup, *, check_tools: bool = True) -> dict[str, object]:
    """Inspect ownership, optionally including selected integration prerequisites."""
    paths = _paths(setup)
    receipt = _receipt(paths, setup)
    prerequisites: dict[str, object] = {}
    conflicts = cast(list[str], receipt["conflicts"]).copy() if receipt else []
    if check_tools:
        try:
            prerequisites.update(_selected_tools(setup))
            _sources(paths)
        except RuntimeError as exc:
            prerequisites["error"] = str(exc)
    try:
        _endpoint_policy(setup)
        if receipt is None:
            _unowned(paths, setup)
            _expect_action(setup, False)
        elif receipt["applied"]:
            _owned_config(paths, receipt)
            _expect_action(setup, bool(receipt["enabled"]))
    except RuntimeError as exc:
        conflicts.append(str(exc))
    return {
        "state": "conflict" if conflicts else "inspected",
        "server": setup.server,
        "config": str(paths.selected),
        "config_target": str(paths.config),
        "owned": receipt is not None,
        "applied": bool(receipt and receipt["applied"]),
        "enabled": bool(receipt and receipt["enabled"]),
        "owned_files": [str(paths.module), str(paths.receipt)],
        "loader": _loader(paths).decode(),
        "prerequisites": prerequisites,
        "conflicts": conflicts,
    }


def apply_setup(setup: Setup, *, expected_tool: str | None = None) -> dict[str, object]:
    """Apply the current binding, optionally requiring a previously qualified path."""
    paths = _paths(setup)
    _receipt(paths, setup)  # Refuse unknown ownership before creating even a lock.
    _endpoint_policy(setup)
    if not paths.receipt.exists():
        _unowned(paths, setup)
        _expect_action(setup, False)
    try:
        paths.workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Cannot create owned setup workspace: {exc}") from exc
    with _operation_lock(paths):
        before = _file(paths.receipt)
        receipt = _receipt(paths, setup)
        config = _file(paths.config)
        assert config.data is not None
        previous_module = _file(paths.module)
        if receipt is None:
            _unowned(paths, setup)
            _expect_action(setup, False)
        elif receipt["applied"]:
            _owned_config(paths, receipt)
        else:
            _unowned(paths, setup)
            _expect_action(setup, False)
        source, launcher = _sources(paths)
        tools = _selected_tools(setup)
        _require_qualified_tool(setup, tools, expected_tool)
        enabled = bool(receipt and receipt["applied"] and receipt["enabled"])
        module = _module(setup, source, launcher, tools, enabled)
        changes: list[_Change] = []
        if previous_module.data != module:
            changes.append(_Change(paths.module, previous_module, module))
        if receipt is None or not receipt["applied"]:
            separator = "" if not config.data or config.data.endswith(b"\n") else "\n"
            changes.append(
                _Change(
                    paths.config,
                    config,
                    config.data + separator.encode() + _loader(paths),
                    True,
                )
            )
        else:
            separator = cast(str, receipt["loader_separator"])
        receipt = {
            "version": 3,
            "kind": f"native-{setup.plugin}",
            "project": str(paths.project),
            "workspace": str(paths.workspace),
            "data_root": str(paths.data_root),
            "config": str(paths.selected),
            "config_target": str(paths.config),
            "server": setup.server,
            "real_server": setup.real_server,
            "applied": True,
            "enabled": enabled,
            "module_sha256": _digest(module),
            "module_mode": previous_module.mode,
            "source_sha256": _digest(source),
            "launcher_sha256": _digest(launcher),
            "tools": tools,
            "loader_separator": separator,
            "conflicts": [],
        }
        _transaction(setup, paths, changes, receipt, before, "Apply")
        return {
            "state": "applied",
            "enabled": enabled,
            "server": setup.server,
            "changed": [str(change.path) for change in changes],
        }


def start(setup: Setup, *, expected_tool: str | None = None) -> dict[str, object]:
    """Enable a current bundle, optionally requiring its previously qualified path."""
    paths = _paths(setup)
    receipt = _receipt(paths, setup)
    if receipt is None or not receipt["applied"]:
        raise RuntimeError(
            f"{setup.plugin} is not installed; run rexdeck enable {setup.plugin}"
        )
    _endpoint_policy(setup)
    with _operation_lock(paths):
        before = _file(paths.receipt)
        receipt = _receipt(paths, setup)
        if receipt is None or not receipt["applied"]:
            raise RuntimeError("Setup ownership changed before selection")
        old = _file(paths.module)
        _current_bundle(setup, paths, receipt)
        if not receipt["enabled"]:
            _expect_action(setup, False)
        source, launcher = _sources(paths)
        tools = cast(dict[str, str], receipt["tools"])
        _require_qualified_tool(setup, tools, expected_tool)
        module = _module(setup, source, launcher, tools, True)
        receipt["enabled"] = True
        receipt["module_sha256"] = _digest(module)
        changes = [_Change(paths.module, old, module)] if old.data != module else []
        _transaction(setup, paths, changes, receipt, before, "Start")
        return {
            "state": "ready",
            "enabled": True,
            "server": setup.server,
        }


def status(setup: Setup) -> dict[str, object]:
    """Report selected ownership and registration, not upstream process readiness."""
    paths = _paths(setup)
    receipt = _receipt(paths, setup)
    try:
        _endpoint_policy(setup)
        if receipt is not None and receipt["applied"]:
            _current_bundle(setup, paths, receipt)
        elif receipt is None:
            _unowned(paths, setup)
        _expect_action(setup, bool(receipt and receipt["enabled"]))
    except RuntimeError as exc:
        return {"state": "fault", "server": setup.server, "error": str(exc)}
    conflicts = receipt["conflicts"] if receipt else []
    return {
        "state": "conflict"
        if conflicts
        else "ready"
        if receipt and receipt["enabled"]
        else "disabled",
        "server": setup.server,
        "applied": bool(receipt and receipt["applied"]),
        "enabled": bool(receipt and receipt["enabled"]),
        "conflicts": conflicts,
    }


def disable(setup: Setup) -> dict[str, object]:
    """Remove selected owned artifacts, preserving siblings and concurrent edits."""
    paths = _paths(setup)
    receipt = _receipt(paths, setup)
    _endpoint_policy(setup)
    if receipt is None:
        _unowned(paths, setup)
        _expect_action(setup, False)
        return {"state": "disabled", "server": setup.server, "conflicts": []}
    with _operation_lock(paths):
        before = _file(paths.receipt)
        receipt = _receipt(paths, setup)
        assert receipt is not None
        if not receipt["applied"]:
            return {
                "state": "conflict" if receipt["conflicts"] else "disabled",
                "server": setup.server,
                "conflicts": receipt["conflicts"],
            }
        config = _file(paths.config)
        assert config.data is not None
        changes: list[_Change] = []
        if receipt["applied"]:
            try:
                restored = _without_loader(
                    config.data,
                    _loader(paths),
                    remove_separator=receipt["loader_separator"] == "\n",
                )
                # The first loader owns a separator needed by any following one.
                # Transfer it before removing that loader, so either disable order
                # restores a config that originally had no final newline.
                tail = config.data.partition(_loader(paths))[2]
                if receipt["loader_separator"] == "\n" and tail.startswith(
                    b"-- BEGIN REXDECK"
                ):
                    sibling, sibling_setup = _sibling(setup)
                    if tail.startswith(_loader(sibling)):
                        sibling_before = _file(sibling.receipt)
                        sibling_receipt = _receipt(sibling, sibling_setup)
                        if sibling_receipt is None or not sibling_receipt["applied"]:
                            raise RuntimeError("Following loader has no owned receipt")
                        _owned_config(sibling, sibling_receipt)
                        sibling_receipt["loader_separator"] = "\n"
                        changes.append(
                            _Change(
                                sibling.receipt, sibling_before, _json(sibling_receipt)
                            )
                        )
            except RuntimeError as exc:
                return {
                    "state": "conflict",
                    "server": setup.server,
                    "conflicts": [str(exc)],
                }
            changes.append(_Change(paths.config, config, restored, True))
        conflicts: list[str] = []
        try:
            module = _file(paths.module)
            if module.data is not None:
                if (
                    _digest(module.data) == receipt["module_sha256"]
                    and module.mode == receipt["module_mode"]
                ):
                    changes.append(_Change(paths.module, module, None))
                else:
                    conflicts.append(f"Changed native module preserved: {paths.module}")
        except RuntimeError as exc:
            conflicts.append(str(exc))
        receipt["enabled"] = False
        receipt["applied"] = False
        receipt["conflicts"] = conflicts
        _transaction(setup, paths, changes, receipt, before, "Disable")
        return {
            "state": "conflict" if conflicts else "disabled",
            "server": setup.server,
            "conflicts": conflicts,
        }

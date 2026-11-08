"""Bounded, atomic storage of the explicitly approved native Files target."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from rexdeck import lifecycle

_MAX_SETTINGS = 16 * 1024
_FIELDS = {
    "version",
    "kind",
    "workspace",
    "data_root",
    "config",
    "config_target",
    "server",
    "real_server",
}


def xdg_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if value is None:
        return default
    if not value or not Path(value).is_absolute() or "\x00" in value:
        raise RuntimeError(f"{name} must be a nonempty absolute path")
    return Path(value)


def settings_path() -> Path:
    return xdg_path("XDG_CONFIG_HOME", Path.home() / ".config") / "rexdeck/config.json"


def default_workspace() -> Path:
    return (
        xdg_path("XDG_STATE_HOME", Path.home() / ".local/state") / "rexdeck/files"
    ).resolve()


def default_data_root() -> Path:
    return (
        xdg_path("XDG_DATA_HOME", Path.home() / ".local/share") / "rexdeck"
    ).resolve()


def _absolute(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError(f"Saved {name} must be a nonempty absolute path")
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise RuntimeError(f"Saved {name} must be a normalized absolute path")
    return path


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"Duplicate settings field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class SavedTarget:
    workspace: Path
    data_root: Path
    config: Path
    config_target: Path
    server: str
    real_server: bool

    def setup(self) -> lifecycle.Setup:
        if self.config.resolve(strict=True) != self.config_target:
            raise RuntimeError(
                "Saved Rex config target changed; restore the selected symlink before "
                "using rexdeck setup or changing Files"
            )
        if self.workspace.resolve() != self.workspace:
            raise RuntimeError("Saved Files workspace target changed; restore its path")
        lifecycle._validate_data_root(self.data_root)
        return lifecycle.Setup(
            project=Path(__file__).resolve().parent,
            workspace=self.workspace,
            data_root=self.data_root,
            config=self.config,
            server=self.server,
            real_server=self.real_server,
        )

    def document(self) -> dict[str, object]:
        return {
            "version": 2,
            "kind": "native-files",
            "workspace": str(self.workspace),
            "data_root": str(self.data_root),
            "config": str(self.config),
            "config_target": str(self.config_target),
            "server": self.server,
            "real_server": self.real_server,
        }


def load(path: Path) -> SavedTarget | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SETTINGS:
            raise RuntimeError(
                "RexDeck settings must be a bounded regular non-symlink file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_SETTINGS + 1)
        if len(data) > _MAX_SETTINGS:
            raise RuntimeError("RexDeck settings exceed the size limit")
    finally:
        os.close(descriptor)
    try:
        value: object = json.loads(data, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"Invalid RexDeck settings JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("RexDeck settings must be a JSON object")
    fields = cast(dict[str, object], value)
    if (
        set(fields) != _FIELDS
        or type(fields["version"]) is not int
        or fields["version"] != 2
        or fields["kind"] != "native-files"
        or type(fields["real_server"]) is not bool
    ):
        raise RuntimeError("Unsupported or malformed RexDeck settings schema")
    server = fields["server"]
    if not isinstance(server, str) or not server or server != server.strip():
        raise RuntimeError("Saved server must be a nonempty Unix endpoint")
    lifecycle._socket_path(server)
    target = SavedTarget(
        workspace=_absolute(fields["workspace"], "workspace"),
        data_root=_absolute(fields["data_root"], "data_root"),
        config=_absolute(fields["config"], "config"),
        config_target=_absolute(fields["config_target"], "config_target"),
        server=server,
        real_server=fields["real_server"],
    )
    if target.config_target.resolve() != target.config_target:
        raise RuntimeError("Saved config_target must be a resolved absolute path")
    target.setup()
    return target


def save(path: Path, target: SavedTarget) -> None:
    data = (json.dumps(target.document(), ensure_ascii=True, indent=2) + "\n").encode()
    if len(data) > _MAX_SETTINGS:
        raise RuntimeError("Approved RexDeck settings exceed the size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Publish a complete file without ever replacing a concurrently saved target.
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

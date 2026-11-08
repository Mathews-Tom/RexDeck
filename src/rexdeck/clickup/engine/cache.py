"""Derived SQLite cache — rebuildable and never authoritative.

The local registry is the source of truth. This store only holds what one
sync pass derived from it, so a schema that no longer matches is discarded and
rebuilt rather than migrated.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rexdeck.settings import xdg_path

from .receipts import digest_json


def cache_path(
    *,
    account_id: str | None = None,
    team_id: str | None = None,
) -> Path:
    """Resolve the XDG cache at call time, optionally scoped by identity/workspace."""
    root = xdg_path("XDG_CACHE_HOME", Path.home() / ".cache")
    base = root / "rexdeck" / "clickup"
    if account_id is not None or team_id is not None:
        base /= digest_json({"account": account_id, "team": team_id})[:24]
    return base / "cache.sqlite"


def open_cache(
    *,
    schema_version: int,
    schema: Sequence[str],
    path: Path | None = None,
    account_id: str | None = None,
    team_id: str | None = None,
) -> sqlite3.Connection:
    """Open the derived ClickUp cache, rebuilding it when the schema has moved."""
    # 0 is SQLite's default for `PRAGMA user_version` on a fresh file, so a
    # caller starting at 0 matches an empty database and skips the replay
    # entirely — every verb then gets a connection with no tables and the first
    # query fails as `no such table`, reading as corruption rather than
    # misconfiguration. Reserve 0 and require a real version.
    if schema_version < 1:
        raise ValueError(f"schema_version must be 1 or greater, got {schema_version}")
    if not schema:
        raise ValueError("no schema statements supplied for the ClickUp cache")

    if path is None:
        path = cache_path(account_id=account_id, team_id=team_id)
        root = xdg_path("XDG_CACHE_HOME", Path.home() / ".cache") / "rexdeck"
        _private_directory(root)
        _private_directory(root / "clickup")
    _private_directory(path.parent)
    connection = _connect(path)
    if _schema_version(connection) == schema_version:
        return connection

    # Discard, do not migrate. Dropping objects one type at a time leaves behind
    # whatever the drop did not name — a view survives DROP TABLE — and the
    # replay then fails before `user_version` advances, so every later open
    # re-enters the rebuild and fails again, permanently. Deleting the file
    # cannot miss an object type.
    connection.close()
    path.unlink(missing_ok=True)

    connection = _connect(path)
    for statement in schema:
        connection.execute(statement)
    # PRAGMA takes no bound parameters; schema_version is a validated int.
    connection.execute(f"PRAGMA user_version = {schema_version:d}")
    connection.commit()
    return connection


def _private_directory(path: Path) -> None:
    """Create missing parents privately and restrict the selected cache directory."""
    if not path.parent.exists():
        _private_directory(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _connect(path: Path) -> sqlite3.Connection:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])

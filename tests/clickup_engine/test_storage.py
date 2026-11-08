"""Explicit synthetic registry setup and disposable XDG cache behavior."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

import pytest

from rexdeck.clickup.engine import cache, registry
from rexdeck.clickup.engine.diagnostics import EngineError


class Selection(TypedDict):
    account_id: str
    team_id: str
    space_id: str
    space_name: str
    list_id: str
    list_name: str


REGISTRY_LOCATION = registry.registry_path
SELECTION: Selection = {
    "account_id": "710000001",
    "team_id": "7000000001",
    "space_id": "700000000001",
    "space_name": "Sample Space",
    "list_id": "70000009999",
    "list_name": "Project Gamma",
}


def test_seed_construction_performs_no_storage_write(tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))
    loaded = registry.seed_registry(**SELECTION)
    assert set(tmp_path.rglob("*")) == before
    assert loaded.synced_at is None
    assert loaded.digest is None
    assert loaded.seeds.me_id == SELECTION["account_id"]
    assert loaded.topology.lists == ()
    with pytest.raises(EngineError) as caught:
        registry.check_fresh(loaded)
    assert caught.value.diagnostic.code == "registry/unsynced"


def test_explicit_bootstrap_is_private_unsynced_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "selected" / "registry.yaml"
    registry.bootstrap(**SELECTION, path=path)
    before = path.read_bytes()
    assert registry.load(path).synced_at is None
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"CLICKUP_API_TOKEN" not in before
    assert b"synthetic-test-token" not in before

    with pytest.raises(FileExistsError):
        registry.bootstrap(**SELECTION, path=path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ("account_id", "team_id", "space_id", "list_id"))
@pytest.mark.parametrize("value", ("", "../outside", "not-an-id", "１２３"))
def test_seed_rejects_non_ascii_numeric_identifiers(name: str, value: str) -> None:
    selection = {key: str(item) for key, item in SELECTION.items()}
    selection[name] = value
    with pytest.raises(ValueError):
        registry.seed_registry(**selection)


def test_registry_and_cache_paths_stay_in_xdg_and_separate_accounts_and_teams() -> None:
    config = Path(os.environ["XDG_CONFIG_HOME"])
    cached = Path(os.environ["XDG_CACHE_HOME"])
    registry_paths = {
        REGISTRY_LOCATION(account_id=account, team_id=team)
        for account, team in (
            ("710000001", "7000000001"),
            ("710000002", "7000000001"),
            ("710000001", "7000000002"),
        )
    }
    cache_paths = {
        cache.cache_path(account_id=account, team_id=team)
        for account, team in (
            ("710000001", "7000000001"),
            ("710000002", "7000000001"),
            ("710000001", "7000000002"),
        )
    }
    assert len(registry_paths) == len(cache_paths) == 3
    assert all(path.is_relative_to(config / "rexdeck") for path in registry_paths)
    assert all(path.is_relative_to(cached / "rexdeck") for path in cache_paths)
    assert all(not path.exists() for path in registry_paths | cache_paths)


def test_cache_schema_change_rebuilds_the_disposable_database(tmp_path: Path) -> None:
    path = tmp_path / "derived.sqlite"
    connection = cache.open_cache(
        schema_version=1,
        schema=("CREATE TABLE old_rows (id TEXT)",),
        path=path,
    )
    connection.execute("INSERT INTO old_rows VALUES ('synthetic-row')")
    connection.execute("CREATE VIEW old_view AS SELECT * FROM old_rows")
    connection.commit()
    connection.close()

    connection = cache.open_cache(
        schema_version=2,
        schema=("CREATE TABLE new_rows (id TEXT)",),
        path=path,
    )
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert names == {"new_rows"}
    finally:
        connection.close()


@pytest.mark.parametrize("value", ("", "relative", "../outside"))
def test_cache_refuses_empty_or_relative_xdg_home(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", value)
    with pytest.raises(RuntimeError, match="XDG_CACHE_HOME"):
        cache.cache_path()
    with pytest.raises(RuntimeError, match="XDG_CACHE_HOME"):
        cache.open_cache(schema_version=1, schema=("CREATE TABLE rows (id TEXT)",))


def test_cache_creates_private_directories_and_database() -> None:
    root = Path(os.environ["XDG_CACHE_HOME"])
    path = cache.cache_path(account_id="710000001", team_id="7000000001")
    previous = os.umask(0)
    try:
        connection = cache.open_cache(
            schema_version=1,
            schema=("CREATE TABLE rows (id TEXT)",),
            account_id="710000001",
            team_id="7000000001",
        )
        connection.close()
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o600
    directory = path.parent
    while directory != root.parent:
        assert directory.stat().st_mode & 0o777 == 0o700
        directory = directory.parent

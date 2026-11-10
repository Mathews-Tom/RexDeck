from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from rexdeck import integrations
from rexdeck.host import launch

SQL = integrations.get("sql")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        (
            "harlequin, version 2.17.0\n\nInstalled Adapters:\n  - sqlite, version 2.17.0",
            "2.17.0",
        ),
        ("harlequin, version 2.17.0", "2.17.0"),
        ("harlequin version 2.17.0", None),
        ("  - sqlite, version 2.17.0", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(SQL.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_sql_opens_one_sqlite_engine_read_only() -> None:
    assert SQL.args == ("--adapter", "sqlite", "--read-only")
    assert SQL.selected_file == ".sqlite,.sqlite3,.db"


@pytest.mark.parametrize("name", ["app.sqlite", "app.SQLITE3", "app.db"])
def test_existing_sqlite_files_are_accepted(tmp_path: Path, name: str) -> None:
    database = tmp_path / name
    sqlite3.connect(database).close()
    assert launch.resolve(
        f"selected-file:{SQL.selected_file}", str(tmp_path), name
    ) == str(database.resolve())


@pytest.mark.parametrize("name", ["missing.db", "data.duckdb", "."])
def test_missing_duckdb_and_directories_are_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / "data.duckdb").write_bytes(b"")
    with pytest.raises(ValueError):
        launch.resolve(f"selected-file:{SQL.selected_file}", str(tmp_path), name)
    assert not (tmp_path / "missing.db").exists()

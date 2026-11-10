"""SQL: unmodified Harlequin on one explicitly selected SQLite file, read-only."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="sql",
    title="SQL",
    tool="harlequin",
    tool_label="Harlequin",
    description="Query an explicitly selected SQLite database read-only in Harlequin",
    keywords="sql sqlite database query harlequin db",
    context="selected-file",
    versions=("2.17.0",),
    version_pattern=r"^harlequin, version (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/tconbeer/harlequin/releases/tag/v2.17.0",
    license="MIT",
    capabilities=(
        "opens only the selected SQLite file read-only (--adapter sqlite "
        "--read-only): writes to it fail, but SQL you type can ATTACH and create "
        "or write other local SQLite files and VACUUM INTO a copy anywhere",
        "runs the sqlite adapter's init script (~/.sqliterc) at connect unless a "
        "Harlequin profile sets no_init",
        "reads config from harlequin.toml, .harlequin.toml and pyproject.toml "
        "[tool.harlequin] in the file's directory, then ~/Library/Application "
        "Support/harlequin and your home directory; config can set adapter "
        "extensions and init scripts",
        "stores query history in history.db under its state directory "
        "($XDG_STATE_HOME/harlequin, else ~/Library/Application Support/harlequin) "
        "and editor buffers and catalog cache under ~/Library/Caches/harlequin",
        "exports query results to files you choose and copies to the clipboard",
    ),
    default_layout=Layout("right", 50),
    args=("--adapter", "sqlite", "--read-only"),
    file_suffixes=(".sqlite", ".sqlite3", ".db"),
)

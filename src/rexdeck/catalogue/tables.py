"""Tables: unmodified VisiData on an explicitly selected local table."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="tables",
    title="Tables",
    tool="vd",
    tool_label="VisiData",
    description="Explore an explicitly selected CSV, TSV, JSON or JSONL table in VisiData",
    keywords="tables visidata vd csv tsv json jsonl sort select frequency",
    context="selected-file",
    versions=("3.5",),
    version_pattern=r"^saul\.pw/VisiData v(\d+\.\d+(?:\.\d+)?)\s*$",
    release="https://github.com/saulpw/visidata/releases/tag/v3.5",
    license="GPL-3.0-only",
    capabilities=(
        "editable tables: sort, select and frequency tables; Ctrl+S and save-source "
        "can write or overwrite files; quitting does not automatically save",
        "Y and gY copy rows to the system clipboard via clipboard_copy_cmd "
        "(pbcopy by default on macOS)",
        "Python execution through = expression columns, exec-python and shell commands; "
        "executes config.py or ~/.visidatarc and installed plugins, not a sandbox; "
        "no automatic cwd .visidatarc loading in 3.5 unless explicitly configured",
        "network-capable loaders and connectors remain available; this action selects "
        "local files only and disables the default startup MOTD request (--motd-url=)",
        "in-memory command log can be saved/replayed; input history persists in "
        "the VisiData data directory; config/plugins use the VisiData config directory "
        "(macOS ~/Library/Preferences/visidata, or XDG_CONFIG_HOME/visidata), "
        "with VD_CONFIG and VD_DIR overrides; data/cache use macOS Application Support "
        "and Caches/visidata or XDG_DATA_HOME/XDG_CACHE_HOME",
    ),
    default_layout=Layout("right", 50),
    args=("--motd-url=",),
    file_suffixes=(".csv", ".tsv", ".json", ".jsonl"),
    homebrew="visidata",
)

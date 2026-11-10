"""Disk: unmodified dua interactive in the invoking terminal's directory."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="disk",
    title="Disk",
    tool="dua",
    tool_label="dua",
    description="Explore directory disk usage with dua interactive",
    keywords="disk dua usage size directory cleanup",
    context="directory",
    versions=("3.0.1",),
    version_pattern=r"^dua (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/Byron/dua-cli/releases/tag/v3.0.1",
    license="MIT",
    capabilities=(
        "scans the invoking directory; navigation can scan parent directories; "
        "not a sandbox and qualified only on owned fixture trees",
        "interactive mode can mark and permanently delete files and directories "
        "or move them to Trash in builds with trash-move (default in 3.0.1)",
        "reads optional macOS ~/Library/Application Support/dua-cli/config.toml "
        "for settings and keybindings, which can remap destructive actions; "
        "O opens the selected path in an external application",
        "launches interactive only: no --once key replay or snapshot --export/--import; "
        "inherits upstream DUA_* environment settings",
    ),
    default_layout=Layout("right", 50),
    args=("interactive",),
    homebrew="dua-cli",
)

"""System: unmodified bottom (btm) system monitor, launched read-only."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="system",
    title="System",
    tool="btm",
    tool_label="bottom",
    description="Open the bottom system monitor read-only in a pane",
    keywords="system monitor cpu memory processes bottom btm top",
    # bottom is machine-wide; the invoking directory is only the pane cwd.
    context="directory",
    versions=("0.15.0",),
    version_pattern=r"^bottom (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/ClementTsang/bottom/releases/tag/0.15.0",
    license="MIT",
    capabilities=(
        "reads machine-wide CPU, memory, disk, network, temperature, battery and "
        "process data; process names, and the full command lines of your own "
        "processes when the command toggle is on, which can contain secrets",
        "launched with --read_only: stopping or signalling processes is disabled; "
        "keyboard and mouse navigation, sorting, search and quit stay available",
        "reads its config (~/.config/bottom/bottom.toml when that file exists, "
        "otherwise $XDG_CONFIG_HOME/bottom/bottom.toml when set, otherwise "
        "~/Library/Application Support/bottom/bottom.toml on macOS) and writes a "
        "default one on first run if none exists; no network access",
    ),
    default_layout=Layout("right", 50),
    args=("--read_only",),
    homebrew="bottom",
)

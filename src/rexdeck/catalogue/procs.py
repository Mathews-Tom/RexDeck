"""Processes: upstream dekit on an explicitly selected trusted project config."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="procs",
    title="Processes",
    tool="dekit",
    tool_label="dekit",
    description="Manage tasks from an explicitly selected trusted dekit.yaml",
    keywords="processes procs dekit tasks services runner",
    context="dekit-config",
    versions=("0.10.3",),
    version_pattern=r"^dekit (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/pvolok/dekit/releases/tag/v0.10.3",
    license="MIT",
    capabilities=(
        "trusted config only: tasks, JavaScript, hooks and console commands can "
        "execute arbitrary programs with your permissions; no sandbox",
        "starts or attaches to the selected project's independent dekit runner; "
        "tasks and the runner may outlive this pane; quit-time task states and "
        "the exact project-scoped down command require acknowledgement",
        "start, attach, restart and stop tasks; upstream console edits and "
        "write-back can change tasks and dekit.yaml; tasks may access files/network",
        "uses upstream XDG_CONFIG_HOME/dekit (default ~/.config/dekit), "
        "XDG_DATA_HOME/dekit (default ~/.local/share/dekit), and "
        "XDG_RUNTIME_DIR/dekit (default temporary-directory/dekit-UID); "
        "no RexDeck daemon or redirected config home",
    ),
    default_layout=Layout("right", 60),
)

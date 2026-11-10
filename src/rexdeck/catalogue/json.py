"""JSON: unmodified jless on one explicitly selected .json file."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="json",
    title="JSON",
    tool="jless",
    tool_label="jless",
    description="Open an explicitly selected JSON file in the jless viewer",
    keywords="json jless viewer pager tree",
    context="selected-file",
    versions=("0.9.0",),
    version_pattern=r"^jless (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/PaulJuliusMartinez/jless/releases/tag/v0.9.0",
    license="MIT",
    capabilities=(
        "read-only viewer: never writes the selected file; parses it as JSON "
        "(--json) and holds the whole document in memory",
        "copies values, keys and paths to the system clipboard on y commands",
        "no network access, configuration file or plugins; F1 or :help opens the "
        "key list in less from the pane PATH, whose ! and | commands run shell "
        "commands and s saves to a file",
    ),
    default_layout=Layout("right", 50),
    args=("--json",),
    file_suffixes=(".json",),
)

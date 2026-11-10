"""Files: unmodified Yazi in the invoking terminal directory."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="files",
    title="Files",
    tool="yazi",
    tool_label="Yazi",
    description="Open yazi in the invoking terminal directory",
    keywords="files yazi explorer",
    context="directory",
    versions=("26.9.1",),
    version_pattern=(
        r"^Yazi(?:[ \t]+|[ \t]*\r?\n[ \t]*Version:[ \t]*)"
        r"(\d+\.\d+\.\d+)(?:\s|$)"
    ),
    release="https://github.com/sxyazi/yazi/releases/tag/v26.9.1",
    license="MIT",
    capabilities=(
        "filesystem read/write: create, rename, move, copy, delete, open",
        "runs configured openers, previewers and shell commands",
        "launch directory is a start point, not a root jail",
    ),
    default_layout=Layout("right", 50),
)

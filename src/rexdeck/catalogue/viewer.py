"""Viewer: Yazi previews of an explicitly selected PDF, JPG, PNG or SVG."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="viewer",
    title="Viewer",
    tool="yazi",
    tool_label="Yazi",
    description="Preview an explicitly selected PDF, JPG, PNG or SVG in Yazi",
    keywords="viewer pdf jpg jpeg png svg image",
    context="media",
    versions=("26.9.1",),
    version_pattern=(
        r"^Yazi(?:[ \t]+|[ \t]*\r?\n[ \t]*Version:[ \t]*)"
        r"(\d+\.\d+\.\d+)(?:\s|$)"
    ),
    release="https://github.com/sxyazi/yazi/releases/tag/v26.9.1",
    license="MIT",
    capabilities=(
        "in-pane Kitty graphics previews; PDF pages use pdftoppm; SVG uses "
        "resvg, not a browser; unbundled helper prerequisites: "
        "pdftoppm/Poppler (GPL), resvg (MIT/Apache-2.0)",
        "filesystem read/write and configured openers, editors and shell "
        "commands: the same unrestricted Yazi as Files",
        "selected file is a start point, not a root jail or sandbox",
        "opens the parent folder and type-checks/preloads visible siblings "
        "using file(1), pdftoppm, resvg and ImageMagick; use trusted or "
        "isolated folders for untrusted files",
        "Enter/open follows the owner's openers (macOS default application), "
        "which may fetch resources and run SVG scripts; RexDeck never invokes "
        "an external opener automatically",
    ),
    default_layout=Layout("right", 50),
    prerequisites=(
        ("pdftoppm", "https://poppler.freedesktop.org/"),
        ("resvg", "https://github.com/linebender/resvg/releases/tag/v0.49.0"),
    ),
)

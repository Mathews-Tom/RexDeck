"""Markdown: markdown-reader on a selected file or directory, with Mermaid."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="markdown",
    title="Markdown",
    tool="markdown-reader",
    tool_label="markdown-reader",
    description=(
        "Open markdown-reader on a selected Markdown file or the invoking "
        "directory, with Mermaid diagrams rendered in the pane"
    ),
    keywords="markdown md mermaid docs readme reader",
    context="document",
    versions=("1.35.4",),
    version_pattern=r"^markdown-reader (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/leboiko/markdown-reader/releases/tag/v1.35.4",
    license="MIT",
    capabilities=(
        "file writes: hybrid (i) and fullscreen (I) edit modes save Markdown "
        "in place with :w",
        "reads the browsed directory tree; stores its own config, session "
        "and cache under the user's Library",
        "runs `git status` (PATH git, inherited GIT_* environment and "
        "repository config honoured) in the browsed root at launch and on file "
        "changes to colour the tree; this may refresh the Git index",
        "network: checks crates.io for a newer version at most daily unless "
        "[updates] check_for_updates = false; --check-external is not used",
        "renders Mermaid in-process; in Rex panes it selects Unicode text "
        "diagrams, with no browser or hosted renderer",
    ),
    default_layout=Layout("right", 50),
)

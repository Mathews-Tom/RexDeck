"""Actions: the packaged read-only workflow run and job log view."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="actions",
    title="Actions",
    tool="rexdeck-actions",
    tool_label="Actions view",
    description="Read workflow runs, jobs and log tails for a branch or commit",
    keywords="github actions workflow runs jobs logs branch commit",
    context="github-worktree",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "read-only Actions API requests using existing gh authentication",
        "requires verified account=LOGIN; never switches accounts or copies credentials",
        "job logs may contain secrets; only the last 200 lines are displayed, in memory",
        "explicit validated run browser link; never opens automatically",
        "explicit refresh only; branch/commit filters and run pagination",
    ),
    default_layout=Layout("right", 50),
    args=("-I", "-m", "rexdeck.actions"),
    owned_module="rexdeck.actions",
    prerequisites=(
        ("git", "https://git-scm.com/downloads"),
        ("gh", "https://cli.github.com/"),
    ),
)

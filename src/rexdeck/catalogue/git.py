"""Git: unmodified lazygit in the invoking Git worktree."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="git",
    title="Git",
    tool="lazygit",
    tool_label="lazygit",
    description="Open lazygit in the invoking terminal directory",
    keywords="git lazygit",
    context="git-worktree",
    versions=("0.66.2", "0.66.3"),
    version_pattern=r"(?:^|,\s*)version=(\d+\.\d+\.\d+)(?=,|\s*$)",
    release="https://github.com/jesseduffield/lazygit/releases/tag/v0.66.3",
    license="MIT",
    capabilities=(
        "repository writes: stage, commit, branch, rebase, discard, stash",
        "network when the user fetches, pulls or pushes with existing Git auth",
        "runs configured editors, pagers and custom commands",
    ),
    default_layout=Layout("right", 50),
    prerequisites=(("git", "https://git-scm.com/downloads"),),
)

"""GitHub: gh-dash with an explicit repository and verified gh account."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="github",
    title="GitHub",
    tool="gh-dash",
    tool_label="gh-dash",
    description="Open GitHub PRs and issues with explicit repository and account",
    keywords="github gh dashboard pull requests issues",
    context="github-worktree",
    versions=("4.26.2",),
    version_pattern=r"^module version: v(\d+\.\d+\.\d+), checksum:",
    release="https://github.com/dlvhdr/gh-dash/releases/tag/v4.26.2",
    license="MIT",
    capabilities=(
        "authenticated GitHub network reads and writes: comments, reviews, "
        "merge, close, assignments and other upstream actions",
        "requires account=LOGIN matching existing gh authentication; never "
        "switches global accounts or copies credentials",
        "launch-scoped repository sections replace custom sections; normal "
        "global/repository theme, keybindings and custom commands are retained",
        "runs gh, configured editors, pagers, browser and custom commands; "
        "upstream may initialize its normal config/cache",
        "initial repository scope is not a sandbox: upstream navigation "
        "and editable filters may deliberately leave it",
    ),
    default_layout=Layout("right", 50),
    prerequisites=(
        ("git", "https://git-scm.com/downloads"),
        ("gh", "https://cli.github.com/"),
    ),
)

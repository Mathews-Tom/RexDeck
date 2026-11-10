"""Inbox: account-scoped, explicitly refreshed GitHub notifications."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="inbox",
    title="Inbox",
    tool="rexdeck-inbox",
    tool_label="Inbox view",
    description="Read unread GitHub review requests, mentions and CI notifications",
    keywords="github inbox notifications review mention ci account",
    context="github-account",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "read-only GitHub notifications using existing gh authentication; GET only",
        "explicit account=LOGIN verified before launch and on every refresh; no worktree required",
        "unread notifications, pagination, participating toggle and current-page reason filters",
        "explicit refresh only; no mark-read, done, subscribe or background polling",
        "personal notification titles and repository names stay in memory",
        "validated github.com thread links; null CheckSuite links explicitly open the repository Actions page",
    ),
    default_layout=Layout("right", 50),
    args=("-I", "-m", "rexdeck.inbox"),
    owned_module="rexdeck.inbox",
    prerequisites=(("gh", "https://cli.github.com/"),),
)

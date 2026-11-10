"""ClickUp: the packaged RexDeck task desk for an explicit list."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="clickup",
    title="ClickUp",
    tool="rexdeck-clickup",
    tool_label="ClickUp engine and pane",
    description="Inspect tasks and deliberately confirm changes in an explicit ClickUp selection",
    keywords="clickup tasks status comments confirmed changes",
    context="directory",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "owned MIT engine/frontend; confirmed status/assignee/due/description/points/field/comment writes",
        "explicit account/workspace/list; credential environment-name reference",
        "all task pages, native statuses, complete comments/replies and engine projections",
        "explicit validated browser deep link; never opens automatically",
    ),
    default_layout=Layout("right", 50),
    args=("-I", "-m", "rexdeck.clickup"),
    owned_module="rexdeck.clickup",
)

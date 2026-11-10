"""Dashboard: unmodified Dashbrew on one explicitly selected trusted JSON board."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="dashboard",
    title="Dashboard",
    tool="dashbrew",
    tool_label="dashbrew",
    description="Open an explicitly selected trusted JSON board in Dashbrew",
    keywords="dashboard dashbrew board scripts api json",
    context="selected-file",
    versions=("1.2.0",),
    version_pattern=(
        r"^[ \t]*mod[ \t]+github\.com/rasjonell/dashbrew[ \t]+"
        r"v(\d+\.\d+\.\d+)[ \t]+h1:\S+[ \t]*$"
    ),
    release="https://github.com/rasjonell/dashbrew/releases/tag/v1.2.0",
    license="MIT",
    capabilities=(
        "trusted boards only: script sources and configured action bindings run "
        "sh -c with the pane's permissions; scripts have no execution timeout",
        "API sources send HTTP GET with a 5-second timeout, no configured auth "
        "headers or cookie jar; URLs, redirects and scripts can access the network",
        "explicit -c selected JSON only: never auto-loads cwd dashboard.json; "
        "reads custom themes from DASHBREW_THEMES_DIR and XDG/home config",
        "todo components rewrite their configured source files; scripts and "
        "actions can write arbitrary files; the board JSON itself is not saved",
    ),
    default_layout=Layout("right", 50),
    args=("-c",),
    prerequisites=(("go", "https://go.dev/dl/"),),
    version_probe=("go", "version", "-m", "{tool}"),
    file_suffixes=(".json",),
)

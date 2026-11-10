"""Usage: the upstream tokscale TUI for local omp sessions."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="usage",
    title="Usage",
    tool="tokscale",
    tool_label="tokscale",
    description=(
        "Open the tokscale usage TUI for local omp sessions with upstream "
        "online pricing and refresh"
    ),
    keywords="usage tokens cost tokscale omp models",
    # Usage is machine-wide; the invoking directory is only the pane cwd.
    context="directory",
    versions=("4.21.0",),
    version_pattern=r"^tokscale (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/junhoyeo/tokscale/releases/tag/v4.21.0",
    license="MIT",
    capabilities=(
        "reads local AI-client session logs machine-wide (omp sessions under "
        "~/.omp) and caches derived usage; launching in a project is not a "
        "project filter",
        "network: first data load downloads model pricing when its 1-hour "
        "cache expired; when signed in to Tokscale it fetches account stats; "
        "the Usage tab (on open and each refresh) queries provider quotas "
        "with existing provider credentials regardless of --client",
        "writes its own settings and caches under ~/.config/tokscale (Shift+R "
        "persists the auto-refresh choice; the Usage tab can import and "
        "later refresh the current Codex login in codex-credentials.json)",
        "runs helpers: macOS security keychain reads, grok agent when Grok "
        "credentials exist, codex login from its UI",
        "its UI exposes account controls (provider login, account switch, "
        "removal, Codex reset-credit use); RexDeck never invokes login, "
        "submit, autosubmit or report",
    ),
    default_layout=Layout("right", 50),
    args=("tui", "--client", "omp"),
)

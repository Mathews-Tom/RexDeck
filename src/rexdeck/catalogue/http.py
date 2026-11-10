"""HTTP: upstream Slumber using the invoking directory's collection discovery."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="http",
    title="HTTP",
    tool="slumber",
    tool_label="Slumber",
    description="Send HTTP requests and inspect responses with Slumber",
    keywords="http api requests responses slumber",
    context="directory",
    versions=("5.4.4",),
    version_pattern=r"^slumber (\d+\.\d+\.\d+)[ \t]*\r?$",
    release="https://github.com/LucasPickering/slumber/releases/tag/v5.4.4",
    license="MIT",
    capabilities=(
        "network: sends collection-defined HTTP requests, including writes and "
        "chained requests; follows redirects by default",
        "reads slumber.yml, slumber.yaml, .slumber.yml or .slumber.yaml in the "
        "invoking directory or its parents, and files referenced by $ref; "
        "collection discovery is not a sandbox",
        "templates read files and environment variables and execute command(...) "
        "subprocesses, including previews before requests are sent; templates "
        "also prompt for values; configured editors, pagers and response "
        "query/export commands can also execute programs",
        "writes request/response history, UI state and per-collection query/export "
        "command history to a local SQLite database "
        "(macOS default ~/Library/Application Support/slumber/state.sqlite); "
        "sensitive preview masking does not prevent history persistence",
        "reads global config (macOS default ~/Library/Application Support/"
        "slumber/config.yml; SLUMBER_CONFIG_PATH can override it), creates its "
        "config directory and writes temporary session logs; UI editing/export "
        "can write files",
        "secrets belong in env/command/prompt templates, not launch argv; "
        "RexDeck passes no arguments and enables no remote-control feature",
    ),
    default_layout=Layout("right", 50),
    enable_notice=(
        "HTTP needs a Slumber collection: open it from a directory that contains "
        "slumber.yml, or from one of its subdirectories."
    ),
)

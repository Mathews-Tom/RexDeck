"""Dependencies: explicit coordinates and public package metadata."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="deps",
    title="Dependencies",
    tool="rexdeck-deps",
    tool_label="Dependencies view",
    description="Browse package dependencies, licenses and known advisories",
    keywords="dependencies package licenses vulnerabilities advisories deps.dev osv",
    context="directory",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "package coordinates leave the machine: sent to deps.dev and OSV",
        "public metadata only; no repository/SBOM upload, install or lockfile repair",
        "no advisories means none known to these sources at fetch time, not a safety verdict",
        "explicit refresh only; no background polling; in-memory snapshots and freshness",
        "licenses; npm/Cargo/Maven/PyPI dependency graphs; package-version advisory IDs and OSV details",
        "explicit validated deps.dev version link; never opens automatically",
    ),
    default_layout=Layout("right", 60),
    args=("-I", "-m", "rexdeck.deps"),
    owned_module="rexdeck.deps",
)

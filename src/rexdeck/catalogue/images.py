"""Images: anonymous public registry tag and manifest metadata."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="images",
    title="Images",
    tool="rexdeck-images",
    tool_label="Images view",
    description="Browse public OCI image tags, digests and platforms",
    keywords="images oci docker registry tags manifest platforms",
    context="directory",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "image references leave the machine: sent to public registries and anonymous token endpoints",
        "metadata only: no blobs, layers, run, push or delete",
        "not a vulnerability or signature verdict; private registries are unsupported",
        "anonymous pull tokens held in memory only, never displayed or logged",
        "explicit refresh; no background polling; source and fetched-at freshness",
        "validated Docker Hub page only; other web pages refused when not derivable",
    ),
    default_layout=Layout("right", 60),
    args=("-I", "-m", "rexdeck.images"),
    owned_module="rexdeck.images",
)

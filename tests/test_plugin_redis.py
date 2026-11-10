from __future__ import annotations

import re

import pytest

from rexdeck.catalogue.redis import INTEGRATION
from rexdeck.integrations import Layout, get

REAL_VERSION = (
    "redis-tui 1.0.48 (commit: a45dc5eb661d34010767e6be1483811a2719041d, "
    "built: 2026-11-06T01:27:34Z)"
)


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_real_version_output(ending: str) -> None:
    match = re.search(INTEGRATION.version_pattern, REAL_VERSION + ending, re.MULTILINE)
    assert match is not None
    assert match.group(1) == "1.0.48"
    assert match.group(1) in INTEGRATION.versions


@pytest.mark.parametrize(
    "output",
    [
        "1.0.48",
        "redis-tui 1.0.48",
        REAL_VERSION.replace("redis-tui", "not-redis-tui"),
        REAL_VERSION.replace("1.0.48", "1.0.48-rc1"),
        REAL_VERSION.replace("1.0.48", "1.0.480"),
        REAL_VERSION.replace("1.0.48", "1.0.47"),
        REAL_VERSION + " unexpected",
        REAL_VERSION.replace(")", ""),
        REAL_VERSION.replace(", built:", "\nbuilt:"),
    ],
)
def test_unqualified_or_malformed_version_output(output: str) -> None:
    match = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
    assert match is None or match.group(1) not in INTEGRATION.versions


def test_redis_descriptor_keeps_connection_selection_upstream() -> None:
    assert get("redis") is INTEGRATION
    assert INTEGRATION.title == "Redis"
    assert INTEGRATION.tool == "redis-tui"
    assert INTEGRATION.context == "directory"
    assert INTEGRATION.versions == ("1.0.48",)
    assert INTEGRATION.license == "MIT"
    assert INTEGRATION.default_layout == Layout("right", 50)
    # No quick-connect, credentials, TLS bypass, update, or inferred local server.
    assert INTEGRATION.args == ()
    assert INTEGRATION.prerequisites == ()
    assert INTEGRATION.owned_module is None

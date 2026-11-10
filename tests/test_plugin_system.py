from __future__ import annotations

import re

import pytest

from rexdeck import dependencies, integrations

SYSTEM = integrations.get("system")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("bottom 0.15.0\n", "0.15.0"),
        ("bottom 0.15.0", "0.15.0"),
        ("btm 0.15.0\n", None),
        ("bottom 0.15\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(SYSTEM.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_system_launches_read_only_and_never_disables_quit() -> None:
    assert SYSTEM.tool == "btm"
    assert SYSTEM.context == "directory"
    assert SYSTEM.args == ("--read_only",)
    assert "--disable_keys" not in SYSTEM.args


def test_install_guidance_names_the_bottom_formula() -> None:
    guidance = dependencies._manual(SYSTEM)
    assert "brew info bottom; run brew install bottom only" in guidance
    assert "btm --version" in guidance

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from rexdeck import dependencies, integrations, lifecycle

KATA = integrations.get("kata")

# First lines of `kata --version` as kata 0.23.0 prints them (Homebrew build).
REPORT = (
    "kata v0.23.0\n"
    "  commit:  unknown\n"
    "  built:   2026-11-09T11:49:11Z\n"
    "  go:      go1.27.3\n"
    "  os/arch: darwin/arm64\n"
)


@pytest.mark.parametrize(
    ("output", "version"),
    [
        (REPORT, "0.23.0"),
        ("kata v0.23.0", "0.23.0"),
        ("kata 0.23.0\n", None),
        ("kata v0.23\n", None),
        ("kata v0.23.0-rc1\n", None),
        ("kata v0.23.0.1\n", None),
        ("kata-cli v0.23.0\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(KATA.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_kata_launches_its_tui_on_the_invoking_directory() -> None:
    assert KATA.tool == "kata"
    assert KATA.context == "directory"
    assert KATA.args == ("tui",)
    assert KATA.owned_module is None
    assert KATA.versions == ("0.23.0",)


def _probe_with(report: str) -> str:
    result = lifecycle._Result(0, report.encode())
    with patch("rexdeck.lifecycle._run", return_value=result):
        return dependencies._probe("/opt/homebrew/bin/kata", KATA)


def test_qualified_report_is_accepted() -> None:
    assert _probe_with(REPORT) == "0.23.0"


def test_unpinned_kata_is_refused_before_registration() -> None:
    with pytest.raises(RuntimeError, match=r"Unqualified kata version 0\.22\.0"):
        _probe_with(REPORT.replace("v0.23.0", "v0.22.0"))


def test_missing_kata_gives_manual_install_guidance() -> None:
    with (
        patch("rexdeck.dependencies._resolve", return_value=None),
        pytest.raises(RuntimeError) as failure,
    ):
        dependencies.ensure("kata")
    message = str(failure.value)
    assert "kata --version" in message
    assert "rexdeck enable kata" in message

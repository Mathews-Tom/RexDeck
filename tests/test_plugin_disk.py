from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck import dependencies, integrations

DISK = integrations.get("disk")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("dua 3.0.1\n", "3.0.1"),
        ("dua 3.0.1", "3.0.1"),
        ("dua 3.0.0\n", "3.0.0"),
        ("dua-cli 3.0.1\n", None),
        ("dua 3.0\n", None),
        ("dua 3.0.1-beta\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(DISK.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_disk_uses_directory_and_only_documented_interactive_subcommand() -> None:
    assert DISK.context == "directory"
    assert DISK.args == ("interactive",)
    assert DISK.versions == ("3.0.1",)
    assert DISK.tool == "dua"
    assert DISK.homebrew == "dua-cli"
    assert DISK.default_layout.document() == {"placement": "right", "size": 50}
    assert DISK.describe()["capabilities"] == list(DISK.capabilities)
    disclosure = " ".join(DISK.capabilities)
    for text in (
        "permanently delete",
        "Trash",
        "config.toml",
        "--once",
        "--export/--import",
        "O opens the selected path in an external application",
        "navigation can scan parent directories",
    ):
        assert text in disclosure


def test_manual_guidance_uses_formula_not_executable_and_qualifies_brew() -> None:
    guidance = dependencies._manual(DISK)
    assert "brew info dua-cli" in guidance
    assert "brew install dua-cli only if it selects a qualified version" in guidance
    assert "brew install dua " not in guidance
    assert "dua 3.0.1" in guidance
    assert DISK.release in guidance
    assert "dua --version" in guidance
    assert "rexdeck enable disk" in guidance


def test_unqualified_brew_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "dua"
    tool.write_text("#!/bin/sh\nprintf 'dua 3.0.0\\n'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="Unqualified dua version 3.0.0"):
        dependencies.ensure("disk")


def test_missing_tool_gives_owner_managed_formula_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="Missing dua on PATH") as error:
        dependencies.ensure("disk")
    assert "brew info dua-cli" in str(error.value)
    assert "no integration was registered" in str(error.value)

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck import dependencies, integrations
from rexdeck.host import launch

TABLES = integrations.get("tables")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("saul.pw/VisiData v3.5\n", "3.5"),
        ("saul.pw/VisiData v3.6", "3.6"),
        ("saul.pw/VisiData v3.5.1", "3.5.1"),
        ("VisiData v3.5", None),
        ("saul.pw/VisiData v3.5dev", None),
        ("saul.pw/VisiData v3", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    match = re.search(TABLES.version_pattern, output, re.MULTILINE)
    assert (match.group(1) if match else None) == version


def test_tables_descriptor_and_network_default() -> None:
    assert TABLES.context == "selected-file"
    assert TABLES.tool == "vd"
    assert TABLES.versions == ("3.5",)
    assert TABLES.license == "GPL-3.0-only"
    assert TABLES.args == ("--motd-url=",)
    assert TABLES.default_layout.document() == {"placement": "right", "size": 50}
    assert TABLES.describe()["selected_file"] == {
        "suffixes": [".csv", ".tsv", ".json", ".jsonl"],
        "directories": False,
    }
    disclosures = " ".join(TABLES.capabilities)
    for term in (
        "Ctrl+S",
        "exec-python",
        ".visidatarc",
        "plugins",
        "network",
        "history",
        "clipboard_copy_cmd",
        "pbcopy",
    ):
        assert term in disclosures


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".json", ".jsonl", ".CSV"])
@pytest.mark.parametrize("representation", ["relative", "absolute", "uri"])
def test_tables_resolves_supported_selection(
    tmp_path: Path, suffix: str, representation: str
) -> None:
    target = tmp_path / f"table with spaces{suffix}"
    target.write_text("category,value\na,1\n", encoding="utf-8")
    selected = {
        "relative": target.name,
        "absolute": str(target),
        "uri": target.as_uri(),
    }[representation]
    resolved = launch.resolve(
        f"selected-file:{TABLES.selected_file}", str(tmp_path), selected
    )
    assert resolved == str(target.resolve())
    assert launch.workdir(resolved) == str(tmp_path.resolve())


@pytest.mark.parametrize(
    "selected", [None, "", "missing.csv", "notes.txt", "folder", "misleading.csv"]
)
def test_tables_refuses_invalid_selection(tmp_path: Path, selected: str | None) -> None:
    (tmp_path / "notes.txt").write_text("not a table", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "misleading.csv").symlink_to(tmp_path / "notes.txt")
    with pytest.raises(ValueError):
        launch.resolve(f"selected-file:{TABLES.selected_file}", str(tmp_path), selected)


def test_tables_guidance_names_formula_and_real_executable() -> None:
    guidance = dependencies._manual(TABLES)
    assert "brew info visidata" in guidance
    assert "brew install visidata" in guidance
    assert "Verify with vd --version" in guidance
    assert "rexdeck enable tables" in guidance
    assert "brew install vd" not in guidance

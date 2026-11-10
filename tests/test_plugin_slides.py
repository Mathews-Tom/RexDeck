from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck.catalogue.slides import INTEGRATION
from rexdeck.host.launch import resolve, workdir
from rexdeck.integrations import Layout


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("presenterm 0.16.1\n", "0.16.1"),
        ("presenterm 0.16.1", "0.16.1"),
        ("presenterm 0.16.2\n", "0.16.2"),
        ("presenterm-ng 0.16.1\n", None),
        ("presenterm 0.16\n", None),
        ("presenterm 0.16.1-beta\n", None),
        ("presenterm v0.16.1\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_slides_descriptor_does_not_enable_execution() -> None:
    assert INTEGRATION.context == "selected-file"
    assert INTEGRATION.tool == "presenterm"
    assert INTEGRATION.versions == ("0.16.1",)
    assert INTEGRATION.license == "BSD-2-Clause"
    assert INTEGRATION.default_layout == Layout("right", 50)
    assert INTEGRATION.describe()["selected_file"] == {
        "suffixes": [".md"],
        "directories": False,
    }
    forbidden = {
        "-x",
        "--enable-snippet-execution",
        "-X",
        "--enable-snippet-execution-replace",
        "--validate-snippets",
    }
    assert not forbidden.intersection(INTEGRATION.args)
    assert INTEGRATION.args == ()


@pytest.mark.parametrize("name", ["deck.md", "DECK.MD", "a deck.md"])
def test_slides_accepts_explicit_markdown(tmp_path: Path, name: str) -> None:
    deck = tmp_path / name
    deck.write_text("# Slides\n", encoding="utf-8")
    context = f"selected-file:{INTEGRATION.selected_file}"
    for selected in (name, str(deck), deck.as_uri()):
        target = resolve(context, str(tmp_path), selected)
        assert target == str(deck)
        assert workdir(target) == str(tmp_path)


@pytest.mark.parametrize(
    "selected", [None, "", "missing.md", "deck.txt", "deck.markdown", "."]
)
def test_slides_refuses_invalid_selection(tmp_path: Path, selected: str | None) -> None:
    (tmp_path / "deck.txt").write_text("# Not selected\n", encoding="utf-8")
    (tmp_path / "deck.markdown").write_text("# Wrong suffix\n", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve(f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), selected)


def test_slides_checks_symlink_target_suffix(tmp_path: Path) -> None:
    target = tmp_path / "not-markdown.txt"
    target.write_text("# Not a deck\n", encoding="utf-8")
    link = tmp_path / "pretend.md"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        resolve(f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), str(link))

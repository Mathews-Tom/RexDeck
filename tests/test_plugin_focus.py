from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck import integrations
from rexdeck.host.launch import resolve, workdir

FOCUS = integrations.get("focus")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("pomo version 1.2.1\n", "1.2.1"),
        ("pomo version 1.2.1", "1.2.1"),
        ("pomo version 1.2.2\n", "1.2.2"),
        ("pomo 1.2.1\n", None),
        ("pomo version v1.2.1\n", None),
        ("pomo version 1.2\n", None),
        ("pomo version 1.2.1-beta\n", None),
        ("not-pomo version 1.2.1\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(FOCUS.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_focus_starts_work_using_unmodified_directory_defaults() -> None:
    assert FOCUS.id == "focus"
    assert FOCUS.title == "Focus"
    assert FOCUS.tool == "pomo"
    assert FOCUS.versions == ("1.2.1",)
    assert FOCUS.license == "MIT"
    assert FOCUS.context == "directory"
    assert FOCUS.args == ()
    assert FOCUS.file_suffixes == ()
    assert not FOCUS.file_directories
    assert FOCUS.owned_module is None
    assert FOCUS.default_layout.document() == {"placement": "right", "size": 50}
    assert FOCUS.describe()["capabilities"] == list(FOCUS.capabilities)
    assert "selected_file" not in FOCUS.describe()


def test_focus_keeps_invoking_directory_and_its_config(tmp_path: Path) -> None:
    (tmp_path / "pomo.yaml").write_text("work:\n  duration: 10s\n")
    for raw in (str(tmp_path), tmp_path.as_uri()):
        target = resolve(FOCUS.context, raw)
        assert target == str(tmp_path)
        assert workdir(target) == str(tmp_path)


def test_focus_refuses_a_selected_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not accept a selected file"):
        resolve(FOCUS.context, str(tmp_path), "pomo.yaml")


@pytest.mark.parametrize("raw", ["", "relative", "file://remote.invalid/tmp"])
def test_focus_refuses_invalid_directory_context(raw: str) -> None:
    with pytest.raises(ValueError):
        resolve(FOCUS.context, raw)


def test_focus_refuses_a_file_as_invoking_directory(tmp_path: Path) -> None:
    config = tmp_path / "pomo.yaml"
    config.touch()
    with pytest.raises(ValueError, match="cwd is not a directory"):
        resolve(FOCUS.context, str(config))

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from rexdeck import integrations

JSON = integrations.get("json")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("jless 0.9.0\n", "0.9.0"),
        ("jless 0.9.0", "0.9.0"),
        ("jless 0.10.0\n", "0.10.0"),
        ("jless-ng 0.9.0\n", None),
        ("jless 0.9\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(JSON.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_json_accepts_only_selected_json_files_and_lists_it() -> None:
    assert JSON.context == "selected-file"
    assert JSON.selected_file == ".json"
    assert JSON.args == ("--json",)
    assert JSON.versions == ("0.9.0",)
    assert JSON.describe()["selected_file"] == {
        "suffixes": [".json"],
        "directories": False,
    }
    assert "selected_file" not in integrations.get("viewer").describe()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"file_suffixes": ()}, "must accept a file suffix or directories"),
        ({"file_suffixes": ("json",)}, "invalid or repeated file suffixes"),
        ({"file_suffixes": (".JSON",)}, "invalid or repeated file suffixes"),
        ({"file_suffixes": (".json", ".json")}, "invalid or repeated file suffixes"),
        ({"context": "directory"}, "declares file selection without selected-file"),
    ],
)
def test_selected_file_declarations_fail_loudly(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(JSON, **changes)  # type: ignore[arg-type]


def test_directory_only_selection_spec() -> None:
    logs = replace(JSON, file_suffixes=(), file_directories=True)
    assert logs.selected_file == "/"
    assert replace(JSON, file_directories=True).selected_file == ".json,/"

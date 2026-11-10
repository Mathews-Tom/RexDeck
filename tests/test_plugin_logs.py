from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck.catalogue.logs import INTEGRATION
from rexdeck.host.launch import resolve


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("lnav 0.14.1\n", "0.14.1"),
        ("lnav 0.14.1", "0.14.1"),
        ("lnav 0.14.2\n", "0.14.2"),
        ("lnav-ng 0.14.1\n", None),
        ("lnav 0.14\n", None),
        ("lnav 0.14.1-dev\n", None),
        ("Version: lnav 0.14.1\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_logs_descriptor_uses_only_explicit_local_selection() -> None:
    assert INTEGRATION.id == "logs"
    assert INTEGRATION.tool == "lnav"
    assert INTEGRATION.versions == ("0.14.1",)
    assert INTEGRATION.context == "selected-file"
    assert INTEGRATION.args == ()
    assert INTEGRATION.selected_file == ".log,.txt,.out,/"
    assert INTEGRATION.describe()["selected_file"] == {
        "suffixes": [".log", ".txt", ".out"],
        "directories": True,
    }


@pytest.mark.parametrize("suffix", [".log", ".txt", ".out", ".LOG"])
@pytest.mark.parametrize("representation", ["relative", "absolute", "uri"])
def test_explicit_local_log_selection(
    tmp_path: Path, suffix: str, representation: str
) -> None:
    target = tmp_path / ("-server ; $(touch nope)" + suffix)
    target.write_text("2026-11-10 10:00:00 INFO fixture\n", encoding="utf-8")
    selected = {
        "relative": target.name,
        "absolute": str(target),
        "uri": target.as_uri(),
    }[representation]
    assert resolve(
        f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), selected
    ) == str(target.resolve())


def test_directory_selection_includes_rotated_names(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "server.log.0").write_text("rotated fixture\n", encoding="utf-8")
    assert resolve(
        f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), "logs"
    ) == str(logs.resolve())
    with pytest.raises(ValueError):
        resolve(
            f"selected-file:{INTEGRATION.selected_file}",
            str(tmp_path),
            "logs/server.log.0",
        )


@pytest.mark.parametrize("suffix", [".json", ".tar", ".gz", ".zip", ".log.0"])
def test_non_log_and_archive_suffixes_are_refused(tmp_path: Path, suffix: str) -> None:
    target = tmp_path / ("input" + suffix)
    target.write_text("fixture\n", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve(
            f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), target.name
        )


@pytest.mark.parametrize(
    "selected",
    [None, "", "missing.log", "-", "user@host:/var/log/test.log", "ssh://host/a.log"],
)
def test_missing_stdin_and_remote_selections_are_refused(
    tmp_path: Path, selected: str | None
) -> None:
    with pytest.raises(ValueError):
        resolve(f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), selected)


@pytest.mark.parametrize("selected", ["x[1]/app.log", "logs*"])
def test_lnav_wildcard_paths_are_refused(tmp_path: Path, selected: str) -> None:
    target = tmp_path / selected
    if target.suffix:
        target.parent.mkdir()
        target.write_text("2026-11-10 10:00:00 INFO fixture\n", encoding="utf-8")
    else:
        target.mkdir()
    with pytest.raises(ValueError, match="Selected paths cannot contain"):
        resolve(f"selected-file:{INTEGRATION.selected_file}", str(tmp_path), selected)

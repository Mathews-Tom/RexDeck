from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from rexdeck import dependencies, integrations, lifecycle
from rexdeck.host.launch import resolve

DASHBOARD = integrations.get("dashboard")
REPORT = (
    "/Users/druk/go/bin/dashbrew: go1.27.3\n"
    "\tpath\tgithub.com/rasjonell/dashbrew/cmd/dashbrew\n"
    "\tmod\tgithub.com/rasjonell/dashbrew\tv1.2.0\t"
    "h1:A8uSHKIod+zv+oGmGpRy8h+vwoJE5FmJEBSOUNIndHA=\n"
    "\tdep\tgithub.com/charmbracelet/bubbletea\tv1.3.4\th1:example=\n"
)


@pytest.mark.parametrize(
    ("output", "version"),
    [
        (REPORT, "1.2.0"),
        (REPORT.replace("v1.2.0", "v1.3.0"), "1.3.0"),
        (REPORT.replace("\tmod\t", "\tdep\t"), None),
        (REPORT.replace("dashbrew\tv1", "dashbrew-extra\tv1"), None),
        (REPORT.replace("v1.2.0", "v1.2.0-rc.1"), None),
        (REPORT.replace("v1.2.0", "v1.2.0+dirty"), None),
        (REPORT.replace("v1.2.0", "(devel)"), None),
        (REPORT.replace("v1.2.0", "v1.2"), None),
        (REPORT.replace("v1.2.0", "1.2.0"), None),
        ("dashbrew 1.2.0\n", None),
    ],
)
def test_module_version_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(DASHBOARD.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version


def test_dashboard_declares_explicit_config_and_go_probe() -> None:
    assert DASHBOARD.context == "selected-file"
    assert DASHBOARD.selected_file == ".json"
    assert DASHBOARD.args == ("-c",)
    assert DASHBOARD.versions == ("1.2.0",)
    assert DASHBOARD.prerequisites == (("go", "https://go.dev/dl/"),)
    assert DASHBOARD.version_probe == ("go", "version", "-m", "{tool}")
    assert DASHBOARD.homebrew is None
    assert DASHBOARD.describe()["selected_file"] == {
        "suffixes": [".json"],
        "directories": False,
    }
    assert DASHBOARD.default_layout == integrations.Layout("right", 50)
    capabilities = " ".join(DASHBOARD.capabilities)
    for disclosure in ("sh -c", "5-second", "auth", "todo", "never auto-loads"):
        assert disclosure in capabilities


def test_probe_uses_go_metadata_not_dashbrew_version() -> None:
    executable = "/trusted/bin/dashbrew"
    with (
        patch.object(dependencies, "_resolve", return_value="/trusted/bin/go"),
        patch.object(
            lifecycle, "_run", return_value=lifecycle._Result(0, REPORT.encode())
        ) as run,
    ):
        assert dependencies._probe(executable, DASHBOARD) == "1.2.0"
    assert run.call_args.args[0] == [
        "/trusted/bin/go",
        "version",
        "-m",
        executable,
    ]


def test_probe_refuses_unqualified_module_version() -> None:
    with (
        patch.object(dependencies, "_resolve", return_value="/trusted/bin/go"),
        patch.object(
            lifecycle,
            "_run",
            return_value=lifecycle._Result(
                0, REPORT.replace("v1.2.0", "v1.3.0").encode()
            ),
        ),
        pytest.raises(RuntimeError, match="Unqualified dashbrew version 1.3.0"),
    ):
        dependencies._probe("/trusted/bin/dashbrew", DASHBOARD)


def test_explicit_json_selection_and_argv(tmp_path: Path) -> None:
    board = tmp_path / "trusted board.JSON"
    board.write_text("{}")
    # A checkout's default board must never substitute for a missing selection.
    (tmp_path / "dashboard.json").write_text("{}")
    for selected in (board.name, str(board), board.as_uri()):
        target = resolve(
            f"selected-file:{DASHBOARD.selected_file}", str(tmp_path), selected
        )
        assert target == str(board)
        assert [DASHBOARD.tool, *DASHBOARD.args, target] == [
            "dashbrew",
            "-c",
            str(board),
        ]


@pytest.mark.parametrize("selected", [None, "", ".", "missing.json", "notes.txt"])
def test_selection_refusals_never_fall_back(
    tmp_path: Path, selected: str | None
) -> None:
    (tmp_path / "dashboard.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("{}")
    with pytest.raises(ValueError):
        resolve(f"selected-file:{DASHBOARD.selected_file}", str(tmp_path), selected)


def test_symlink_must_target_json_file(tmp_path: Path) -> None:
    board = tmp_path / "board.json"
    board.write_text("{}")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(board)
    assert resolve(
        f"selected-file:{DASHBOARD.selected_file}", str(tmp_path), str(alias)
    ) == str(board)
    plain = tmp_path / "plain.txt"
    plain.write_text("{}")
    disguised = tmp_path / "disguised.json"
    disguised.symlink_to(plain)
    with pytest.raises(ValueError):
        resolve(
            f"selected-file:{DASHBOARD.selected_file}", str(tmp_path), str(disguised)
        )

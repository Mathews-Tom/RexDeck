from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck import integrations
from rexdeck.host import launch

PROCS = integrations.get("procs")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("dekit 0.10.3\n", "0.10.3"),
        ("dekit 0.10.3", "0.10.3"),
        ("dekit 0.10.4\n", "0.10.4"),
        ("mprocs 0.10.3\n", None),
        ("dekit 0.10\n", None),
        ("dekit 0.10.3-beta\n", None),
        ("dekit-cli 0.10.3\n", None),
    ],
)
def test_version_report_is_parsed_exactly(output: str, version: str | None) -> None:
    found = re.search(PROCS.version_pattern, output, re.MULTILINE)
    assert (found.group(1) if found else None) == version
    if version is not None:
        assert (version in PROCS.versions) == (version == "0.10.3")


def test_selected_config_uses_explicit_root_not_yaml_positional(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = project / "dekit.yaml"
    config.write_text("tasks: {}\n")
    assert launch.resolve(PROCS.context, str(tmp_path), "project/dekit.yaml") == str(
        config.resolve()
    )
    assert PROCS.args == ()
    assert launch.workdir(str(config)) == str(project)
    assert PROCS.describe()["context"] == "dekit-config"
    capabilities = " ".join(PROCS.capabilities)
    for disclosure in ("arbitrary", "outlive", "down", "write-back", "XDG_RUNTIME_DIR"):
        assert disclosure in capabilities


@pytest.mark.parametrize(
    "selection", [None, "missing.yaml", "other.yaml", "dekit.yml", "."]
)
def test_invalid_config_never_falls_back_to_discovered_project(
    tmp_path: Path, selection: str | None
) -> None:
    (tmp_path / "dekit.yaml").write_text("tasks: {}\n")
    (tmp_path / "other.yaml").write_text("tasks: {}\n")
    (tmp_path / "dekit.yml").write_text("tasks: {}\n")
    with pytest.raises((ValueError, OSError)):
        launch.resolve(PROCS.context, str(tmp_path), selection)


def test_symlink_cannot_select_other_named_config(tmp_path: Path) -> None:
    elsewhere = tmp_path / "other.yaml"
    elsewhere.write_text("tasks: {}\n")
    (tmp_path / "dekit.yaml").symlink_to(elsewhere)
    with pytest.raises(ValueError, match="exactly dekit.yaml"):
        launch.resolve(PROCS.context, str(tmp_path), "dekit.yaml")


@pytest.mark.parametrize("character", ["*", "?", "[", "\\"])
def test_config_retains_shared_glob_character_refusal(
    tmp_path: Path, character: str
) -> None:
    project = tmp_path / ("project" + character)
    project.mkdir()
    config = project / "dekit.yaml"
    config.write_text("tasks: {}\n")
    with pytest.raises(ValueError, match="Selected paths cannot contain"):
        launch.resolve(PROCS.context, str(tmp_path), str(config))

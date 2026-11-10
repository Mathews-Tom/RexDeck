from __future__ import annotations

import re
from pathlib import Path

import pytest

from rexdeck.catalogue.music import INTEGRATION
from rexdeck.host import launch
from rexdeck.integrations import Layout, get


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_real_version_report_qualifies(ending: str) -> None:
    match = re.search(
        INTEGRATION.version_pattern, "spotify_player 0.25.2" + ending, re.MULTILINE
    )
    assert match is not None
    assert match.group(1) == "0.25.2"
    assert match.group(1) in INTEGRATION.versions


@pytest.mark.parametrize(
    "output",
    [
        "0.25.2",
        "spotify-player 0.25.2",
        "not_spotify_player 0.25.2",
        "spotify_player 0.25.1",
        "spotify_player 0.25.20",
        "spotify_player 0.25.2-rc1",
        "spotify_player 0.25.2+custom",
        "spotify_player 0.25.2 unexpected",
    ],
)
def test_unpinned_or_malformed_version_is_not_qualified(output: str) -> None:
    match = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
    assert match is None or match.group(1) not in INTEGRATION.versions


@pytest.mark.parametrize("uri", [False, True])
def test_music_accepts_invoking_directory_without_selecting_account(
    tmp_path: Path, uri: bool
) -> None:
    cwd = tmp_path / "music space 雪"
    cwd.mkdir()
    raw = cwd.as_uri() if uri else str(cwd)
    assert launch.resolve(INTEGRATION.context, raw) == str(cwd.resolve())


def test_music_refuses_missing_cwd_or_file_context(tmp_path: Path) -> None:
    file = tmp_path / "playlist.json"
    file.write_text("{}", encoding="utf-8")
    for raw in (str(file), str(tmp_path / "missing")):
        with pytest.raises((OSError, ValueError)):
            launch.resolve(INTEGRATION.context, raw)
    with pytest.raises(ValueError, match="does not accept a selected file"):
        launch.resolve(INTEGRATION.context, str(tmp_path), str(file))


def test_music_preserves_foreground_upstream_auth_and_storage() -> None:
    assert get("music") is INTEGRATION
    assert INTEGRATION.title == "Music"
    assert INTEGRATION.tool == "spotify_player"
    assert INTEGRATION.context == "directory"
    assert INTEGRATION.versions == ("0.25.2",)
    assert INTEGRATION.license == "MIT"
    assert INTEGRATION.default_layout == Layout("right", 50)
    # No daemon, authenticate subcommand, credentials, device or config overrides.
    assert INTEGRATION.args == ()
    assert INTEGRATION.prerequisites == ()
    assert INTEGRATION.owned_module is None

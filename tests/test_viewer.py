from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from rexdeck import dependencies
from rexdeck.host import launch


@pytest.mark.parametrize("missing", ["pdftoppm", "resvg"])
def test_missing_viewer_helper_is_actionable_before_tool_probe(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setattr(
        dependencies,
        "_resolve",
        lambda name: None if name == missing else sys.executable,
    )
    with pytest.raises(RuntimeError, match=f"(?i){missing}.*prerequisite") as error:
        dependencies.ensure("viewer")
    assert "rexdeck enable viewer" in str(error.value)
    assert "never installs" in str(error.value)


def test_svg_selection_accepts_xml_namespace_and_long_preamble(tmp_path: Path) -> None:
    target = tmp_path / "namespaced.svg"
    target.write_text(
        '<?xml version="1.0"?><!--'
        + "long preamble " * 200
        + '--><s:svg xmlns:s="http://www.w3.org/2000/svg"><s:rect/></s:svg>',
        encoding="utf-8",
    )
    assert launch.resolve("media", str(tmp_path), str(target)) == str(target.resolve())


def test_svg_selection_does_not_expand_external_entities(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        port = listener.getsockname()[1]
        target = tmp_path / "entity.svg"
        target.write_text(
            f'<!DOCTYPE svg [<!ENTITY external SYSTEM "http://127.0.0.1:{port}/remote">]>'
            '<svg xmlns="http://www.w3.org/2000/svg"><text>&external;</text></svg>',
            encoding="utf-8",
        )
        assert launch.resolve("media", str(tmp_path), str(target)) == str(
            target.resolve()
        )
        with pytest.raises(TimeoutError):
            listener.accept()

from __future__ import annotations

import errno
import io
import json
import os
import socket
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import quote

import filesystem
import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project café"
    root.mkdir()
    return root


def test_identity_sorting_and_display_control_names(project: Path) -> None:
    (project / "z directory").mkdir()
    names = ["b.txt", "A.md", "東京 space.txt", "line\nbreak\t.txt", "quote'\".txt"]
    for name in names:
        (project / name).write_text("content", encoding="utf-8")
    entries = filesystem.list_directory(str(project))["entries"]
    assert entries[0]["name"] == "z directory"
    assert [item["name"] for item in entries[1:]] == sorted(
        names, key=lambda s: (s.casefold(), s)
    )
    for item in entries:
        assert item["id"] == item["path"] == str(project / item["name"])


@pytest.mark.parametrize(
    "authority", ["", "localhost", "LOCALHOST", socket.gethostname(), socket.getfqdn()]
)
def test_local_uri_root_is_not_hub_cwd(project: Path, authority: str) -> None:
    uri = f"file://{authority}{quote(str(project))}"
    assert filesystem.source_root(uri) == project.resolve()


@pytest.mark.parametrize(
    "source",
    [
        "",
        ".",
        "file://remote.invalid/tmp",
        "file://user@localhost/tmp",
        "file://localhost:22/tmp",
        "file:///tmp?x=1",
        "file:///tmp#x",
        "file:///tmp%GG",
        "file:///tmp%FF",
        "file:///tmp\n",
    ],
)
def test_unsupported_origin_refused(source: str) -> None:
    with pytest.raises((OSError, ValueError)):
        filesystem.source_root(source)


def test_symlink_root_confinement_and_broken_siblings(
    project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    target = project / "inside.txt"
    target.write_text("inside", encoding="utf-8")
    (project / "inside-link").symlink_to(target)
    (project / "escape-link").symlink_to(outside)
    (project / "broken").symlink_to(project / "missing")
    (project / "loop").symlink_to(project / "loop")
    entries = {e["name"]: e for e in filesystem.list_directory(str(project))["entries"]}
    assert entries["broken"]["kind"] == entries["loop"]["kind"] == "other"
    assert filesystem.file_target(str(project), str(project / "inside-link"))[
        "path"
    ] == str(target)
    with pytest.raises(filesystem.BoundaryError):
        filesystem.file_target(str(project), str(project / "escape-link"))
    with pytest.raises(filesystem.BoundaryError):
        filesystem.selected_path(project, str(project.parent))


def test_root_slash_has_no_parent_escape() -> None:
    assert filesystem.source_root("/") == Path("/")
    assert filesystem.selected_path(Path("/"), "/tmp").is_absolute()


def test_renamed_and_missing_origin_are_errors(project: Path) -> None:
    nested = project / "nested"
    nested.mkdir()
    filesystem.list_directory(str(project), str(nested))
    nested.rename(project / "renamed")
    with pytest.raises(FileNotFoundError):
        filesystem.list_directory(str(project), str(nested))
    assert filesystem.list_directory(str(project))["path"] == str(project)
    project.rename(project.with_name("moved"))
    with pytest.raises(FileNotFoundError):
        filesystem.list_directory(str(project))


def test_nonregular_targets(project: Path) -> None:
    fifo = project / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(filesystem.BoundaryError):
        filesystem.file_target(str(project), str(fifo))


def test_denied_open_is_reported(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = project / "unreadable.txt"
    target.write_text("secret", encoding="utf-8")

    def denied_open(path: str | os.PathLike[str], flags: int) -> int:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), os.fspath(path))

    monkeypatch.setattr(os, "open", denied_open)
    output = io.StringIO()
    with redirect_stdout(output):
        code = filesystem.main(["file", str(project), str(target)])
    result = json.loads(output.getvalue())
    assert code == 1 and result["ok"] is False
    assert "PermissionError" in result["error"]
    assert str(target) in result["error"]
    assert "path" not in result


def test_directory_payload_refuses_without_partial_rows(project: Path) -> None:
    for i in range(400):
        (project / (f"{i:04d}-" + "long-name-" * 20 + ".txt")).touch()
    output = io.StringIO()
    with redirect_stdout(output):
        code = filesystem.main(["list", str(project)])
    result = json.loads(output.getvalue())
    assert code == 1 and result["ok"] is False
    assert "entries" not in result
    assert len(output.getvalue().encode()) < filesystem.MAX_HELPER_BYTES
    assert len(list(project.iterdir())) == 400


def test_success_stdout_at_exact_helper_budget(project: Path) -> None:
    names: list[str] = []
    # Each ASCII filename byte contributes three encoded bytes (id, name, path).
    for count in range(1, 200):
        candidates = [f"{i:04d}" for i in range(count)]
        entries = [
            {
                "id": str(project / name),
                "name": name,
                "path": str(project / name),
                "kind": "file",
            }
            for name in candidates
        ]
        size = len(
            filesystem.encoded(
                {
                    "ok": True,
                    "root": str(project),
                    "path": str(project),
                    "entries": entries,
                }
            )
        )
        remaining, remainder = divmod(filesystem.MAX_HELPER_BYTES - size, 3)
        if remainder or not 0 <= remaining <= count * 251:
            continue
        for i in range(count):
            extra = min(remaining, 251)
            candidates[i] += "x" * extra
            remaining -= extra
        names = candidates
        break
    assert names, "Could not construct the exact encoded-byte boundary"
    for name in names:
        (project / name).touch()
    result = subprocess.run(
        [sys.executable, filesystem.__file__, "list", str(project)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert len(result.stdout) == filesystem.MAX_HELPER_BYTES
    reply = json.loads(result.stdout)
    assert reply["ok"] is True
    assert {entry["name"] for entry in reply["entries"]} == set(names)


@pytest.mark.parametrize(
    "content",
    [b"\x00binary", b"\x1b]52;c;bad\x07", b"invalid\xff", b"unfinished\xe2\x82"],
)
def test_binary_and_non_utf8_are_not_launched(project: Path, content: bytes) -> None:
    target = project / "input.txt"
    target.write_bytes(content)
    with pytest.raises(filesystem.BoundaryError):
        filesystem.file_target(str(project), str(target))


def test_text_byte_limit_and_utf8_chunk_boundary(project: Path) -> None:
    target = project / "boundary.txt"
    target.write_bytes(b"x" * (65536 - 1) + "東京".encode() + b"\n")
    assert filesystem.file_target(str(project), str(target))["path"] == str(target)
    target.write_bytes(b"x" * filesystem.MAX_TEXT_BYTES)
    assert filesystem.file_target(str(project), str(target))["path"] == str(target)
    with target.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(filesystem.BoundaryError):
        filesystem.file_target(str(project), str(target))


@pytest.mark.parametrize(
    "replacement",
    [b"\x00binary", "changed".encode("utf-16"), b"x" * (filesystem.MAX_TEXT_BYTES + 1)],
)
def test_validated_snapshot_survives_file_changes(
    project: Path, replacement: bytes
) -> None:
    target = project / "snapshot.py"
    original = b"x" * (65536 - 1) + "東京\n".encode()
    target.write_bytes(original)
    metadata, content = filesystem.read_text_file(str(project), str(target))
    target.write_bytes(replacement)
    assert content == original
    assert metadata == {"ok": True, "root": str(project), "path": str(target)}
    with pytest.raises(filesystem.BoundaryError):
        filesystem.file_target(str(project), str(target))


def test_non_utf8_path_input_refuses_instead_of_changing_identity(
    project: Path,
) -> None:
    with pytest.raises(filesystem.BoundaryError):
        filesystem.absolute_path(str(project) + "/invalid-\udcff")

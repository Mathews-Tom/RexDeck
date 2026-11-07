"""Bounded, read-only local filesystem queries for the self-contained Files plugin."""

from __future__ import annotations

import codecs
import json
import os
import re
import socket
import stat
import sys
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import unquote, urlsplit

# The complete Lua view has a separate byte guard, including encoded actions.
MAX_HELPER_BYTES = 12 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024


class Entry(TypedDict):
    id: str
    name: str
    path: str
    kind: Literal["directory", "file", "other"]


class FileResult(TypedDict):
    ok: Literal[True]
    root: str
    path: str


class ListResult(FileResult):
    entries: list[Entry]


class BoundaryError(ValueError):
    """The selected input is outside the supported local read boundary."""


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def absolute_path(value: str) -> Path:
    if "\x00" in value:
        raise BoundaryError("Path contains a NUL character")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise BoundaryError("A UTF-8 filename is required") from error
    path = Path(value)
    if not path.is_absolute():
        raise BoundaryError("An absolute local path is required")
    return path


def source_root(source_pwd: str) -> Path:
    if source_pwd.lower().startswith("file:"):
        if any(ord(c) < 32 or ord(c) == 127 for c in source_pwd):
            raise BoundaryError("File URI contains unescaped control characters")
        uri = urlsplit(source_pwd)
        local_hosts = {
            "",
            "localhost",
            socket.gethostname().casefold(),
            socket.getfqdn().casefold(),
        }
        if uri.netloc.casefold() not in local_hosts:
            raise BoundaryError("Nonlocal or credentialed file URI is not allowed")
        if uri.query or uri.fragment or "?" in source_pwd or "#" in source_pwd:
            raise BoundaryError("File URI must not contain a query or fragment")
        if re.search(r"%(?![0-9a-fA-F]{2})", uri.path):
            raise BoundaryError("File URI contains an invalid percent encoding")
        try:
            source_pwd = unquote(uri.path, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BoundaryError("File URI contains invalid UTF-8") from error
    root = absolute_path(source_pwd).resolve(strict=True)
    if not root.is_dir():
        raise BoundaryError("Source pwd is not a directory")
    return root


def selected_path(root: Path, value: str) -> Path:
    path = absolute_path(value)
    if not path.resolve(strict=False).is_relative_to(root):
        raise BoundaryError(
            "Selected path escapes the project root, including symlinks"
        )
    return path.resolve(strict=True)


def list_directory(source_pwd: str, directory: str | None = None) -> ListResult:
    root = source_root(source_pwd)
    path = root if directory is None else selected_path(root, directory)
    entries: list[Entry] = []
    result: ListResult = {
        "ok": True,
        "root": str(root),
        "path": str(path),
        "entries": entries,
    }
    size = len(encoded(result))
    with os.scandir(path) as children:
        for child in children:
            try:
                mode = child.stat(follow_symlinks=True).st_mode
            except OSError:
                if not child.is_symlink():
                    raise
                mode = child.stat(follow_symlinks=False).st_mode
            kind: Literal["directory", "file", "other"]
            if stat.S_ISDIR(mode):
                kind = "directory"
            elif stat.S_ISREG(mode):
                kind = "file"
            else:
                kind = "other"
            entry: Entry = {
                "id": child.path,
                "name": child.name,
                "path": child.path,
                "kind": kind,
            }
            try:
                child.path.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise BoundaryError(
                    "Directory contains an unsupported non-UTF-8 filename; listing refused"
                ) from error
            size += len(encoded(entry)) + (1 if entries else 0)
            if size > MAX_HELPER_BYTES:
                raise BoundaryError(
                    "Directory exceeds the supported listing byte budget. "
                    "No partial listing was returned; use Parent, Refresh or Return."
                )
            entries.append(entry)
    entries.sort(
        key=lambda item: (
            item["kind"] != "directory",
            item["name"].casefold(),
            item["name"],
        )
    )
    return result


def read_text_file(source_pwd: str, filename: str) -> tuple[FileResult, bytes]:
    """Return metadata and the bounded UTF-8 bytes validated for this preview."""
    root = source_root(source_pwd)
    path = selected_path(root, filename)
    if not stat.S_ISREG(path.stat().st_mode):
        raise BoundaryError("Selected path is not a regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise BoundaryError("Selected path is not a regular file")
        if info.st_size > MAX_TEXT_BYTES:
            raise BoundaryError("Text preview supports files up to 2 MiB")
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        total = 0
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TEXT_BYTES:
                raise BoundaryError("Text preview supports files up to 2 MiB")
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError as error:
                raise BoundaryError(
                    "Unsupported text encoding; UTF-8 is required"
                ) from error
            if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                raise BoundaryError(
                    "Binary/control content is not supported in text preview"
                )
            chunks.append(chunk)
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise BoundaryError(
                "Unsupported text encoding; UTF-8 is required"
            ) from error
    finally:
        os.close(descriptor)
    return {"ok": True, "root": str(root), "path": str(path)}, b"".join(chunks)


def file_target(source_pwd: str, filename: str) -> FileResult:
    target, _ = read_text_file(source_pwd, filename)
    return target


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    try:
        result: ListResult | FileResult
        if len(args) in (2, 3) and args[0] == "list":
            result = list_directory(args[1], args[2] if len(args) == 3 else None)
        elif len(args) == 3 and args[0] == "file":
            result = file_target(args[1], args[2])
        else:
            raise BoundaryError(
                "Use list SOURCE_PWD [DIRECTORY] or file SOURCE_PWD PATH"
            )
        reply = encoded(result)
        if len(reply) > MAX_HELPER_BYTES:
            raise BoundaryError("Filesystem result exceeds the supported byte budget")
    except (OSError, ValueError, RuntimeError) as error:
        message = f"{type(error).__name__}: {error}"
        # OS error paths may contain arbitrary filename bytes; keep errors small, too.
        print(encoded({"ok": False, "error": message[:512]}).decode("ascii"))
        return 1
    print(reply.decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

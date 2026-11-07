"""Explicit source builds and read-only verification of the unchanged LazyRex pin.

The archive is the source authority; build.json is a local build receipt, not a
signature or a defense against an actor able to replace this code and receipt.
Preparation never adopts existing binaries. Startup never creates a receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, cast

_COMMIT = "8b9e30b6b6b63f957a2696e9027e0bbe5c6fea0f"
_ARCHIVE_HASH = "616ca2aa0acf63da2a84b211006443dd31d6dbd13e8a86bc5e01f8ce07a8f2f3"
_SOURCE = f"LazyRex-{_COMMIT}"
_GO_VERSION = "go version go1.27.3 darwin/arm64"
_BINARIES = ("lazyrex-hub", "lazyrex-view")
_MAX_ARCHIVE = 64 * 1024 * 1024
_MAX_EXPANDED = 512 * 1024 * 1024
_MAX_MEMBERS = 100_000


@dataclass(frozen=True)
class RuntimePaths:
    source: Path
    sdk: Path
    hub: Path
    view: Path


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _regular(path: Path, *, executable: bool = False) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError(f"Expected an unlinked regular file, not a link: {path}")
    if executable and not os.access(path, os.X_OK):
        raise RuntimeError(f"Runtime binary is not executable: {path}")


def _directory(path: Path) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError(f"Expected a directory, not a symlink: {path}")


def _json(path: Path) -> dict[str, object]:
    _regular(path)
    if path.stat().st_size > 64 * 1024:
        raise RuntimeError(f"Oversized runtime metadata: {path}")
    value: object = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_pairs
    )
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return cast("dict[str, object]", value)


def _text(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Runtime lock requires a nonempty string: {key}")
    return value


def _relative(value: str) -> None:
    parts = PurePosixPath(value).parts
    if (
        not parts
        or value != "/".join(parts)
        or value.startswith("/")
        or "\\" in value
        or any(part in {".", ".."} for part in parts)
    ):
        raise RuntimeError(f"Unsafe runtime lock path: {value}")


def _lock(project: Path) -> dict[str, object]:
    document = _json(project / "runtime.lock.json")
    if (
        set(document)
        != {
            "schema",
            "repository",
            "commit",
            "archive_url",
            "archive",
            "archive_sha256",
            "source_directory",
            "sdk",
            "license",
            "platform",
            "go",
            "build_environment",
            "builds",
        }
        or type(document.get("schema")) is not int
        or document["schema"] != 1
    ):
        raise RuntimeError("Malformed runtime lock schema")
    for key, supported in (
        ("commit", _COMMIT),
        ("archive_sha256", _ARCHIVE_HASH),
        ("platform", "darwin/arm64"),
        ("go", _GO_VERSION),
    ):
        if _text(document, key) != supported:
            raise RuntimeError(f"Runtime lock does not match the supported pin: {key}")
    environment = document["build_environment"]
    if (
        not isinstance(environment, dict)
        or set(environment)
        != {
            "GOOS",
            "GOARCH",
            "CGO_ENABLED",
            "GOENV",
            "GOTOOLCHAIN",
            "GOWORK",
            "GOFLAGS",
            "GOPROXY",
            "GOSUMDB",
        }
        or not all(isinstance(value, str) for value in environment.values())
    ):
        raise RuntimeError("Malformed runtime build environment")
    if (
        f"{environment['GOOS']}/{environment['GOARCH']}" != document["platform"]
        or environment["CGO_ENABLED"] not in {"0", "1"}
        or environment["GOTOOLCHAIN"] != "local"
        or any(environment[key] != "off" for key in ("GOENV", "GOWORK"))
        or environment["GOFLAGS"] != ""
        or environment["GOSUMDB"] != "sum.golang.org"
    ):
        raise RuntimeError("Unsafe or unsupported runtime build environment")
    proxy = urllib.parse.urlsplit(environment["GOPROXY"])
    if (
        proxy.scheme != "https"
        or proxy.netloc != "proxy.golang.org"
        or proxy.path not in {"", "/"}
        or proxy.query
        or proxy.fragment
    ):
        raise RuntimeError("Runtime builds require the verified HTTPS Go module proxy")
    repository = urllib.parse.urlsplit(_text(document, "repository"))
    if (
        repository.scheme != "https"
        or repository.netloc != "github.com"
        or repository.path != "/lazy-rex/LazyRex"
        or repository.query
        or repository.fragment
    ):
        raise RuntimeError("Runtime lock has an unsupported upstream repository")
    archive_url = _text(document, "archive_url")
    if archive_url != f"https://codeload.github.com{repository.path}/tar.gz/{_COMMIT}":
        raise RuntimeError(
            "Runtime lock archive URL is not bound to the pinned repository/commit"
        )
    for key in ("archive", "source_directory", "sdk", "license"):
        _relative(_text(document, key))
    if (
        document["archive"] != "upstream.tar.gz"
        or document["source_directory"] != _SOURCE
        or document["license"] != "LICENSE"
    ):
        raise RuntimeError(
            "Runtime lock paths do not match the owned archive/source/license layout"
        )
    builds = document["builds"]
    if not isinstance(builds, dict) or set(builds) != set(_BINARIES):
        raise RuntimeError("Runtime lock must build both hub and view")
    for name in _BINARIES:
        build: object = builds[name]
        if not isinstance(build, dict) or set(build) != {"directory", "arguments"}:
            raise RuntimeError(f"Malformed runtime build recipe: {name}")
        recipe = cast("dict[str, object]", build)
        _relative(_text(recipe, "directory"))
        arguments = recipe["arguments"]
        if (
            not isinstance(arguments, list)
            or len(arguments) != 5
            or not all(isinstance(argument, str) for argument in arguments)
            or arguments[:4] != ["build", "-trimpath", "-o", "{output}"]
        ):
            raise RuntimeError(
                f"Runtime build must use the trimpath source recipe: {name}"
            )
        package = cast("str", arguments[4])
        if package != ".":
            if not package.startswith("./"):
                raise RuntimeError(
                    f"Runtime build requires a local source package: {name}"
                )
            _relative(package[2:])
    return document


def _host() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Pinned runtime requires darwin/arm64")


def _digest_stream(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _digest(path: Path) -> str:
    _regular(path)
    with path.open("rb") as stream:
        return _digest_stream(stream)


def _archive(path: Path) -> None:
    _regular(path)
    if path.stat().st_size > _MAX_ARCHIVE or _digest(path) != _ARCHIVE_HASH:
        raise RuntimeError("Pinned source archive digest mismatch")


def _members(bundle: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    total = 0
    for member in bundle:
        raw = member.name.rstrip("/")
        parts = PurePosixPath(raw).parts
        if (
            not parts
            or parts[0] != _SOURCE
            or raw != "/".join(parts)
            or any(part in {".", "..", ".git", ".gitmodules"} for part in parts)
            or "\\" in raw
            or not (member.isfile() or member.isdir())
            or member.mode & 0o7000
            or member.size < 0
        ):
            raise RuntimeError(f"Unsafe source archive member: {member.name}")
        if raw in names:
            raise RuntimeError(f"Duplicate source archive member: {raw}")
        names.add(raw)
        members.append(member)
        total += member.size
        if len(members) > _MAX_MEMBERS or total > _MAX_EXPANDED:
            raise RuntimeError("Source archive exceeds extraction limits")
    if not members:
        raise RuntimeError("Empty source archive")
    directories = {member.name.rstrip("/") for member in members if member.isdir()}
    for member in members:
        for ancestor in PurePosixPath(member.name).parents:
            if str(ancestor) != "." and str(ancestor) not in directories:
                raise RuntimeError(f"Source archive lacks parent directory: {ancestor}")
    files = {member.name for member in members if member.isfile()}
    required = {
        f"{_SOURCE}/{name}" for name in ("LICENSE", "hub/go.mod", "view/go.mod")
    }
    if not required <= files or f"{_SOURCE}/sdk/lua" not in directories:
        raise RuntimeError("Source archive lacks the license, modules or Lua SDK")
    return members


def _source(archive: Path, source: Path) -> None:
    _directory(source)
    actual = {"."}
    for directory, folders, files in os.walk(source, followlinks=False):
        for name in (*folders, *files):
            path = Path(directory) / name
            actual.add(path.relative_to(source).as_posix())
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(
                    f"Upstream source contains a link or special file: {path}"
                )
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = _members(bundle)
        expected: set[str] = set()
        for member in members:
            relative = PurePosixPath(member.name).relative_to(_SOURCE)
            path = source.joinpath(*relative.parts)
            expected.add(relative.as_posix())
            if member.isdir():
                _directory(path)
            else:
                _regular(path)
                original = bundle.extractfile(member)
                if original is None:
                    raise RuntimeError(f"Unreadable archive member: {member.name}")
                with original:
                    original_hash = _digest_stream(original)
                if _digest(path) != original_hash:
                    raise RuntimeError(f"Upstream source modified: {path}")
            if stat.S_IMODE(path.stat().st_mode) != member.mode & 0o777:
                raise RuntimeError(f"Upstream source mode modified: {path}")
        if actual != expected:
            raise RuntimeError("Upstream source contains extra or missing entries")


def _extract(archive: Path, destination: Path) -> None:
    # Do not delegate path handling to extractall, even with a platform filter.
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = _members(bundle)
        for member in sorted(
            members, key=lambda entry: len(PurePosixPath(entry.name).parts)
        ):
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(mode=0o700)
                continue
            original = bundle.extractfile(member)
            if original is None:
                raise RuntimeError(f"Unreadable archive member: {member.name}")
            with original, target.open("xb") as output:
                shutil.copyfileobj(original, output, 1024 * 1024)
            target.chmod(member.mode & 0o777)
        for member in reversed(members):
            if member.isdir():
                destination.joinpath(*PurePosixPath(member.name).parts).chmod(
                    member.mode & 0o777
                )


def _paths(root: Path, lock: dict[str, object]) -> RuntimePaths:
    source = root / _text(lock, "source_directory")
    return RuntimePaths(
        source,
        source / _text(lock, "sdk"),
        root / "bin/lazyrex-hub",
        root / "bin/lazyrex-view",
    )


def _metadata(lock: dict[str, object]) -> dict[str, object]:
    return {
        "schema": 1,
        "lock_sha256": hashlib.sha256(_canonical(lock).encode()).hexdigest(),
        "commit": lock["commit"],
        "archive_sha256": lock["archive_sha256"],
        "platform": lock["platform"],
        "go": lock["go"],
    }


def _verify(project: Path, workspace: Path) -> RuntimePaths:
    lock = _lock(project)
    _host()
    _directory(workspace)
    root = workspace / "runtime"
    _directory(root)
    if {path.name for path in root.iterdir()} != {
        "upstream.tar.gz",
        _SOURCE,
        "bin",
        "build.json",
    }:
        raise RuntimeError(
            "Runtime layout is incomplete or contains unowned entries; "
            "inspect it or explicitly prepare a fresh workspace"
        )
    paths = _paths(root, lock)
    _archive(root / "upstream.tar.gz")
    _source(root / "upstream.tar.gz", paths.source)
    _directory(paths.sdk)
    _directory(root / "bin")
    if {path.name for path in (root / "bin").iterdir()} != set(_BINARIES):
        raise RuntimeError("Runtime binary directory contains extra or missing entries")
    receipt = _json(root / "build.json")
    binaries = receipt.pop("binaries", None)
    if _canonical(receipt) != _canonical(_metadata(lock)) or not isinstance(
        binaries, dict
    ):
        raise RuntimeError("Malformed or mismatched runtime build receipt")
    digests = cast("dict[str, object]", binaries)
    if set(digests) != set(_BINARIES):
        raise RuntimeError("Malformed runtime binary receipt")
    for name, path in zip(_BINARIES, (paths.hub, paths.view), strict=True):
        value = digests[name]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RuntimeError(f"Malformed binary digest in build receipt: {name}")
        _regular(path, executable=True)
        if _digest(path) != value:
            raise RuntimeError(f"Runtime binary digest mismatch: {path}")
    return paths


def verify_runtime(project: Path, workspace: Path) -> RuntimePaths:
    """Verify archive, exact source tree and received binaries without any writes.

    Go is not needed here. A missing/invalid receipt is never repaired or adopted.
    """
    try:
        return _verify(project, workspace)
    except (OSError, ValueError, EOFError, RecursionError, tarfile.TarError) as error:
        raise RuntimeError(
            f"Cannot verify runtime: {error}; inspect it or explicitly prepare a fresh workspace"
        ) from error


def _run(
    arguments: list[str], environment: dict[str, str], *, cwd: Path | None = None
) -> str:
    result = subprocess.run(
        arguments, cwd=cwd, env=environment, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Runtime command failed ({arguments[0]}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _download(path: Path, lock: dict[str, object]) -> None:
    url = _text(lock, "archive_url")
    request = urllib.request.Request(
        url, headers={"User-Agent": "RexDeck-runtime-prepare"}
    )
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        path.open("xb") as output,
    ):
        if response.geturl() != url:
            raise RuntimeError(
                "Runtime archive download redirected away from the pinned URL"
            )
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > _MAX_ARCHIVE:
                raise RuntimeError("Runtime archive download exceeds size limit")
            output.write(block)
    _archive(path)


def _build(
    source: Path, output: Path, temporary: Path, lock: dict[str, object]
) -> None:
    go = shutil.which("go")
    if go is None:
        raise RuntimeError(
            "Runtime preparation requires Go go1.27.3 darwin/arm64 on PATH"
        )
    # Ignore persisted Go settings and ambient cross-compilers, overlays, alternate
    # workspaces and module proxies. Fresh caches cannot adopt edited modules.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GO", "CGO_"))
        and key not in {"CC", "CXX", "AR", "PKG_CONFIG"}
    }
    locked_environment = cast("dict[str, str]", lock["build_environment"])
    environment.update(locked_environment)
    environment.update(
        {
            "GOPATH": str(temporary / "go"),
            "GOMODCACHE": str(temporary / "modules"),
            "GOCACHE": str(temporary / "cache"),
        }
    )
    if _run([go, "version"], environment) != _text(lock, "go"):
        raise RuntimeError(f"Runtime preparation requires exactly {_text(lock, 'go')}")
    output.mkdir()
    builds = cast("dict[str, dict[str, object]]", lock["builds"])
    for name in _BINARIES:
        recipe = builds[name]
        directory = _text(recipe, "directory")
        binary = output / name
        arguments = cast("list[str]", recipe["arguments"])
        command = [
            go,
            *(
                str(binary) if argument == "{output}" else argument
                for argument in arguments
            ),
        ]
        _run(command, environment, cwd=source / directory)
        _regular(binary, executable=True)
        info = _run([go, "version", "-m", str(binary)], environment)
        lines = info.splitlines()
        settings = {
            line.strip().removeprefix("build\t")
            for line in lines[1:]
            if line.strip().startswith("build\t")
        }
        expected_settings = {
            "-trimpath=true",
            *(
                f"{key}={locked_environment[key]}"
                for key in ("GOOS", "GOARCH", "CGO_ENABLED")
            ),
        }
        if (
            not lines
            or lines[0] != f"{binary}: {_text(lock, 'go').split()[2]}"
            or not expected_settings <= settings
        ):
            raise RuntimeError(
                f"Built artifact does not match pinned Go build recipe: {binary}"
            )
        _run([go, "mod", "verify"], environment, cwd=source / directory)


def prepare_runtime(project: Path, workspace: Path) -> RuntimePaths:
    """Explicitly download (or use a staged archive), verify, build and receipt.

    Only an absent runtime or a directory containing just upstream.tar.gz may be
    initialized. Existing receipted runtimes are strictly verified, never rebuilt
    to hide tampering. Foreign/partial source and binaries are never overwritten.
    """
    try:
        lock = _lock(project)
        _host()
        workspace = workspace.absolute()
        if workspace.is_symlink():
            raise RuntimeError("Runtime workspace must not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True)
        _directory(workspace)
        root = workspace / "runtime"
        if root.is_symlink():
            raise RuntimeError("Runtime directory must not be a symlink")
        root.mkdir(exist_ok=True)
        _directory(root)
        if (root / "build.json").exists() or (root / "build.json").is_symlink():
            return _verify(project, workspace)
        if {path.name for path in root.iterdir()} - {"upstream.tar.gz"}:
            raise RuntimeError(
                "Refusing to adopt or overwrite unreceipted runtime source/binaries; "
                "explicitly prepare a fresh workspace"
            )
        staged_archive = root / "upstream.tar.gz"
        if staged_archive.exists() or staged_archive.is_symlink():
            _archive(staged_archive)
        with tempfile.TemporaryDirectory(
            prefix=".prepare-", dir=root
        ) as temporary_name:
            temporary = Path(temporary_name)
            archive = staged_archive
            if not archive.exists():
                archive = temporary / "upstream.tar.gz"
                _download(archive, lock)
            _extract(archive, temporary)
            source = temporary / _SOURCE
            _source(archive, source)
            _directory(source / _text(lock, "sdk"))
            _build(source, temporary / "bin", temporary, lock)
            _archive(archive)
            _source(archive, source)
            receipt = _metadata(lock)
            receipt["binaries"] = {
                name: _digest(temporary / "bin" / name) for name in _BINARIES
            }
            receipt_path = temporary / "build.json"
            with receipt_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(receipt, indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            if {path.name for path in root.iterdir()} - {
                temporary.name,
                "upstream.tar.gz",
            }:
                raise RuntimeError(
                    "Runtime directory changed during preparation; refusing publication"
                )
            if archive != staged_archive:
                os.link(archive, staged_archive)
                archive.unlink()
            source.rename(root / _SOURCE)
            (temporary / "bin").rename(root / "bin")
            # Last publication step: no receipt can exist for a failed build.
            os.link(receipt_path, root / "build.json")
            receipt_path.unlink()
        return _verify(project, workspace)
    except (
        OSError,
        ValueError,
        EOFError,
        RecursionError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as error:
        raise RuntimeError(f"Cannot prepare runtime: {error}") from error

"""Selected-tool qualification and explicitly consented existing-Homebrew installs."""

from __future__ import annotations

import codecs
import errno
import json
import os
import platform
import re
import select
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from rexdeck import lifecycle

_TOOLS = {"files": "yazi", "git": "lazygit"}
_VERSIONS = {
    "yazi": ("26.9.1",),
    "lazygit": ("0.66.2", "0.66.3"),
    "brew": ("7.0.12",),
}
_RELEASES = {
    "yazi": "https://github.com/sxyazi/yazi/releases/tag/v26.9.1",
    "lazygit": "https://github.com/jesseduffield/lazygit/releases/tag/v0.66.3",
}
_PATTERNS = {
    "yazi": (
        r"^Yazi(?:[ \t]+|[ \t]*\r?\n[ \t]*Version:[ \t]*)"
        r"(\d+\.\d+\.\d+)(?:\s|$)"
    ),
    "lazygit": r"(?:^|,\s*)version=(\d+\.\d+\.\d+)(?=,|\s*$)",
    "brew": r"^Homebrew (\d+\.\d+\.\d+)(?:\s|$)",
}
_INSTALL_ENV = {
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_INSTALL_UPGRADE": "1",
    "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1",
}
_METADATA_TIMEOUT = 120.0


def _resolve(name: str) -> str | None:
    """Search absolute PATH entries while preserving lexical executable identity."""
    search_path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
        if Path(entry).is_absolute()
    )
    found = shutil.which(name, path=search_path) if search_path else None
    return str(Path(found).absolute()) if found is not None else None


def _probe(executable: str, name: str) -> str:
    """Require an explicitly qualified upstream version before registration."""
    result = lifecycle._run([executable, "--version"], dict(os.environ))
    text = result.output.decode(errors="replace").strip()
    version = re.search(_PATTERNS[name], text, re.MULTILINE)
    if result.code != 0 or version is None:
        raise RuntimeError(f"Cannot identify {name} version at {executable}: {text}")
    value = version.group(1)
    if value not in _VERSIONS[name]:
        raise RuntimeError(
            f"Unqualified {name} version {value} at {executable}; "
            f"RexDeck qualifies {name} {' or '.join(_VERSIONS[name])} only"
        )
    return value


def _manual(plugin: str) -> str:
    """Give version-qualified manual steps without authorizing installation."""
    tool = _TOOLS[plugin]
    return (
        f"Manually install {tool} {' or '.join(_VERSIONS[tool])} from {_RELEASES[tool]}. "
        f"On macOS arm64 with existing qualified Homebrew {_VERSIONS['brew'][0]}, "
        f"the manual command is: brew install {tool}. Inspect its selected version "
        "and dependency/source-build/upgrade effects first; RexDeck does not run "
        "that manual route. "
        f"Verify with {tool} --version, then rerun: rexdeck enable {plugin}. "
        "RexDeck never upgrades or uninstalls existing packages."
    )


def _manager() -> str:
    """Qualify an existing manager's platform, version, layout, and redirects."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Automatic installation supports macOS arm64 only")
    brew = _resolve("brew")
    if brew is None:
        raise RuntimeError(
            "Existing Homebrew was not found on PATH; no manager is installed"
        )
    _probe(brew, "brew")
    result = lifecycle._run([brew, "--prefix"], dict(os.environ))
    prefix = result.output.decode(errors="replace").strip()
    path = Path(prefix)
    if (
        result.code != 0
        or not prefix
        or "\n" in prefix
        or "\x00" in prefix
        or not path.is_absolute()
        or not path.is_dir()
    ):
        raise RuntimeError(
            f"Homebrew did not report an existing absolute prefix: {prefix!r}"
        )
    # Homebrew supports short custom Apple Silicon prefixes as well as its default.
    if prefix != "/opt/homebrew" and len(os.fsencode(prefix)) > 13:
        raise RuntimeError(
            f"Unqualified Homebrew prefix {prefix!r}; supported Apple Silicon prefixes "
            "are /opt/homebrew or an existing custom prefix of at most 13 bytes"
        )
    result = lifecycle._run([brew, "--cellar"], dict(os.environ))
    cellar = result.output.decode(errors="replace").strip()
    if (
        result.code != 0
        or cellar != str(path / "Cellar")
        or len(os.fsencode(cellar)) > 20
        or Path(brew).resolve() != (path / "bin/brew").resolve()
    ):
        raise RuntimeError(
            "Unqualified Homebrew layout; use the prefix's own bin/brew and its "
            "standard prefix/Cellar (at most 20 bytes)"
        )
    if os.environ.get("HOMEBREW_NO_RELOCATE_BUILD_PREFIX"):
        raise RuntimeError(
            "Homebrew build prefix relocation is disabled; automatic installation "
            "requires its supported default relocation"
        )
    for name, expected in (
        ("HOMEBREW_PREFIX", prefix),
        ("HOMEBREW_CELLAR", cellar),
        ("HOMEBREW_BREW_FILE", brew),
    ):
        override = os.environ.get(name)
        if override and Path(override).resolve() != Path(expected).resolve():
            raise RuntimeError(
                f"{name} redirects the qualified Homebrew layout; use the selected "
                "manager directly in a clean environment"
            )
    if (
        os.environ.get("HOMEBREW_BREW_FILE")
        and os.environ.get("HOMEBREW_PATH")
        and os.environ["HOMEBREW_PATH"] != os.environ.get("PATH")
    ):
        raise RuntimeError(
            "HOMEBREW_PATH redirects the selected manager's PATH; invoke Homebrew "
            "directly in a clean environment"
        )
    for name in ("HOMEBREW_NO_INSTALL_FROM_API", "HOMEBREW_FORCE_API_AUTO_UPDATE"):
        if os.environ.get(name):
            raise RuntimeError(
                f"{name} prevents frozen API metadata; install the tool manually"
            )
    _session_members(os.getsid(0))
    return brew


def _session_members(session: int) -> list[int]:
    """Find live same-user session members; never infer ownership from command text."""
    result = subprocess.run(
        ["/bin/ps", "-axo", "uid=,pid=,stat="],
        capture_output=True,
        check=True,
        timeout=5,
    )
    members = []
    for row in result.stdout.splitlines():
        uid, raw_pid, state = row.split()
        pid = int(raw_pid)
        if int(uid) != os.getuid() or state.startswith(b"Z"):
            continue
        try:
            if os.getsid(pid) == session:
                members.append(pid)
        except ProcessLookupError:
            continue
    return members


def _stop_session(child: subprocess.Popen[bytes]) -> None:
    """Stop separate helper groups before reaping the session's reserved leader PID."""
    deadline = time.monotonic() + 5
    while True:
        members = _session_members(child.pid)
        if not members:
            return
        for pid in members:
            try:
                if os.getsid(pid) == child.pid:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        if time.monotonic() >= deadline:
            raise RuntimeError("Cannot confirm owned Homebrew helpers have stopped")
        time.sleep(0.02)


def _brew_command(
    argv: list[str], environment: dict[str, str], *, capture: bool = False
) -> bytes:
    """Bound metadata output/time and own helpers across separate process groups."""
    child: subprocess.Popen[bytes] | None = None
    output = bytearray()
    deadline = time.monotonic() + _METADATA_TIMEOUT if capture else None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    exit_queue = select.kqueue()
    try:
        with lifecycle._defer_cancellation():
            child = subprocess.Popen(
                argv,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=None if capture else subprocess.STDOUT,
                start_new_session=True,
            )
            events = exit_queue.control(
                [
                    select.kevent(
                        child.pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                1,
                0,
            )
            # A fast child can already be an unreaped zombie when registered.
            if events and events[0].flags & select.KQ_EV_ERROR:
                if events[0].data != errno.ESRCH:
                    raise OSError(events[0].data, "Cannot observe Homebrew exit")
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise RuntimeError("Homebrew metadata command timed out")
                for key, _event in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fd)
                    elif capture:
                        output.extend(chunk)
                        if len(output) > lifecycle._MAX_READ:
                            raise RuntimeError(
                                "Homebrew metadata output exceeded its limit"
                            )
                    else:
                        sys.stderr.write(decoder.decode(chunk))
                        sys.stderr.flush()
        # kqueue observes exit without reaping, including on Python 3.12 macOS.
        # The reserved leader PID/SID remains ours until helper cleanup completes.
        if not events:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise RuntimeError("Homebrew metadata command timed out")
            if not exit_queue.control(None, 1, remaining):
                raise RuntimeError("Homebrew metadata command timed out")
        if not capture:
            sys.stderr.write(decoder.decode(b"", final=True))
            sys.stderr.flush()
    finally:
        try:
            if child is not None:
                with lifecycle._defer_cancellation():
                    try:
                        _stop_session(child)
                    finally:
                        child.wait()
                        if child.stdout is not None:
                            child.stdout.close()
        finally:
            exit_queue.close()
    if child.returncode != 0:
        raise RuntimeError(
            f"Installer exited with status {child.returncode}: {shlex.join(argv)}"
        )
    return bytes(output)


def _qualify_bottle(output: bytes, tool: str) -> None:
    """Accept qualified core bottles with no runtime dependencies or install hooks."""
    try:
        data = json.loads(output)
        formulae = data["formulae"]
        if not isinstance(formulae, list) or len(formulae) != 1:
            raise ValueError
        formula = formulae[0]
        versions = formula["versions"]
        files = formula["bottle"]["stable"]["files"]
        if (
            formula["name"] != tool
            or formula["tap"] != "homebrew/core"
            or versions["stable"] not in _VERSIONS[tool]
            or versions["bottle"] is not True
            or not isinstance(files, dict)
            or not files
            or formula["post_install_defined"] is not False
            or formula["disabled"] is not False
            or any(
                formula[key] != []
                for key in (
                    "dependencies",
                    "recommended_dependencies",
                    "optional_dependencies",
                    "uses_from_macos",
                    "requirements",
                    "options",
                )
            )
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Cannot qualify a dependency-free {tool} core bottle; automatic source "
            "builds, runtime dependencies and install hooks are unsupported. "
            "No package installation was started"
        ) from exc


@contextmanager
def _installer_signals() -> Iterator[None]:
    """Turn termination into an exception only while owning an installer session."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {signum: signal.getsignal(signum) for signum in signals}

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        """Unwind through exact-owned installer cancellation."""
        raise KeyboardInterrupt(f"Received signal {signum}")

    try:
        for signum, handler in previous.items():
            if handler != signal.SIG_IGN:
                signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _install(argv: list[str]) -> None:
    """Install only a dependency-free bottle using frozen private API metadata."""
    tool = argv[-1].rsplit("/", 1)[-1]
    try:
        with (
            _installer_signals(),
            tempfile.TemporaryDirectory(prefix="rexdeck-brew-") as cache,
        ):
            environment = {
                **os.environ,
                **_INSTALL_ENV,
                "HOMEBREW_CACHE": cache,
            }
            # NO_AUTO_UPDATE freezes this private API cache between info and install.
            # Bottles exclude build dependencies even when another transaction
            # installs those formulae concurrently.
            metadata = _brew_command(
                [argv[0], "info", "--json=v2", "--formula", argv[-1]],
                environment,
                capture=True,
            )
            _qualify_bottle(metadata, tool)
            _brew_command(argv, environment)
    except KeyboardInterrupt as exc:
        raise RuntimeError(
            "Dependency installation cancelled; no integration was registered"
        ) from exc


def ensure(plugin: str) -> lifecycle.QualifiedTool:
    """Reuse a qualified selected tool or request default-No installation consent."""
    tool = _TOOLS[plugin]
    if plugin == "git" and _resolve("git") is None:
        raise RuntimeError(
            "Git is a preinstalled prerequisite for the Git integration and was not "
            "found on PATH. Install Git using https://git-scm.com/downloads, make git "
            "available on PATH, then rerun: rexdeck enable git. RexDeck never installs Git"
        )
    executable = _resolve(tool)
    if executable is not None:
        try:
            qualified = lifecycle.QualifiedTool.capture(executable)
            _probe(executable, tool)
            qualified.require_current()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"{exc}. {_manual(plugin)}") from exc
        return qualified
    guidance = _manual(plugin)
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Missing {tool}; noninteractive input cannot consent. {guidance}"
        )
    try:
        brew = _manager()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Missing {tool}: {exc}. {guidance}") from exc
    argv = [brew, "install", "--formula", "--force-bottle", f"homebrew/core/{tool}"]
    print(
        f"Missing {tool}. Run {shlex.join(argv)}?\n"
        "This uses the existing Homebrew prefix, contacts package registries, downloads "
        "and runs upstream package installation code. A temporary private Homebrew "
        "cache freezes qualified core metadata. Only a qualified bottle with no runtime "
        "dependencies or install hooks is allowed; source builds are refused. Existing "
        "packages are not upgraded or removed. Homebrew auto-update, install-upgrade, "
        "dependent checks and automatic cleanup are disabled for these commands. "
        "It does not install Rex or Homebrew.\n"
        "Install this dependency? [y/N]: ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = sys.stdin.readline().strip().lower()
    except KeyboardInterrupt as exc:
        raise RuntimeError(
            f"Installation consent cancelled; no integration was registered. {guidance}"
        ) from exc
    if answer not in {"y", "yes"}:
        raise RuntimeError(
            f"Installation declined; no integration was registered. {guidance}"
        )
    try:
        _install(argv)
        executable = _resolve(tool)
        if executable is None:
            raise RuntimeError(
                f"Installer completed but {tool} is still missing from PATH"
            )
        qualified = lifecycle.QualifiedTool.capture(executable)
        _probe(executable, tool)
        qualified.require_current()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{exc}. No integration was registered. {guidance}") from exc
    return qualified

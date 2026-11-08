"""Selected-tool qualification and explicitly consented existing-Homebrew installs."""

from __future__ import annotations

import codecs
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
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
        f"the install command is: brew install {tool}. "
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
    return brew


def _install_environment(brew: str) -> dict[str, str]:
    """Forbid replacing installed formulae, without excluding missing dependencies."""
    environment = {**os.environ, **_INSTALL_ENV}
    result = lifecycle._run(
        [brew, "info", "--json=v2", "--installed", "--formula"],
        environment,
        inherit_stderr=True,
    )
    try:
        inventory = json.loads(result.output)
        if result.code != 0 or not isinstance(inventory, dict):
            raise ValueError
        formulae = inventory["formulae"]
        if not isinstance(formulae, list):
            raise ValueError
        forbidden = set(environment.get("HOMEBREW_FORBIDDEN_FORMULAE", "").split())
        for formula in formulae:
            if not isinstance(formula, dict):
                raise ValueError
            for key in ("name", "full_name"):
                name = formula[key]
                if (
                    not isinstance(name, str)
                    or re.fullmatch(
                        r"[A-Za-z0-9_+@.-]+(?:/[A-Za-z0-9_+@.-]+){0,2}", name
                    )
                    is None
                ):
                    raise ValueError
                forbidden.add(name)
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "Cannot inventory installed Homebrew formulae safely; no installer was "
            "started. Inspect brew info --json=v2 --installed --formula, or install "
            "the selected tool manually without replacing existing dependencies"
        ) from exc
    # Homebrew 7.0.12 checks this supported control against its computed install
    # dependencies in FormulaInstaller#prelude, before installing any dependency.
    # Satisfied dependencies are skipped; an installed but incompatible one is
    # forbidden rather than upgraded, while missing dependencies remain eligible.
    environment["HOMEBREW_FORBIDDEN_FORMULAE"] = " ".join(sorted(forbidden))
    return environment


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
    """Protect installed formulae, stream progress, and reap only our installer group."""
    child: subprocess.Popen[bytes] | None = None
    try:
        with _installer_signals():
            environment = _install_environment(argv[0])
            try:
                with lifecycle._defer_cancellation():
                    child = subprocess.Popen(
                        argv,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                assert child.stdout is not None
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while chunk := os.read(child.stdout.fileno(), 65536):
                    sys.stderr.write(decoder.decode(chunk))
                    sys.stderr.flush()
                sys.stderr.write(decoder.decode(b"", final=True))
                sys.stderr.flush()
                code = child.wait()
                if code != 0:
                    raise RuntimeError(
                        f"Installer exited with status {code}: {shlex.join(argv)}"
                    )
            except BaseException:
                # The new session makes this group ours, including download/build children.
                # Kill before reaping the leader; do not search for or touch other processes.
                with lifecycle._defer_cancellation():
                    if child is not None and child.returncode is None:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait()
                raise
    except KeyboardInterrupt as exc:
        raise RuntimeError(
            "Dependency installation cancelled; no integration was registered"
        ) from exc
    finally:
        if child is not None and child.stdout is not None:
            child.stdout.close()


def ensure(plugin: str) -> str:
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
            _probe(executable, tool)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"{exc}. {_manual(plugin)}") from exc
        return executable
    guidance = _manual(plugin)
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Missing {tool}; noninteractive input cannot consent. {guidance}"
        )
    try:
        brew = _manager()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Missing {tool}: {exc}. {guidance}") from exc
    argv = [brew, "install", tool]
    print(
        f"Missing {tool}. Run {shlex.join(argv)}?\n"
        "This uses the existing Homebrew prefix, contacts package registries, downloads "
        "and runs upstream package installation code, and may install transitive "
        "dependencies. Installed formulae are protected from replacement; an "
        "incompatible existing dependency makes installation fail instead of upgrading "
        "it. Homebrew auto-update, install-upgrade, dependent checks and automatic "
        "cleanup are disabled for this command. "
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
        _probe(executable, tool)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{exc}. No integration was registered. {guidance}") from exc
    return executable

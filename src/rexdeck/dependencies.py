"""Qualify selected preinstalled tools and explain owner-managed installation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from rexdeck import lifecycle

_TOOLS = {"files": "yazi", "git": "lazygit"}
_VERSIONS = {
    "yazi": ("26.9.1",),
    "lazygit": ("0.66.2", "0.66.3"),
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
    """Give owner-run, version-qualified installation and exact rerun instructions."""
    tool = _TOOLS[plugin]
    return (
        f"Manually install {tool} {' or '.join(_VERSIONS[tool])} from {_RELEASES[tool]}. "
        f"On macOS with existing Homebrew, inspect brew info {tool}; run brew install "
        f"{tool} only if it selects a qualified version and you accept its dependency, "
        "build and upgrade effects. Otherwise skip Homebrew and use the qualified "
        f"upstream release. Verify with {tool} --version, put the executable in a "
        f"trusted absolute PATH directory, then rerun: rexdeck enable {plugin}. "
        "RexDeck never installs, upgrades or removes upstream tools."
    )


def ensure(plugin: str) -> lifecycle.QualifiedTool:
    """Validate only the selected preinstalled tool; never prompt or run an installer."""
    tool = _TOOLS[plugin]
    if plugin == "git" and _resolve("git") is None:
        raise RuntimeError(
            "Git is a preinstalled prerequisite for the Git integration and was not "
            "found on PATH. Install Git using https://git-scm.com/downloads, make git "
            "available on PATH, then rerun: rexdeck enable git. RexDeck never installs Git"
        )
    executable = _resolve(tool)
    if executable is None:
        raise RuntimeError(
            f"Missing {tool} on PATH; no integration was registered. {_manual(plugin)}"
        )
    try:
        qualified = lifecycle.QualifiedTool.capture(executable)
        _probe(executable, tool)
        qualified.require_current()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{exc}. {_manual(plugin)}") from exc
    return qualified

"""Qualify selected preinstalled tools and explain owner-managed installation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from rexdeck import integrations, lifecycle


def _resolve(name: str) -> str | None:
    """Search absolute PATH entries while preserving lexical executable identity."""
    search_path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
        if Path(entry).is_absolute()
    )
    found = shutil.which(name, path=search_path) if search_path else None
    return str(Path(found).absolute()) if found is not None else None


def _probe(executable: str, integration: integrations.Integration) -> str:
    """Require an explicitly qualified upstream version before registration."""
    name = integration.tool
    result = lifecycle._run([executable, "--version"], dict(os.environ))
    text = result.output.decode(errors="replace").strip()
    version = re.search(integration.version_pattern, text, re.MULTILINE)
    if result.code != 0 or version is None:
        raise RuntimeError(f"Cannot identify {name} version at {executable}: {text}")
    value = version.group(1)
    if value not in integration.versions:
        raise RuntimeError(
            f"Unqualified {name} version {value} at {executable}; "
            f"RexDeck qualifies {name} {' or '.join(integration.versions)} only"
        )
    return value


def _manual(integration: integrations.Integration) -> str:
    """Give owner-run, version-qualified installation and exact rerun instructions."""
    tool = integration.tool
    return (
        f"Manually install {tool} {' or '.join(integration.versions)} from "
        f"{integration.release}. "
        f"On macOS with existing Homebrew, inspect brew info {tool}; run brew install "
        f"{tool} only if it selects a qualified version and you accept its dependency, "
        "build and upgrade effects. Otherwise skip Homebrew and use the qualified "
        f"upstream release. Verify with {tool} --version, put the executable in a "
        f"trusted absolute PATH directory, then rerun: rexdeck enable {integration.id}. "
        "RexDeck never installs, upgrades or removes upstream tools."
    )


def ensure(plugin: str) -> lifecycle.QualifiedTool:
    """Validate only the selected preinstalled tool; never prompt or run an installer."""
    integration = integrations.get(plugin)
    for prerequisite, source in integration.prerequisites:
        name = prerequisite.capitalize()
        if _resolve(prerequisite) is None:
            raise RuntimeError(
                f"{name} is a preinstalled prerequisite for the "
                f"{integration.title} integration and was not found on PATH. Install "
                f"{name} using {source}, make {prerequisite} available on PATH, then "
                f"rerun: rexdeck enable {plugin}. RexDeck never installs {name}"
            )
    tool = integration.tool
    executable = _resolve(tool)
    if executable is None:
        raise RuntimeError(
            f"Missing {tool} on PATH; no integration was registered. "
            f"{_manual(integration)}"
        )
    try:
        qualified = lifecycle.QualifiedTool.capture(executable)
        _probe(executable, integration)
        qualified.require_current()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{exc}. {_manual(integration)}") from exc
    return qualified

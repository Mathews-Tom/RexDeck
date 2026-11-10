"""Qualify selected preinstalled tools and explain owner-managed installation."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
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
    argv = [executable, "--version"]
    env = dict(os.environ)
    if integration.version_probe:
        reporter = _resolve(integration.version_probe[0])
        if reporter is None:
            raise RuntimeError(
                f"Cannot identify {name} version: {integration.version_probe[0]} "
                "is not on PATH"
            )
        argv = [
            reporter,
            *(
                executable if part == "{tool}" else part
                for part in integration.version_probe[1:]
            ),
        ]
        # Metadata reporters must not select or download a checkout's Go toolchain.
        env["GOTOOLCHAIN"] = "local"
        result = lifecycle._run(argv, env, cwd="/")
    else:
        result = lifecycle._run(argv, env)
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
    formula = integration.homebrew or tool
    # A probe reads the executable by path, so the shown check resolves it first.
    check = (
        " ".join(
            f'"$(command -v {tool})"' if part == "{tool}" else shlex.quote(part)
            for part in integration.version_probe
        )
        if integration.version_probe
        else f"{tool} --version"
    )
    return (
        f"Manually install {tool} {' or '.join(integration.versions)} from "
        f"{integration.release}. "
        f"On macOS with existing Homebrew, inspect brew info {formula}; run brew "
        f"install {formula} only if it selects a qualified version and you accept its "
        "dependency, build and upgrade effects. Otherwise skip Homebrew and use the "
        f"qualified upstream release. Verify with {check}, put the executable in a "
        f"trusted absolute PATH directory, then rerun: rexdeck enable {integration.id}. "
        "RexDeck never installs, upgrades or removes upstream tools."
    )


def ensure(plugin: str) -> lifecycle.QualifiedTool:
    """Validate only the selected preinstalled tool; never prompt or run an installer."""
    integration = integrations.get(plugin)
    if integration.owned_module is not None:
        return lifecycle.QualifiedTool.capture(str(Path(sys.executable).absolute()))
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

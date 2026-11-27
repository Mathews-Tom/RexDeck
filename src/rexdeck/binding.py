"""Exact Rex block binding: endpoint incarnation, block lookup and verified focus.

Callers record a block by the server instance, session, block and host IDs Rex
reported, never by labels, titles or cwd. Every later action revalidates that
whole identity first, so a restarted server or a reused block ID is refused.
"""

from __future__ import annotations

import json
from typing import cast

from rexdeck import lifecycle


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise RuntimeError("Invalid Rex object")
    return cast(dict[str, object], value)


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("Rex requires nonempty string identities")
    return value


def instance(setup: lifecycle.Setup, purpose: str) -> str:
    """Return the selected endpoint's current server instance ID."""
    current = lifecycle._server_status(setup, selected=True)
    if (
        current is None
        or lifecycle._canonical_server(_identity(current.get("endpoint")))
        != setup.server
    ):
        raise RuntimeError(f"Selected {purpose} endpoint is not available")
    return _identity(current.get("instance_id"))


def call(
    setup: lifecycle.Setup, session: str, method: str, args: dict[str, object]
) -> dict[str, object]:
    return lifecycle._rex(setup, "api", "call", "-s", session, method, json.dumps(args))


def _listed(setup: lifecycle.Setup, session: str) -> list[object] | None:
    """The session's raw block list, or None when the session is gone."""
    sessions = lifecycle._rex(setup, "ls", "--json").get("sessions")
    if not isinstance(sessions, list):
        raise RuntimeError("Invalid Rex session list")
    if not any(_object(item).get("session_id") == session for item in sessions):
        return None
    value = call(setup, session, "session.list_blocks", {})
    listed = value.get("blocks") or []
    if not isinstance(listed, list):
        raise RuntimeError("Invalid Rex block list")
    return listed


def block(
    setup: lifecycle.Setup, session: str, block_id: str
) -> dict[str, object] | None:
    """Find one exact block, placed or detached, or None when it or its session is gone."""
    for item in _listed(setup, session) or []:
        candidate = _object(item)
        if candidate.get("block_id") == block_id:
            return candidate
    return None


def blocks(setup: lifecycle.Setup, session: str) -> dict[str, dict[str, object]] | None:
    """Every block in the session by ID, or None when the session is gone."""
    listed = _listed(setup, session)
    if listed is None:
        return None
    found: dict[str, dict[str, object]] = {}
    for item in listed:
        candidate = _object(item)
        if isinstance(candidate.get("block_id"), str):
            found[cast(str, candidate["block_id"])] = candidate
    return found


def focus(
    setup: lifecycle.Setup,
    *,
    purpose: str,
    instance_id: object,
    session: object,
    block_id: object,
    host_id: object,
) -> None:
    """Focus a recorded block only if its server, placement and host still match."""
    if instance(setup, purpose) != instance_id:
        raise RuntimeError("Captured Rex server was replaced; no focus attempted")
    exact_session, exact_block = _identity(session), _identity(block_id)
    found = block(setup, exact_session, exact_block)
    if found is None or found.get("placement") != "placed":
        raise RuntimeError("Exact target is closed or unbound; no focus attempted")
    if found.get("host_id") != host_id:
        raise RuntimeError("Captured block host changed; no focus attempted")
    call(setup, exact_session, "session.focus_block", {"block_id": exact_block})

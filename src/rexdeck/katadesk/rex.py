"""Kata Desk's Rex side: verify the desk's own block, place agents and read their state.

Agents are identified only by the IDs Rex reported when they were placed: server
instance, session, block and host. Titles, labels and cwd never identify one.
Every action revalidates the server instance first, so a recorded block from a
replaced server is refused before any Rex mutation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from rexdeck import binding, lifecycle
from rexdeck.katadesk.agents import Agent

PURPOSE = "Kata Desk"
FLAVOR = "com.superlogical.terminal.shell"
BACKGROUND_WINDOW = "Kata agents"
MAX_RECORDS = 128
# Rex terminal events that change an agent's badge.
STATUS_EVENTS = ("program_status_changed", "program_status_removed", "child_exited")
BADGES = {
    "working": "working",
    "blocked": "waiting for you",
    "done": "done",
    "error": "error",
    "idle": "idle",
}


@dataclass(frozen=True)
class Desk:
    """The desk's own verified Rex block, captured when the desk opened."""

    session_id: str
    block_id: str
    host_id: str
    instance_id: str


@dataclass(frozen=True)
class State:
    """One agent's badge from Rex, or why it has none."""

    badge: str
    # When the agent began waiting for the owner; None unless it waits now.
    waiting_since: str | None = None
    gone: bool = False
    replaced: bool = False

    @property
    def running(self) -> bool:
        return not (self.gone or self.replaced or self.badge.startswith("exited"))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RuntimeError("Rex returned a malformed object")
    return cast(dict[str, object], value)


def _id(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError(f"Rex returned no {what}")
    return value


def moment(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def badge(records: object, process: Mapping[str, object]) -> State:
    """Rex's view of one agent: its exit, else its newest reported status."""
    last_exit = process.get("last_exit")
    if isinstance(last_exit, dict):
        code = last_exit.get("exit_code")
        return State(f"exited {code}" if isinstance(code, int) else "exited")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise RuntimeError("Rex returned malformed program status records")
    reports = [
        record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("state"), str)
        and isinstance(record.get("updated_at"), str)
    ]
    if not reports:
        # omp reports nothing with terminal.programStatus off: say so, never guess.
        return State("state unknown")
    newest = max(reports, key=lambda record: moment(record["updated_at"]))
    state = cast(str, newest["state"])
    # Rex documents an unknown state as idle.
    return State(
        BADGES.get(state, "idle"),
        cast(str, newest["updated_at"]) if state == "blocked" else None,
    )


def event_block(line: str) -> str | None:
    """The agent-relevant block a `rex events --json` line names, if any."""
    try:
        value = json.loads(line)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("type") != "session_event":
        return None
    payload = value.get("payload")
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
        return None
    detail = event["payload"]
    block = detail.get("block_id")
    if not isinstance(block, str):
        return None
    if event.get("type") == "block_closed":
        return block
    if event.get("type") == "block_event" and detail.get("name") in STATUS_EVENTS:
        return block
    return None


class AgentRex:
    """Rex calls for agents, all on the endpoint RexDeck setup saved."""

    def __init__(self, setup: lifecycle.Setup, environment: Mapping[str, str]) -> None:
        self.setup = setup
        self.environment = environment

    def desk(self) -> Desk:
        """Verify that this pane is a placed block on RexDeck's saved endpoint."""
        server = self.environment.get("REX_SERVER", "")
        session = self.environment.get("REX_SESSION", "")
        block = self.environment.get("REX_BLOCK", "")
        if not (
            server and session.startswith("session:") and block.startswith("block:")
        ):
            raise RuntimeError(
                "This pane does not report its Rex session and block; "
                "agents need Kata Desk opened from Rex"
            )
        if lifecycle._canonical_server(server) != self.setup.server:
            raise RuntimeError(
                "This pane runs on a different Rex endpoint than the one RexDeck "
                "setup saved; agents are disabled here"
            )
        instance = binding.instance(self.setup, PURPOSE)
        found = binding.block(self.setup, session, block)
        if found is None or found.get("placement") != "placed":
            raise RuntimeError("Kata Desk cannot find its own block in Rex")
        return Desk(session, block, _id(found.get("host_id"), "host"), instance)

    def require_current(self, desk: Desk) -> None:
        if binding.instance(self.setup, PURPOSE) != desk.instance_id:
            raise RuntimeError(
                "The Rex server was replaced since Kata Desk opened; reopen the desk"
            )

    def _call(
        self, session: str, method: str, args: dict[str, object]
    ) -> dict[str, object]:
        return binding.call(self.setup, session, method, args)

    def start(
        self,
        desk: Desk,
        placement: str,
        *,
        label: str,
        cwd: str,
        command: Sequence[str],
        path_prepend: Sequence[str],
        background: Agent | None,
    ) -> tuple[str, str | None, str]:
        """Place one agent block and return its block, window and host IDs."""
        self.require_current(desk)
        block: dict[str, object] = {
            "flavor": FLAVOR,
            "label": label,
            "options": {
                "cwd": cwd,
                "shell": "none",
                "login": False,
                "path_prepend": list(path_prepend),
                # An exited agent stays visible until Stop closes it.
                "exit": {"on_completion": False},
                "command": list(command),
            },
        }
        window: str | None = None
        if placement == "split":
            created = self._call(
                desk.session_id,
                "session.new_split",
                {
                    "anchor_id": desk.block_id,
                    "direction": "horizontal",
                    "side": "after",
                    "ratio": 0.5,
                    "focus": True,
                    "layout": {"block": block},
                },
            )
        elif placement == "window":
            created = self._call(
                desk.session_id,
                "session.new_window",
                {"focus": True, "window_label": label, "layout": {"block": block}},
            )
            window = _id(created.get("window_id"), "window")
        elif placement == "background" and background is not None:
            # Later background agents join the first one's window, unfocused.
            created = self._call(
                desk.session_id,
                "session.new_split",
                {
                    "anchor_id": background.block_id,
                    "direction": "vertical",
                    "side": "after",
                    "focus": False,
                    "layout": {"block": block},
                },
            )
            window = background.window_id
        elif placement == "background":
            created = self._call(
                desk.session_id,
                "session.new_window",
                {
                    "focus": False,
                    "window_label": BACKGROUND_WINDOW,
                    "layout": {"block": block},
                },
            )
            window = _id(created.get("window_id"), "window")
        else:
            raise RuntimeError(f"Unknown agent placement: {placement}")
        ids = created.get("block_ids")
        if not isinstance(ids, list) or len(ids) != 1:
            raise RuntimeError("Rex did not report exactly one agent block")
        block_id = _id(ids[0], "agent block")
        found = binding.block(self.setup, desk.session_id, block_id)
        if found is None or found.get("placement") != "placed":
            raise RuntimeError("Rex reported an agent block it cannot find")
        return block_id, window, _id(found.get("host_id"), "host")

    def states(self, agents: Sequence[Agent]) -> dict[str, State]:
        """Each agent's state, reading the server instance and block lists once."""
        if not agents:
            return {}
        instance = binding.instance(self.setup, PURPOSE)
        listed: dict[str, dict[str, dict[str, object]] | None] = {}
        result: dict[str, State] = {}
        for agent in agents:
            if agent.instance_id != instance:
                result[agent.block_id] = State("server replaced", replaced=True)
                continue
            if agent.session_id not in listed:
                listed[agent.session_id] = self._blocks(agent.session_id)
            blocks = listed[agent.session_id]
            found = None if blocks is None else blocks.get(agent.block_id)
            if found is None or found.get("host_id") != agent.host_id:
                result[agent.block_id] = State("gone", gone=True)
                continue
            target: dict[str, object] = {"block_id": agent.block_id, "args": {}}
            status = self._call(
                agent.session_id, "com.superlogical.terminal.program_status", target
            )
            process = self._call(
                agent.session_id, "com.superlogical.terminal.process", target
            )
            result[agent.block_id] = badge(status.get("records", []), process)
        return result

    def _blocks(self, session: str) -> dict[str, dict[str, object]] | None:
        sessions = lifecycle._rex(self.setup, "ls", "--json").get("sessions")
        if not isinstance(sessions, list):
            raise RuntimeError("Invalid Rex session list")
        if not any(_object(item).get("session_id") == session for item in sessions):
            return None
        blocks = self._call(session, "session.list_blocks", {}).get("blocks") or []
        if not isinstance(blocks, list):
            raise RuntimeError("Invalid Rex block list")
        found: dict[str, dict[str, object]] = {}
        for item in blocks:
            candidate = _object(item)
            if isinstance(candidate.get("block_id"), str):
                found[cast(str, candidate["block_id"])] = candidate
        return found

    def focus(self, agent: Agent) -> None:
        binding.focus(
            self.setup,
            purpose=PURPOSE,
            instance_id=agent.instance_id,
            session=agent.session_id,
            block_id=agent.block_id,
            host_id=agent.host_id,
        )

    def stop(self, agent: Agent) -> None:
        """Close exactly this agent's block after revalidating its identity."""
        if binding.instance(self.setup, PURPOSE) != agent.instance_id:
            raise RuntimeError("The agent's Rex server was replaced; nothing closed")
        found = binding.block(self.setup, agent.session_id, agent.block_id)
        if found is None:
            raise RuntimeError("The agent's block is already gone; nothing closed")
        if found.get("host_id") != agent.host_id:
            raise RuntimeError("The agent's block changed host; nothing closed")
        self._call(agent.session_id, "block.close", {"block_id": agent.block_id})

    def events_argv(self, desk: Desk) -> list[str]:
        return [
            lifecycle._executable("rex"),
            "-S",
            self.setup.server,
            "--autostart=false",
            "events",
            "--session",
            desk.session_id,
            "--json",
        ]

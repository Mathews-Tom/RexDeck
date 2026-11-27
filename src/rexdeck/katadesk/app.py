"""Kata Desk: a bound kata project's issues and your attention requests, read-only."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Markdown, Static

from rexdeck.katadesk.agents import Agent, Store, prompt_text
from rexdeck.katadesk.kata import (
    Daemon,
    Detail,
    Issue,
    Kata,
    KataError,
    Listing,
    Request,
    event_kind,
    tree,
)
from rexdeck.katadesk.rex import AgentRex, Desk, State, event_block, moment

# Wide enough for the list and the detail side by side; narrower panes stack them.
SIDE_BY_SIDE = 100
# Coalesce bursts of events (including the replay on connect) into one reload.
SETTLE = 0.3
# A single event line can carry an issue body; allow far more than asyncio's 64 KiB.
LINE_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    daemon: Daemon
    recipient: str
    listing: Listing | None
    unbound: str | None
    requests: tuple[Request, ...]
    clock: str


def _markdown(detail: Detail) -> str:
    issue = detail.issue
    facts = [
        issue.status,
        "—" if issue.priority is None else f"P{issue.priority}",
        f"owner {issue.owner or 'none'}",
        f"updated {issue.updated}",
    ]
    if detail.labels:
        facts.append("labels " + ", ".join(detail.labels))
    lines = [
        f"## {issue.ref} · {issue.title}",
        "",
        " · ".join(facts),
        "",
        detail.body or "_No body._",
    ]
    if detail.links:
        lines += ["", "### Links", ""] + [f"- {link}" for link in detail.links]
    if detail.children:
        lines += ["", f"### Children ({len(detail.children)})", ""]
        lines += [
            f"- {child.ref} · {child.status} · {child.title}"
            for child in detail.children
        ]
    count = f"{len(detail.comments)}{'+' if detail.comments_truncated else ''}"
    lines += ["", f"### Comments ({count})"]
    for comment in detail.comments:
        lines += ["", f"**{comment.author}** · {comment.created}", "", comment.body]
    return "\n".join(lines)


@dataclass(frozen=True)
class AgentSetup:
    """What starting agents needs: verified Rex calls, the store and omp's path."""

    rex: AgentRex
    store: Store
    omp: str
    endpoint: str


class Choice(ModalScreen[str | None]):
    """A one-key question: each listed key answers with its value; escape cancels."""

    CSS = """
    Choice { align: center middle; }
    #question { width: 76; height: auto; padding: 1 2; border: solid $accent;
                background: $surface; }
    """

    def __init__(self, question: str, answers: dict[str, tuple[str, str]]) -> None:
        super().__init__()
        self.question = question
        self.answers = answers

    def compose(self) -> ComposeResult:
        keys = "   ".join(f"{key} {label}" for key, (_, label) in self.answers.items())
        yield Static(
            f"{self.question}\n\n{keys}   esc cancel", id="question", markup=False
        )

    def on_key(self, event: Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key == "escape":
            self.dismiss(None)
        elif event.key in self.answers:
            self.dismiss(self.answers[event.key][0])


class KataDesk(App[None]):
    TITLE = "RexDeck Kata Desk"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #header, #status { height: auto; padding: 0 1; }
    #status { color: $accent; }
    #body { height: 1fr; layout: horizontal; }
    #rows { width: 1fr; height: 1fr; }
    #detail { width: 1fr; height: 1fr; padding: 0 1; border-left: solid $panel; }
    #body.stacked { layout: vertical; }
    #body.stacked #detail { border-left: none; border-top: solid $panel; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "issues", "Issues"),
        Binding("2", "attention", "Attention"),
        Binding("s", "status_filter", "Open/closed"),
        Binding("a", "agent", "Agent"),
        Binding("g", "focus_agent", "Go to agent"),
        Binding("n", "next_waiting", "Next waiting"),
        Binding("x", "stop_agent", "Stop agent"),
    ]

    def __init__(
        self, kata: Kata, agents: AgentSetup | str = "agents are not configured"
    ) -> None:
        super().__init__()
        self.kata = kata
        self.mode = "issues"
        self.status_filter = "open"
        self.snapshot: Snapshot | None = None
        self.state = "loading"
        self.notice = ""
        self.rows: list[tuple[int, Issue]] = []
        self.requests: tuple[Request, ...] = ()
        self.live = False
        self._generation = 0
        self._detail_generation = 0
        self._events: asyncio.subprocess.Process | None = None
        # True from scheduling a tail until it ends, so a refresh burst starts one.
        self._tailing = False
        self._settle: Timer | None = None
        self.agent_setup = agents if isinstance(agents, AgentSetup) else None
        # Why agents are off; None once the desk verified its own Rex block.
        self.agents_off: str | None = agents if isinstance(agents, str) else None
        self.desk: Desk | None = None
        self.agents: list[Agent] = []
        self.agent_states: dict[str, State] = {}
        self._agent_events: asyncio.subprocess.Process | None = None
        self._agent_settle: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("Kata Desk", id="header", markup=False)
        yield Static("", id="status", markup=False)
        with Container(id="body"):
            yield DataTable(id="rows", cursor_type="row")
            with VerticalScroll(id="detail"):
                yield Markdown("", id="markdown", open_links=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).focus()
        self.action_refresh()
        self._open_agents()

    def on_resize(self, event: Resize) -> None:
        self.query_one("#body").set_class(event.size.width < SIDE_BY_SIDE, "stacked")

    async def on_unmount(self) -> None:
        await self._stop_events()
        process, self._agent_events = self._agent_events, None
        if process is not None:
            await self._terminate(process)

    # Reading

    def _read(self, status: str) -> Snapshot:
        # Locate first on every read: a stopped daemon is reported, never started.
        daemon = self.kata.locate()
        recipient = self.kata.recipient()
        listing: Listing | None = None
        unbound: str | None = None
        try:
            listing = self.kata.issues(status)
        except KataError as error:
            if error.state != "unbound":
                raise
            unbound = str(error)
        requests = self.kata.inbox(recipient)
        return Snapshot(
            daemon,
            recipient,
            listing,
            unbound,
            requests,
            datetime.now().strftime("%H:%M:%S"),
        )

    def action_refresh(self) -> None:
        self._generation += 1
        generation, status = self._generation, self.status_filter
        self.state = "loading"
        self._render()
        if self.desk is not None:
            self._schedule_agents()

        async def fetch() -> None:
            try:
                snapshot = await asyncio.to_thread(self._read, status)
            except KataError as error:
                if generation == self._generation:
                    await self._fail(error)
                return
            if generation != self._generation:
                return
            self.snapshot = snapshot
            self.rows = tree(snapshot.listing.issues) if snapshot.listing else []
            self.requests = snapshot.requests
            self.state = "ready"
            self.notice = ""
            if snapshot.unbound is not None:
                self.mode = "attention"
            elif not self._tailing:
                self._tailing = True
                self.run_worker(self._tail(), group="events", exclusive=True)
            self._render()

        self.run_worker(fetch(), group="refresh", exclusive=True)

    async def _fail(self, error: KataError) -> None:
        self.state = error.state
        self.notice = str(error)
        if error.state == "stopped":
            self.notice += " · Start it with kata daemon start, then press r."
            await self._stop_events()
        self._render()

    async def _tail(self) -> None:
        try:
            await self._follow()
        finally:
            self._tailing = False

    async def _follow(self) -> None:
        # Same process group as the pane: Rex's hangup on close reaches it too.
        try:
            process = await asyncio.create_subprocess_exec(
                *self.kata.events_argv(),
                cwd=self.kata.cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=LINE_LIMIT,
            )
        except OSError as error:
            self.notice = f"Live updates unavailable: {error}"
            self._render()
            return
        self._events = process
        self.live = True
        self._render()
        assert process.stdout is not None
        errors = asyncio.create_task(self._watch_stream_errors(process))
        try:
            async for raw in process.stdout:
                if event_kind(raw.decode(errors="replace")) is not None:
                    self.live = True
                    self._schedule_refresh()
        except ValueError:
            self.notice = "A kata event was too large to read; press r to refresh."
        finally:
            errors.cancel()
            self.live = False
            if self._events is process:
                self._events = None
                await self._terminate(process)
                if self.is_running and self.state == "ready":
                    self.notice = (
                        self.notice or "Live updates stopped; press r to refresh."
                    )
                    self._render()

    async def _watch_stream_errors(self, process: asyncio.subprocess.Process) -> None:
        # kata events never exits when its daemon stops; it retries and says so on
        # stderr. Stop claiming live, and re-probe: a stopped daemon ends the stream.
        assert process.stderr is not None
        while True:
            try:
                line = await process.stderr.readline()
            except ValueError:
                line = b"oversized"
            if not line:
                return
            if self.live:
                self.live = False
                self._render()
            self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._settle is not None:
            self._settle.stop()
        self._settle = self.set_timer(SETTLE, self.action_refresh)

    async def _stop_events(self) -> None:
        process, self._events = self._events, None
        self.live = False
        if process is not None:
            await self._terminate(process)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    # Rendering

    def _render(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            header = "Kata Desk"
        else:
            project = (
                "no kata project bound here"
                if snapshot.unbound is not None
                else (self.rows[0][1].project if self.rows else "bound project")
            )
            header = (
                f"Kata Desk · {project} · daemon {snapshot.daemon.kind}"
                f" · attention for {snapshot.recipient}"
            )
        if self.agents:
            waiting = sum(1 for agent in self.agents if self._waiting(agent))
            header += f" · agents {len(self.agents)}"
            header += f", {waiting} waiting" if waiting else ""
        elif self.agents_off is not None:
            header += " · agents off"
        self.query_one("#header", Static).update(header)
        freshness = (
            "live"
            if self.live and self.state == "ready"
            else f"as of {snapshot.clock}"
            if snapshot
            else ""
        )
        view = (
            f"Issues ({self.status_filter})" if self.mode == "issues" else "Attention"
        )
        parts = [self.state.upper(), view]
        if freshness:
            parts.append(freshness)
        if snapshot and snapshot.listing and snapshot.listing.truncated:
            parts.append("list truncated by kata")
        if snapshot and snapshot.unbound is not None and self.mode == "attention":
            parts.append("Run kata init here to bind a project")
        if self.notice:
            parts.append(self.notice)
        self.query_one("#status", Static).update(" · ".join(parts))
        self._fill()

    def _fill(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        if self.mode == "issues":
            table.add_columns("ID", "P", "Status", "Title", "Owner", "Updated", "Agent")
            for index, (depth, issue) in enumerate(self.rows):
                title = ("  " * (depth - 1) + "└ " if depth else "") + issue.title
                status = issue.status + (" · blocked" if issue.blocked else "")
                agent = self._agent_for(issue.ref)
                table.add_row(
                    Text(issue.short),
                    Text("—" if issue.priority is None else f"P{issue.priority}"),
                    Text(status),
                    Text(title),
                    Text(issue.owner or "—"),
                    Text(issue.updated),
                    Text("" if agent is None else self._state(agent).badge),
                    key=str(index),
                )
            count = len(self.rows)
        else:
            table.add_columns("Issue", "From", "Message", "Title")
            for index, request in enumerate(self.requests):
                table.add_row(
                    Text(request.ref),
                    Text(request.sender or "—"),
                    Text(request.message.splitlines()[0] if request.message else ""),
                    Text(request.title),
                    key=str(index),
                )
            count = len(self.requests)
        if count:
            table.move_cursor(row=min(max(row, 0), count - 1))
        elif self.state == "ready":
            empty = (
                f"No {self.status_filter} issues."
                if self.mode == "issues"
                else "No requests."
            )
            self.query_one("#markdown", Markdown).update(f"_{empty}_")

    # Selection and detail

    def _selected_ref(self) -> str | None:
        index = self.query_one(DataTable).cursor_row
        if self.mode == "issues" and 0 <= index < len(self.rows):
            return self.rows[index][1].ref
        if self.mode == "attention" and 0 <= index < len(self.requests):
            return self.requests[index].ref
        return None

    @on(DataTable.RowHighlighted)
    def _highlighted(self) -> None:
        self._open_detail()

    @on(DataTable.RowSelected)
    def _selected(self) -> None:
        self._open_detail()

    def _open_detail(self) -> None:
        # kata show would auto-start a stopped daemon: never read one without a fresh
        # locate answer, and treat a stopped answer as the desk's own state.
        ref = self._selected_ref()
        if ref is None or self.state != "ready":
            return
        self._detail_generation += 1
        generation = self._detail_generation

        async def load() -> None:
            try:
                await asyncio.to_thread(self.kata.locate)
            except KataError as error:
                if generation == self._detail_generation:
                    await self._fail(error)
                return
            try:
                detail = await asyncio.to_thread(self.kata.detail, ref)
            except KataError as error:
                if generation == self._detail_generation:
                    self.query_one("#markdown", Markdown).update(
                        f"_Cannot read {ref}: {error}_"
                    )
                return
            if generation == self._detail_generation:
                await self.query_one("#markdown", Markdown).update(_markdown(detail))

        self.run_worker(load(), group="detail", exclusive=True)

    # Actions

    def action_issues(self) -> None:
        if self.snapshot is not None and self.snapshot.unbound is not None:
            self.notice = "No kata project is bound here; Attention works without one."
            self._render()
            return
        self.mode = "issues"
        self._render()

    def action_attention(self) -> None:
        self.mode = "attention"
        self._render()

    def action_status_filter(self) -> None:
        if self.mode != "issues":
            return
        self.status_filter = "closed" if self.status_filter == "open" else "open"
        self.action_refresh()

    # Agents

    def _state(self, agent: Agent) -> State:
        return self.agent_states.get(agent.block_id, State("reading"))

    def _waiting(self, agent: Agent) -> bool:
        state = self._state(agent)
        return state.running and state.waiting_since is not None

    def _agent_for(self, ref: str) -> Agent | None:
        return max(
            (agent for agent in self.agents if agent.ref == ref),
            key=lambda agent: agent.started,
            default=None,
        )

    def _refuse(self, reason: str) -> None:
        self.notice = reason
        self._render()

    def _open_agents(self) -> None:
        setup = self.agent_setup
        if setup is None:
            return

        async def verify() -> None:
            try:
                desk = await asyncio.to_thread(setup.rex.desk)
            except (OSError, RuntimeError) as error:
                self.agents_off = f"Agents unavailable: {error}"
                self._render()
                return
            self.desk = desk
            self.run_worker(self._follow_agents(desk), group="agent-events")
            await self._read_agents()

        self.run_worker(verify(), group="agent-open", exclusive=True)

    def _schedule_agents(self) -> None:
        if self._agent_settle is not None:
            self._agent_settle.stop()
        self._agent_settle = self.set_timer(
            SETTLE,
            lambda: self.run_worker(
                self._read_agents(), group="agent-read", exclusive=True
            ),
        )

    async def _read_agents(self) -> None:
        """Relist this endpoint's agents with Rex's state; forget those whose block is gone."""
        setup = self.agent_setup
        if setup is None or self.desk is None:
            return

        def read() -> tuple[list[Agent], dict[str, State]]:
            agents = [a for a in setup.store.load() if a.endpoint == setup.endpoint]
            states = setup.rex.states(agents)
            gone = {block for block, state in states.items() if state.gone}
            if gone:
                setup.store.update(
                    lambda rows: [a for a in rows if a.block_id not in gone]
                )
                for agent in agents:
                    if agent.block_id in gone:
                        setup.store.forget_prompt(agent)
            kept = [agent for agent in agents if agent.block_id not in gone]
            return kept, {block: states[block] for block in states if block not in gone}

        try:
            self.agents, self.agent_states = await asyncio.to_thread(read)
        except (OSError, RuntimeError) as error:
            self.notice = f"Agents: {error}"
        self._render()

    async def _follow_agents(self, desk: Desk) -> None:
        # Same process group as the pane: Rex's hangup on close reaches it too.
        setup = self.agent_setup
        if setup is None:
            return
        try:
            process = await asyncio.create_subprocess_exec(
                *setup.rex.events_argv(desk),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=LINE_LIMIT,
            )
        except (OSError, RuntimeError) as error:
            self._refuse(f"Agent updates unavailable: {error}")
            return
        self._agent_events = process
        assert process.stdout is not None
        try:
            async for raw in process.stdout:
                block = event_block(raw.decode(errors="replace"))
                if block is not None and any(a.block_id == block for a in self.agents):
                    self._schedule_agents()
        except ValueError:
            pass
        finally:
            if self._agent_events is process:
                self._agent_events = None
                await self._terminate(process)
                if self.is_running:
                    self._refuse(
                        self.notice or "Agent updates stopped; press r to refresh."
                    )

    def _selected_issue(self) -> Issue | None:
        index = self.query_one(DataTable).cursor_row
        if self.mode == "issues" and 0 <= index < len(self.rows):
            return self.rows[index][1]
        return None

    def action_agent(self) -> None:
        if self.agent_setup is None or self.desk is None:
            self._refuse(self.agents_off or "Agents are still starting; try again")
            return
        issue = self._selected_issue()
        if issue is None:
            self._refuse("Select an issue in Issues (1) to start an agent on it")
            return
        if issue.status != "open":
            self._refuse(f"{issue.ref} is {issue.status}; agents start on open issues")
            return
        current = self._agent_for(issue.ref)
        if current is not None and self._state(current).running:
            self._refuse(f"An agent already works on {issue.ref}; g focuses it")
            return

        def chosen(placement: str | None) -> None:
            if placement is not None:
                self._start(issue, placement)

        self.push_screen(
            Choice(
                f"Start an omp agent on {issue.ref}: {issue.title}",
                {
                    "s": ("split", "split beside the desk"),
                    "w": ("window", "new window"),
                    "b": ("background", "background window"),
                },
            ),
            chosen,
        )

    def _start(self, issue: Issue, placement: str) -> None:
        setup, desk = self.agent_setup, self.desk
        if setup is None or desk is None:
            return
        anchor = next(
            (
                agent
                for agent in self.agents
                if agent.placement == "background" and self._state(agent).running
            ),
            None,
        )
        self._refuse(f"Starting an agent on {issue.ref}…")

        def start() -> Agent:
            # A fresh locate first: kata show would start a stopped daemon.
            self.kata.locate()
            current = self.kata.detail(issue.ref).issue
            if current.status != "open":
                raise RuntimeError(f"{issue.ref} is {current.status} now")
            setup.rex.require_current(desk)
            prompt = setup.store.prompt(
                issue.ref, prompt_text(issue.ref, current.title)
            )
            command = [
                sys.executable,
                "-I",
                "-m",
                "rexdeck.katadesk.agent",
                setup.omp,
                issue.ref,
                str(prompt),
            ]
            # The agent runs kata and omp under the Rex server's minimal PATH.
            prepend = list(
                dict.fromkeys(os.path.dirname(p) for p in (setup.omp, self.kata.path))
            )
            try:
                block, window, host = setup.rex.start(
                    desk,
                    placement,
                    label=f"kata {issue.ref}",
                    cwd=str(self.kata.cwd),
                    command=command,
                    path_prepend=prepend,
                    background=anchor,
                )
            except (OSError, RuntimeError):
                prompt.unlink(missing_ok=True)
                raise
            agent = Agent(
                block_id=block,
                session_id=desk.session_id,
                host_id=host,
                instance_id=desk.instance_id,
                endpoint=setup.endpoint,
                ref=issue.ref,
                title=current.title,
                placement=placement,
                window_id=window,
                prompt=str(prompt),
                started=time.time(),
            )
            try:
                setup.store.update(lambda rows: [*rows, agent])
            except (OSError, RuntimeError):
                # Never leave an agent running that the desk cannot list.
                setup.rex.stop(agent)
                prompt.unlink(missing_ok=True)
                raise
            return agent

        async def run() -> None:
            try:
                agent = await asyncio.to_thread(start)
            except (OSError, RuntimeError, KataError) as error:
                self._refuse(f"No agent started: {error}")
                return
            self.agents.append(agent)
            self._refuse(f"Agent started on {agent.ref} ({agent.placement})")
            self._schedule_agents()

        self.run_worker(run(), group="agent-start", exclusive=True)

    def _focus(self, agent: Agent) -> None:
        setup = self.agent_setup
        if setup is None:
            return

        async def run() -> None:
            try:
                await asyncio.to_thread(setup.rex.focus, agent)
            except (OSError, RuntimeError) as error:
                self._refuse(f"{agent.ref}: {error}")
                return
            self._refuse(f"Focused the agent on {agent.ref}")

        self.run_worker(run(), group="agent-focus", exclusive=True)

    def _selected_agent(self) -> Agent | None:
        ref = self._selected_ref()
        agent = None if ref is None else self._agent_for(ref)
        if agent is None:
            self._refuse("No agent on the selected issue; a starts one")
        return agent

    def action_focus_agent(self) -> None:
        agent = self._selected_agent()
        if agent is not None:
            self._focus(agent)

    def action_next_waiting(self) -> None:
        waiting = [agent for agent in self.agents if self._waiting(agent)]
        if not waiting:
            self._refuse("No agent is waiting for you")
            return
        latest = max(
            waiting, key=lambda agent: moment(self._state(agent).waiting_since or "")
        )
        self._focus(latest)

    def action_stop_agent(self) -> None:
        setup = self.agent_setup
        agent = self._selected_agent()
        if setup is None or agent is None:
            return
        if self._state(agent).replaced:
            # Its block belonged to an earlier Rex server: nothing there to close.
            setup.store.update(
                lambda rows: [a for a in rows if a.block_id != agent.block_id]
            )
            self.agents = [a for a in self.agents if a.block_id != agent.block_id]
            self._refuse(
                f"The agent on {agent.ref} ran on a replaced Rex server; nothing "
                "closed, record removed"
            )
            return

        async def stop() -> None:
            try:
                await asyncio.to_thread(setup.rex.stop, agent)
            except (OSError, RuntimeError) as error:
                self._refuse(f"{agent.ref}: {error}")
                return
            self._refuse(f"Stopped the agent on {agent.ref}")
            await self._read_agents()

        def chosen(answer: str | None) -> None:
            if answer == "stop":
                self.run_worker(stop(), group="agent-stop", exclusive=True)

        self.push_screen(
            Choice(
                f"Stop the agent on {agent.ref}? This closes its {agent.placement} "
                "block and ends omp there; every other block stays.",
                {"y": ("stop", "stop it"), "n": ("keep", "keep it")},
            ),
            chosen,
        )

    async def action_quit(self) -> None:
        running = [agent for agent in self.agents if self._state(agent).running]
        if not running:
            self.exit()
            return
        lines = "\n".join(
            f"  {agent.ref} · {agent.placement} · {self._state(agent).badge}"
            for agent in running
        )

        def chosen(answer: str | None) -> None:
            if answer == "quit":
                self.exit()

        self.push_screen(
            Choice(
                f"These agents keep running after Kata Desk quits:\n{lines}\n\n"
                "Stop one with x first, or close its block in Rex. Reopening the "
                "desk lists them again.",
                {"y": ("quit", "quit, leave them running"), "n": ("stay", "stay")},
            ),
            chosen,
        )

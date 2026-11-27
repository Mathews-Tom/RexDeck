"""Kata Desk: a bound kata project's issues and your attention requests, read-only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Markdown, Static

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
    ]

    def __init__(self, kata: Kata) -> None:
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

    def on_resize(self, event: Resize) -> None:
        self.query_one("#body").set_class(event.size.width < SIDE_BY_SIDE, "stacked")

    async def on_unmount(self) -> None:
        await self._stop_events()

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
            table.add_columns("ID", "P", "Status", "Title", "Owner", "Updated")
            for index, (depth, issue) in enumerate(self.rows):
                title = ("  " * (depth - 1) + "└ " if depth else "") + issue.title
                status = issue.status + (" · blocked" if issue.blocked else "")
                table.add_row(
                    Text(issue.short),
                    Text("—" if issue.priority is None else f"P{issue.priority}"),
                    Text(status),
                    Text(title),
                    Text(issue.owner or "—"),
                    Text(issue.updated),
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

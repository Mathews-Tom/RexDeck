"""Explicit-refresh workflow runs, jobs and in-memory job log tails."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Literal

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Input, RichLog, Static

from rexdeck.actions.api import (
    ActionsError,
    Client,
    Job,
    Run,
    RunFilter,
    RunsPage,
    clean,
)

Data = RunsPage | tuple[Job, ...] | str
View = Literal["runs", "jobs", "logs"]


def _line(text: str) -> str:
    """Sanitize a value that must stay on one display line (logs keep newlines)."""
    return " ".join(clean(text).split())


@dataclass(frozen=True)
class Snapshot:
    data: Data
    fetched: float
    clock: str


class ActionsApp(App[None]):
    TITLE = "RexDeck Actions"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #identity, #location, #status { height: auto; padding: 0 1; }
    #status { color: $accent; min-height: 2; }
    #filters { height: auto; }
    Input { width: 1fr; }
    Button { min-width: 10; }
    DataTable, RichLog { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "back", "Back"),
        Binding("o", "open", "Open run"),
        Binding("b", "branch", "Branch"),
        Binding("s", "sha", "Commit"),
        Binding("n", "next", "Next page"),
        Binding("p", "previous", "Previous page"),
    ]

    def __init__(self, client: Client, selected: RunFilter) -> None:
        super().__init__()
        self.client = client
        self.selected = selected
        self.page = 1
        self.view: View = "runs"
        self.selected_run: Run | None = None
        self.job: Job | None = None
        self.state = "loading"
        self.notice = ""
        self.cache: dict[tuple[object, ...], Snapshot] = {}
        self._generation = 0

    def compose(self) -> ComposeResult:
        ctx = self.client.context
        yield Static(
            f"Actions · {ctx.host}/{ctx.owner}/{ctx.repo} · account {ctx.account}",
            id="identity",
            markup=False,
        )
        with Horizontal(id="filters"):
            yield Input(
                self.selected.value if self.selected.kind == "branch" else "",
                placeholder="Branch (b)",
                id="branch",
            )
            yield Input(
                self.selected.value if self.selected.kind == "head_sha" else "",
                placeholder="Commit SHA (s)",
                id="sha",
            )
            yield Button("Apply", id="apply")
        yield Static("", id="location", markup=False)
        yield Static("", id="status", markup=False)
        yield DataTable(id="items", cursor_type="row")
        yield RichLog(
            id="logs", markup=False, highlight=False, wrap=True, auto_scroll=False
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#items", DataTable).focus()
        self.action_refresh()

    def _key(self) -> tuple[object, ...]:
        if self.view == "runs":
            return (self.view, self.selected, self.page)
        if self.view == "jobs":
            return (self.view, self.selected_run.id if self.selected_run else None)
        return (self.view, self.job.id if self.job else None)

    def _render(self) -> None:
        snap = self.cache.get(self._key())
        self.query_one("#filters").display = self.view == "runs"
        location = (
            f"{self.selected.kind}={_line(self.selected.value)} · page {self.page}"
        )
        if self.selected_run and self.view != "runs":
            location = f"Run {self.selected_run.id} · {_line(self.selected_run.name)}"
        if self.job and self.view == "logs":
            location += f" / {_line(self.job.name)} · last 200 log lines (memory only)"
        if self.view == "runs" and snap and isinstance(snap.data, RunsPage):
            location += f" · {snap.data.total} matches (GitHub search limit: 1,000)"
        self.query_one("#location", Static).update(location)
        status = self.state.upper()
        if snap:
            status += f" · updated {snap.clock}"
            if self.state == "stale":
                status += f" · last success {int(monotonic() - snap.fetched)}s ago"
        if self.notice:
            status += f" · {self.notice}"
        self.query_one("#status", Static).update(status)
        table = self.query_one("#items", DataTable)
        logs = self.query_one("#logs", RichLog)
        table.display = self.view != "logs"
        logs.display = self.view == "logs"
        row = table.cursor_row
        table.clear(columns=True)
        logs.clear()
        if not snap:
            return
        if isinstance(snap.data, RunsPage):
            table.add_columns("Run", "Workflow", "State", "Branch", "Commit")
            for run in snap.data.runs:
                table.add_row(
                    str(run.id),
                    Text(_line(run.name)),
                    Text(_line(run.conclusion or run.status)),
                    Text(_line(run.branch)),
                    Text(_line(run.sha[:10])),
                    key=str(run.id),
                )
        elif isinstance(snap.data, tuple):
            table.add_columns("Job", "Name", "State")
            for job in snap.data:
                table.add_row(
                    str(job.id),
                    Text(_line(job.name)),
                    Text(_line(job.conclusion or job.status)),
                    key=str(job.id),
                )
        else:
            logs.write(snap.data)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))

    def action_refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        key, view, selected, page, run, job = (
            self._key(),
            self.view,
            self.selected,
            self.page,
            self.selected_run,
            self.job,
        )
        self.state, self.notice = "loading", "Reading GitHub; no background polling."
        self._render()

        async def fetch() -> None:
            try:
                await asyncio.to_thread(self.client.verify_account)
                data: Data
                if view == "runs":
                    data = await asyncio.to_thread(self.client.runs, selected, page)
                elif view == "jobs" and run:
                    data = await asyncio.to_thread(self.client.jobs, run.id)
                elif view == "logs" and job:
                    data = await asyncio.to_thread(self.client.logs, job.id)
                else:
                    return
                if generation != self._generation:
                    return
                self.cache[key] = Snapshot(
                    data, monotonic(), datetime.now().strftime("%H:%M:%S")
                )
                empty = not data.runs if isinstance(data, RunsPage) else not data
                self.state = "empty" if empty else "ready"
                self.notice = (
                    "No results. Change filter or press r to refresh."
                    if empty
                    else "Enter reads selection · o opens run · r refreshes"
                )
            except ActionsError as error:
                if generation != self._generation:
                    return
                self.state = "stale" if key in self.cache else error.state
                self.notice = f"{error.state}: {error}"
            self._render()

        self.run_worker(fetch(), group="refresh", exclusive=True, exit_on_error=False)

    @on(Input.Submitted)
    @on(Button.Pressed, "#apply")
    def apply_filter(self) -> None:
        branch = self.query_one("#branch", Input).value.strip()
        sha = self.query_one("#sha", Input).value.strip()
        try:
            if bool(branch) == bool(sha):
                raise ValueError(
                    "Fill exactly one branch or commit SHA; clear the other field."
                )
            self.selected = RunFilter("branch" if branch else "head_sha", branch or sha)
        except ValueError as error:
            self.notice = str(error)
            self._render()
            return
        self.page = 1
        self.selected_run = self.job = None
        self.query_one("#items", DataTable).focus()
        self.action_refresh()

    def action_branch(self) -> None:
        if self.view == "runs":
            self.query_one("#sha", Input).value = ""
            self.query_one("#branch", Input).focus()

    def action_sha(self) -> None:
        if self.view == "runs":
            self.query_one("#branch", Input).value = ""
            self.query_one("#sha", Input).focus()

    @on(DataTable.RowSelected)
    def read_selected(self, event: DataTable.RowSelected) -> None:
        if self.state not in ("ready", "stale"):
            return
        snap = self.cache.get(self._key())
        if not snap:
            return
        if isinstance(snap.data, RunsPage):
            self.selected_run = next(
                (r for r in snap.data.runs if str(r.id) == event.row_key.value), None
            )
            if not self.selected_run:
                return
            self.view = "jobs"
        elif isinstance(snap.data, tuple):
            self.job = next(
                (j for j in snap.data if str(j.id) == event.row_key.value), None
            )
            if not self.job:
                return
            self.view = "logs"
        else:
            return
        self.action_refresh()
        if self.view == "logs":
            self.query_one("#logs", RichLog).focus()

    async def action_back(self) -> None:
        if self.view == "logs":
            self.view = "jobs"
            self.job = None
        elif self.view == "jobs":
            self.view = "runs"
            self.selected_run = None
        else:
            self.query_one("#items", DataTable).focus()
            return
        self.action_refresh()
        self.query_one("#items", DataTable).focus()

    def action_next(self) -> None:
        snap = self.cache.get(self._key())
        if (
            self.view == "runs"
            and self.state in ("ready", "stale")
            and snap
            and isinstance(snap.data, RunsPage)
            and snap.data.has_next
        ):
            self.page += 1
            self.action_refresh()

    def action_previous(self) -> None:
        if self.view == "runs" and self.page > 1:
            self.page -= 1
            self.action_refresh()

    def action_open(self) -> None:
        run = self.selected_run
        snap = self.cache.get(self._key())
        if self.view == "runs" and snap and isinstance(snap.data, RunsPage):
            row = self.query_one("#items", DataTable).cursor_row
            run = snap.data.runs[row] if row < len(snap.data.runs) else None
        if self.state != "ready" or run is None:
            self.notice = "Browser open needs a fresh selected run; press r to refresh."
            self._render()
            return

        async def open_selected() -> None:
            try:
                await asyncio.to_thread(self.client.open_run, run)
                self.notice = "Opened the selected run's exact URL."
            except ActionsError as error:
                self.notice = str(error)
            self._render()

        self.run_worker(
            open_selected(), group="browser", exclusive=True, exit_on_error=False
        )

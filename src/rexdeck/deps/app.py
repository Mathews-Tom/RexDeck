"""Explicit package entry, in-memory snapshots and user-triggered refresh only."""

from __future__ import annotations

import asyncio
import math

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Input, Static

from rexdeck.actions.api import clean
from rexdeck.deps.api import NO_ADVISORIES, Client, Coordinates, DepsError, Snapshot

PAGE_SIZE = 50


def report(snapshot: Snapshot, page: int) -> str:
    package = snapshot.package
    pages = max(1, math.ceil(len(snapshot.dependencies) / PAGE_SIZE))
    lines = [
        f"{package.system} {package.name} @ {package.version}",
        f"Fetched-at {snapshot.fetched_at}",
        "Sources: deps.dev + OSV (package version only)",
        "Licenses (deps.dev): " + (", ".join(snapshot.licenses) or "Not provided"),
        f"Dependencies (deps.dev) · page {page}/{pages} · n/p to page",
    ]
    if snapshot.graph_notice:
        lines.append(snapshot.graph_notice)
    start = (page - 1) * PAGE_SIZE
    for dep in snapshot.dependencies[start : start + PAGE_SIZE]:
        lines.append(f"{dep.relation}: {dep.package.name} @ {dep.package.version}")
    if not snapshot.dependencies and not snapshot.graph_notice:
        lines.append("No dependencies reported by deps.dev.")
    lines.append("Advisories for this package version (not a graph-wide audit):")
    for advisory in snapshot.advisories:
        lines.extend(
            [
                f"{advisory.id} [{advisory.source}]",
                advisory.summary,
                "Severity: " + advisory.severity,
            ]
        )
    if not snapshot.advisories:
        lines.append(NO_ADVISORIES)
    return clean("\n".join(lines))


class DepsApp(App[None]):
    TITLE = "RexDeck Dependencies"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #identity, #status { height: auto; padding: 0 1; }
    #status { color: $accent; }
    Input { height: 3; }
    #buttons { height: 3; }
    Button { width: 1fr; min-width: 8; }
    #report { height: 1fr; }
    #content { height: auto; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open", "Open page"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("escape", "browse", "Browse"),
        Binding("e", "edit", "Edit"),
    ]

    def __init__(self, client: Client | None = None) -> None:
        super().__init__()
        self.client = client or Client()
        self.selected: Coordinates | None = None
        self.snapshot: Snapshot | None = None
        self.state = "empty"
        self.notice = "Enter system, package and optional version. Enter fetches."
        self.page = 1
        self._generation = 0

    def compose(self) -> ComposeResult:
        yield Static(
            "Dependencies · coordinates sent to deps.dev/OSV",
            id="identity",
            markup=False,
        )
        yield Input(
            value="pypi",
            placeholder="System: npm/pypi/go/maven/cargo/nuget/rubygems",
            id="system",
        )
        yield Input(placeholder="Package name", id="name")
        yield Input(placeholder="Version (blank = deps.dev default)", id="version")
        with Horizontal(id="buttons"):
            yield Button("Fetch", id="fetch")
            yield Button("Refresh", id="refresh")
        yield Static("", id="status", markup=False)
        with VerticalScroll(id="report"):
            yield Static("", id="content", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._render()
        self.query_one("#name", Input).focus()

    def _render(self) -> None:
        self.query_one("#status", Static).update(
            f"{self.state.upper()} · {self.notice}"
        )
        self.query_one("#content", Static).update(
            report(self.snapshot, self.page) if self.snapshot else ""
        )

    @on(Input.Submitted)
    @on(Button.Pressed, "#fetch")
    def apply(self) -> None:
        try:
            selected = Coordinates(
                self.query_one("#system", Input).value.strip().lower(),
                self.query_one("#name", Input).value.strip(),
                self.query_one("#version", Input).value.strip(),
            )
        except ValueError as error:
            self.notice = str(error)
            self.state = "error"
            self._render()
            return
        if self.selected != selected:
            self.snapshot = None
            self.page = 1
        self.selected = selected
        self.action_browse()
        self.action_refresh()

    @on(Button.Pressed, "#refresh")
    def refresh_button(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        if self.selected is None:
            self.apply()
            return
        self._generation += 1
        generation = self._generation
        selected = self.selected
        self.state = "loading"
        self.notice = "Fetching deps.dev + OSV; no background polling."
        self._render()

        async def fetch() -> None:
            try:
                snapshot = await asyncio.to_thread(
                    self.client.fetch,
                    selected,
                    cancelled=lambda: generation != self._generation,
                )
                if generation != self._generation:
                    return
                self.snapshot = snapshot
                self.page = min(
                    self.page, max(1, math.ceil(len(snapshot.dependencies) / PAGE_SIZE))
                )
                self.state = "ready"
                self.notice = "r refresh · o opens exact version · e edits · scroll for advisories"
            except DepsError as error:
                if generation != self._generation:
                    return
                self.state = "stale" if self.snapshot else error.state
                self.notice = f"{error.state}: {error}"
            self._render()

        self.run_worker(fetch(), group="refresh", exclusive=True)

    def on_unmount(self) -> None:
        self._generation += 1

    def action_browse(self) -> None:
        self.query_one("#report", VerticalScroll).focus()

    def action_edit(self) -> None:
        self.query_one("#name", Input).focus()

    def action_next(self) -> None:
        if self.snapshot and self.page * PAGE_SIZE < len(self.snapshot.dependencies):
            self.page += 1
            self._render()

    def action_previous(self) -> None:
        if self.page > 1:
            self.page -= 1
            self._render()

    def action_open(self) -> None:
        if not self.snapshot:
            self.notice = "Fetch a package version before opening its page."
        else:
            try:
                self.client.open_package(self.snapshot.package)
                self.notice = "Opened exact deps.dev package version."
            except DepsError as error:
                self.notice = str(error)
        self._render()

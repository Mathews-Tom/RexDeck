"""Explicit image entry and refresh, with memory-only anonymous pull tokens."""

from __future__ import annotations

import asyncio

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Input, Static

from rexdeck.actions.api import clean
from rexdeck.images.api import Client, ImagesError, Manifest, Reference, TagPage


def report(reference: Reference, page: TagPage, manifest: Manifest | None) -> str:
    lines = [
        reference.normalized,
        f"Source: https://{reference.host} (OCI distribution)",
        f"Tags fetched-at {page.fetched_at}",
    ]
    if manifest:
        lines.extend(
            [
                f"Tag: {manifest.tag}",
                f"Manifest fetched-at {manifest.fetched_at}",
                f"Digest: {manifest.digest}",
                f"Media type: {manifest.media_type}",
                "Platforms: "
                + (
                    ", ".join(manifest.platforms)
                    or "Not declared in this manifest; config blobs are not fetched."
                ),
            ]
        )
    else:
        lines.append("Select a tag with Enter to read its digest and platforms.")
    return clean("\n".join(lines))


class ImagesApp(App[None]):
    TITLE = "RexDeck Images"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #identity, #status { height: auto; padding: 0 1; }
    #status { color: $accent; }
    Input { height: 3; }
    #buttons { height: 3; }
    Button { width: 1fr; min-width: 8; }
    #tags { height: 6; }
    #report { height: 1fr; }
    #content { height: auto; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open", "Open page"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("e", "edit", "Edit"),
        Binding("escape", "browse", "Browse"),
    ]

    def __init__(self, client: Client | None = None) -> None:
        super().__init__()
        self.client = client or Client()
        self.reference: Reference | None = None
        self.page: TagPage | None = None
        self.manifest: Manifest | None = None
        self.tag: str | None = None
        self.cursors = [""]
        self.page_number = 0
        self.state = "empty"
        self.notice = "Enter registry/repository, e.g. nginx or ghcr.io/owner/image."
        self._generation = 0

    def compose(self) -> ComposeResult:
        yield Static(
            "Images · references leave the machine. Metadata only; not a vulnerability or signature verdict. Anonymous/public only; no polling.",
            id="identity",
            markup=False,
        )
        yield Input(
            placeholder="registry/repository (default docker.io)", id="reference"
        )
        with Horizontal(id="buttons"):
            yield Button("Fetch", id="fetch")
            yield Button("Refresh", id="refresh")
        yield Static("", id="status", markup=False)
        yield DataTable(id="tags", cursor_type="row")
        with VerticalScroll(id="report"):
            yield Static("", id="content", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#tags", DataTable).add_column("Tags (Enter selects; n/p pages)")
        self._render()
        self.action_edit()

    def _render(self) -> None:
        self.query_one("#status", Static).update(
            f"{self.state.upper()} · {self.notice}"
        )
        self.query_one("#content", Static).update(
            report(self.reference, self.page, self.manifest)
            if self.reference and self.page
            else ""
        )

    @on(Input.Submitted)
    @on(Button.Pressed, "#fetch")
    def apply(self) -> None:
        try:
            reference = Reference.parse(self.query_one("#reference", Input).value)
        except ValueError as error:
            self.state, self.notice = "error", str(error)
            self._render()
            return
        if reference != self.reference:
            self.page, self.manifest, self.tag = None, None, None
            self.cursors, self.page_number = [""], 0
            self.query_one("#tags", DataTable).clear()
        self.reference = reference
        self.action_browse()
        self.action_refresh()

    @on(Button.Pressed, "#refresh")
    def refresh_button(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        if self.reference is None:
            self.apply()
            return
        self._fetch(self.page_number, self.cursors[self.page_number], self.tag)

    def _fetch(
        self, number: int, cursor: str, tag: str | None, detail_only: bool = False
    ) -> None:
        reference = self.reference
        if reference is None:
            return
        self._generation += 1
        generation = self._generation
        self.state, self.notice = (
            "loading",
            f"Reading {reference.normalized}; no blobs or layers.",
        )
        self._render()

        async def fetch() -> None:
            try:
                page = (
                    self.page
                    if detail_only
                    else await asyncio.to_thread(self.client.tags, reference, cursor)
                )
                manifest = (
                    await asyncio.to_thread(self.client.manifest, reference, tag)
                    if tag
                    else None
                )
                if generation != self._generation:
                    return
                self.page, self.manifest, self.tag = page, manifest, tag
                self.page_number = number
                self.cursors = self.cursors[: number + 1]
                if len(self.cursors) <= number:
                    self.cursors.append(cursor)
                table = self.query_one("#tags", DataTable)
                if not detail_only:
                    table.clear()
                    if page:
                        # DataTable strings interpret markup; Rich Text keeps tags literal.
                        from rich.text import Text

                        for value in page.tags:
                            table.add_row(Text(value), key=value)
                self.state = "ready" if page and page.tags else "empty"
                self.notice = (
                    f"Page {number + 1} · r refresh · Enter tag · n/p page · e edit"
                    if page and page.tags
                    else "No tags reported; r retries or e edits."
                )
            except ImagesError as error:
                if generation != self._generation:
                    return
                self.state = "stale" if self.page else error.state
                self.notice = f"{error.state}: {error}"
            self._render()

        self.run_worker(fetch(), group="fetch", exclusive=True)

    @on(DataTable.RowSelected, "#tags")
    def select_tag(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self.tag = str(event.row_key.value)
            self._fetch(
                self.page_number, self.cursors[self.page_number], self.tag, True
            )

    def action_next(self) -> None:
        if self.state == "loading":
            return
        if self.page and self.page.next_cursor:
            self._fetch(self.page_number + 1, self.page.next_cursor, None)

    def action_previous(self) -> None:
        if self.state != "loading" and self.page_number > 0:
            self._fetch(self.page_number - 1, self.cursors[self.page_number - 1], None)

    def action_edit(self) -> None:
        self.query_one("#reference", Input).focus()

    def action_browse(self) -> None:
        self.query_one("#tags", DataTable).focus()

    def action_open(self) -> None:
        try:
            if self.reference is None or self.page is None:
                raise ImagesError(
                    "empty", "Fetch a repository before opening its page."
                )
            self.client.open_reference(self.reference)
            self.notice = "Opened exact upstream image page."
        except ImagesError as error:
            self.notice = str(error)
        self._render()

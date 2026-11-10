"""A personal inbox with explicit refresh, no mutation and no polling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Static

from rexdeck.inbox.api import (
    REASONS,
    Client,
    InboxError,
    Notification,
    Page,
    clean,
    filter_reason,
)


@dataclass(frozen=True)
class Snapshot:
    page: Page
    fetched: float
    clock: str


class InboxApp(App[None]):
    TITLE = "RexDeck Inbox"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #identity, #filters, #status, #selected { height: auto; padding: 0 1; }
    #status { color: $accent; min-height: 2; }
    #selected { max-height: 6; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open", "Open thread"),
        Binding("f", "reason", "Reason"),
        Binding("t", "participating", "Participating"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
    ]

    def __init__(self, client: Client) -> None:
        super().__init__()
        self.client = client
        self.page = 1
        self.participating = False
        self.reason = "all"
        self.state = "loading"
        self.notice = ""
        self.cache: dict[tuple[int, bool], Snapshot] = {}
        self.items: tuple[Notification, ...] = ()
        self._generation = 0
        self._opening = False
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"Inbox · {clean(self.client.context.host)} · account {clean(self.client.context.account)}",
            id="identity",
            markup=False,
        )
        yield Static("", id="filters", markup=False)
        yield Static("", id="status", markup=False)
        yield DataTable(id="items", cursor_type="row")
        yield Static("", id="selected", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).focus()
        self.action_refresh()

    def _key(self) -> tuple[int, bool]:
        return self.page, self.participating

    def _render(self) -> None:
        snap = self.cache.get(self._key())
        self.items = filter_reason(snap.page.items, self.reason) if snap else ()
        self.query_one("#filters", Static).update(
            f"Unread · page {self.page} · participating={'yes' if self.participating else 'no'}\nReason: {self.reason} (f) · filter applies to this page"
        )
        state = "empty" if self.state == "ready" and not self.items else self.state
        status = state.upper()
        if snap:
            status += (
                f" · refreshed {snap.clock} · {int(monotonic() - snap.fetched)}s ago"
            )
        if self.notice:
            status += " · " + self.notice
        self.query_one("#status", Static).update(status)
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        table.add_columns("Repo", "Type", "Title", "Reason", "Updated")
        for index, item in enumerate(self.items):
            table.add_row(
                *(
                    Text(clean(value))
                    for value in (
                        item.repo,
                        item.kind,
                        item.title,
                        item.reason,
                        item.updated,
                    )
                ),
                key=str(index),
            )
        if self.items:
            table.move_cursor(row=min(row, len(self.items) - 1))
        self._selection()

    def _selection(self) -> None:
        table = self.query_one(DataTable)
        index = table.cursor_row
        text = ""
        if 0 <= index < len(self.items):
            item = self.items[index]
            text = f"{clean(item.repo)} · {clean(item.kind)}\n{clean(item.title)}\n{clean(item.reason)} · updated {clean(item.updated)}"
        self.query_one("#selected", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._selection()

    def action_refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self._generation += 1
        generation, key = self._generation, self._key()
        self.state, self.notice = "loading", "Reading GitHub; no background polling."
        self._render()

        async def fetch() -> None:
            try:
                data = await asyncio.to_thread(self.client.notifications, *key)
                if generation != self._generation:
                    return
                self.cache[key] = Snapshot(
                    data, monotonic(), datetime.now().strftime("%H:%M:%S")
                )
                self.state = "ready" if data.items else "empty"
                self.notice = "r refresh · f reason · t participating · o open"
            except InboxError as error:
                if generation != self._generation:
                    return
                self.state = "stale" if key in self.cache else error.state
                self.notice = f"{error.state}: {error}"
            except Exception:
                self.state = "error"
                self.notice = "Unexpected Inbox error. Press r to retry."
            finally:
                self._refreshing = False
            self._render()

        self.run_worker(fetch(), group="refresh", exclusive=True)

    def action_reason(self) -> None:
        self.reason = REASONS[(REASONS.index(self.reason) + 1) % len(REASONS)]
        self._render()

    def action_participating(self) -> None:
        if self._refreshing:
            return
        self.participating = not self.participating
        self.page = 1
        self.action_refresh()

    def action_next(self) -> None:
        if self._refreshing:
            return
        snap = self.cache.get(self._key())
        if snap and snap.page.has_next:
            self.page += 1
            self.action_refresh()
        else:
            self.notice = "No next page."
            self._render()

    def action_previous(self) -> None:
        if self._refreshing:
            return
        if self.page > 1:
            self.page -= 1
            self.action_refresh()

    def action_open(self) -> None:
        index = self.query_one(DataTable).cursor_row
        if self._opening or not 0 <= index < len(self.items):
            return
        item, key, generation = self.items[index], self._key(), self._generation
        self._opening = True

        async def open_selected() -> None:
            try:
                url = await asyncio.to_thread(self.client.thread_url, item)
                if key != self._key() or generation != self._generation:
                    return
                self.open_url(url)
                self.notice = (
                    "Opened the repository's Actions page; this notification has no exact run URL."
                    if item.kind == "CheckSuite" and item.subject_url is None
                    else "Opened the selected exact GitHub thread URL."
                )
            except InboxError as error:
                self.notice = f"{error.state}: {error}"
            except Exception:
                self.state = "error"
                self.notice = "Unexpected Inbox error while opening. Press r to retry."
            finally:
                self._opening = False
            self._render()

        self.run_worker(open_selected(), group="open")

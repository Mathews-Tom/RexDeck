"""Native, identity-bound ClickUp task desk; the controller owns every mutation."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.geometry import Size
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TextArea,
    Tree,
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from rexdeck.clickup import frontend
from rexdeck.clickup.engine import diagnostics
from rexdeck.clickup.text import clean, single_line

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_CLOSED_TYPES = frozenset({"closed", "done"})
_DESK_ACTIONS = frozenset(
    {
        "filter",
        "lists",
        "actions",
        "refresh",
        "open",
        "mine",
        "closed",
        "back",
        "help",
        "quit",
    }
)
_READS = (
    (
        "sync",
        "Sync registry",
        "Explicitly discover names, fields and sprint configuration",
    ),
    ("tasks", "Task report", "Engine task report for this list"),
    ("task", "Selected task report", "Engine detail and comments for this task"),
    ("sprint", "Sprint report", "Current sprint report"),
    ("search", "Search", "Engine search; enter a query"),
)
_WRITES = (
    ("status", "Change status"),
    ("assignee", "Change assignee"),
    ("due", "Change due date"),
    ("description", "Edit description"),
    ("points", "Change points"),
    ("field", "Change custom field"),
    ("comment", "Add comment"),
)


def date_label(raw: str) -> str:
    """ClickUp dates are epoch milliseconds; malformed dates stay explicitly unknown."""
    if not raw:
        return "Not set"
    try:
        return (
            datetime.fromtimestamp(int(raw) / 1000)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M %Z")
        )
    except (ValueError, OverflowError, OSError):
        return "Unknown date"


def status_text(task: frontend.Task) -> Text:
    # Rich markup is never parsed from API content. Only literal RGB colours pass.
    color = task.status_color if _HEX_COLOR.fullmatch(task.status_color) else ""
    return Text(task.status or "Status unavailable", style=color)


class TaskQueue(DataTable[Text]):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def on_resize(self, event: Resize) -> None:
        columns = self.ordered_columns
        if len(columns) not in {2, 3} or event.size.width <= 0:
            return
        width = max(16, event.size.width - 2)
        status_width = min(16, max(9, width // 4))
        if len(columns) == 3:
            list_width = min(16, max(8, width // 4))
            columns[0].width = max(3, width - status_width - list_width - 6)
            columns[2].width = list_width
        else:
            columns[0].width = max(3, width - status_width - 4)
        columns[1].width = status_width
        self.virtual_size = Size(width, self.virtual_size.height)
        self.refresh(layout=True)


class DeskModal(ModalScreen[None]):
    """A dismissible informational screen with no application-key leakage."""

    BINDINGS = [Binding("escape", "dismiss", show=False)]


class OutputModal(DeskModal):
    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.heading = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.heading, style="bold"), classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Static(Text(clean(self.body)), id="read-output")
            yield Button("Close", id="close-output")

    def on_mount(self) -> None:
        self.query_one("#close-output", Button).focus()

    @on(Button.Pressed, "#close-output")
    def close_output(self) -> None:
        self.dismiss()


class ActionsModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, view: frontend.View) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        fresh = self.view.state in {"ready", "empty"}
        task_fresh = self.view.state == "ready" and self.view.detail is not None
        options: list[Option] = []
        for action, label, hint in _READS:
            options.append(
                Option(
                    Text(f"{label}\n  {hint}"),
                    id=action,
                    disabled=not fresh or (action == "task" and not task_fresh),
                )
            )
        for action, label in _WRITES:
            options.append(
                Option(
                    Text(label + "  (review before applying)"),
                    id=action,
                    disabled=not task_fresh,
                )
            )
        options.extend(
            (
                Option(
                    "Choose list / workspace",
                    id="lists",
                    disabled=self.view.state == "writing",
                ),
                Option(
                    "Space view / server filters",
                    id="workspace",
                    disabled=self.view.state == "writing",
                ),
                Option(
                    "Advanced account / scope switch",
                    id="switch",
                    disabled=self.view.state == "writing",
                ),
                Option(
                    "Last output / write receipt",
                    id="output",
                    disabled=not bool(self.view.output),
                ),
                Option("Keyboard help", id="help"),
            )
        )
        with Vertical(classes="dialog"):
            yield Static("Actions", classes="dialog-title")
            yield Static(
                "Sync is explicit: run it before engine reports or edits to discover the registry. "
                "It updates local metadata, not your tasks.",
                classes="dialog-note",
                markup=False,
            )
            yield OptionList(*options, id="action-options", markup=False)
            yield Button("Cancel", id="cancel-actions")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel-actions")
    def cancel_actions(self) -> None:
        self.dismiss(None)


class ValueModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(
        self,
        title: str,
        hint: str,
        value: str = "",
        *,
        multiline: bool = False,
        submit_label: str = "Review change",
    ) -> None:
        super().__init__()
        self.heading = title
        self.hint = hint
        self.initial = value
        self.multiline = multiline
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog value-dialog"):
            yield Static(Text(self.heading, style="bold"), classes="dialog-title")
            yield Static(Text(self.hint), classes="dialog-note")
            if self.multiline:
                yield TextArea(self.initial, id="value", soft_wrap=True)
            else:
                yield Input(self.initial, id="value")
            yield Static("", id="value-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-value")
                yield Button(self.submit_label, id="submit-value", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#value").focus()

    @on(Input.Submitted, "#value")
    def submit_input(self) -> None:
        self.submit_value()

    @on(Button.Pressed, "#submit-value")
    def submit_value(self) -> None:
        widget = self.query_one("#value")
        value = (
            widget.text
            if isinstance(widget, TextArea)
            else self.query_one("#value", Input).value
        )
        if not value.strip():
            self.query_one("#value-error", Static).update(
                "Enter a value; nothing has been sent."
            )
            return
        self.dismiss(value)

    @on(Button.Pressed, "#cancel-value")
    def cancel_value(self) -> None:
        self.dismiss(None)


class StatusModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, controller: frontend.Controller, task: frontend.Task) -> None:
        super().__init__()
        self.controller = controller
        self.selected_task = task

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Change status", classes="dialog-title")
            yield Static(
                Text(
                    f"Current: {self.selected_task.status}\nReading exact home-list statuses…"
                ),
                id="status-note",
            )
            yield OptionList(id="status-options", markup=False)
            yield Button("Cancel", id="cancel-status")

    def on_mount(self) -> None:
        self.run_worker(self.load_statuses(), exit_on_error=False)
        self.query_one("#cancel-status", Button).focus()

    async def load_statuses(self) -> None:
        try:
            statuses = await asyncio.wrap_future(
                self.controller.read(
                    lambda service: service.task_statuses(self.selected_task),
                    current_scope=True,
                )
            )
        except Exception as error:
            if self.is_mounted:
                self.query_one("#status-note", Static).update(
                    Text(f"Could not read statuses: {clean(str(error))}; no retry.")
                )
            return
        if not self.is_mounted:
            return
        options = self.query_one("#status-options", OptionList)
        for status in statuses:
            color = status.color if _HEX_COLOR.fullmatch(status.color) else ""
            options.add_option(Option(Text(status.name, style=color), id=status.name))
        self.query_one("#status-note", Static).update(
            Text(
                f"Current: {self.selected_task.status}\nChoose a native status from home list {self.selected_task.list_id}."
            )
        )
        options.focus()

    @on(OptionList.OptionSelected)
    def selected_status(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel-status")
    def cancel_status(self) -> None:
        self.dismiss(None)


class WorkspaceModal(
    ModalScreen[tuple[frontend.Selection, bool, frontend.WorkspaceFilters] | None]
):
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, service: frontend.Service) -> None:
        super().__init__()
        self.service = service

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Space view / server filters", classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Static("Mode", markup=False)
                yield Select(
                    [("List (unchanged)", "list"), ("Space", "space")],
                    value="space" if self.service.workspace else "list",
                    allow_blank=False,
                    id="workspace-mode",
                )
                yield Static(
                    "Default space ID (saved; filters reset each launch)", markup=False
                )
                yield Input(self.service.selection.space, id="workspace-space")
                yield Static("Assignee: me, any, or member ID", markup=False)
                yield Input(self.service.filters.assignee, id="workspace-assignee")
                yield Static("Updated within", markup=False)
                yield Select(
                    [(value, value) for value in ("1d", "7d", "14d", "30d", "any")],
                    value=self.service.filters.lookback,
                    allow_blank=False,
                    id="workspace-lookback",
                )
            yield Static("", id="workspace-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-workspace")
                yield Button("Load view", id="load-workspace", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#cancel-workspace", Button).focus()

    @on(Button.Pressed, "#cancel-workspace")
    def cancel_workspace(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#load-workspace")
    def load_workspace(self) -> None:
        try:
            selection = replace(
                self.service.selection,
                space=self.query_one("#workspace-space", Input).value.strip(),
            )
            workspace = self.query_one("#workspace-mode", Select).value == "space"
            if workspace and not selection.space:
                raise ValueError("Enter a space ID")
            filters = frontend.WorkspaceFilters(
                self.query_one("#workspace-assignee", Input).value.strip(),
                str(self.query_one("#workspace-lookback", Select).value),
            )
        except ValueError as error:
            self.query_one("#workspace-error", Static).update(Text(str(error)))
            return
        self.dismiss((selection, workspace, filters))


class ScopeModal(ModalScreen[frontend.Selection | None]):
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, selection: frontend.Selection) -> None:
        super().__init__()
        self.selection = selection

    def compose(self) -> ComposeResult:
        selected = self.selection
        with Vertical(classes="dialog"):
            yield Static("Advanced account / scope switch", classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Static("Explicit account ID", markup=False)
                yield Input(selected.account, id="scope-account")
                yield Static("Workspace ID", markup=False)
                yield Input(selected.team, id="scope-team")
                yield Static("List ID", markup=False)
                yield Input(selected.list, id="scope-list")
                yield Static(
                    "Credential environment variable name (never paste a token)",
                    markup=False,
                )
                yield Input(selected.credential_env, id="scope-env")
                yield Static(
                    "Switch this pane only; the saved selection is unchanged. Credential and membership are verified when loaded.",
                    markup=False,
                )
            yield Static("", id="scope-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-scope")
                yield Button("Switch pane", id="save-scope", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#scope-account", Input).focus()

    @on(Button.Pressed, "#save-scope")
    def save_scope(self) -> None:
        try:
            selected = frontend.Selection(
                self.query_one("#scope-account", Input).value.strip(),
                self.query_one("#scope-team", Input).value.strip(),
                self.query_one("#scope-list", Input).value.strip(),
                self.query_one("#scope-env", Input).value.strip(),
            )
        except ValueError as error:
            self.query_one("#scope-error", Static).update(Text(clean(str(error))))
            return
        self.dismiss(selected)

    @on(Button.Pressed, "#cancel-scope")
    def cancel_scope(self) -> None:
        self.dismiss(None)


class HierarchyModal(ModalScreen[frontend.Selection | None]):
    BINDINGS = [Binding("escape", "cancel_hierarchy", show=False)]

    def __init__(
        self, controller: frontend.Controller, selection: frontend.Selection
    ) -> None:
        super().__init__()
        self.controller = controller
        self.selection = selection
        self.choices: tuple[frontend.ListChoice, ...] = ()
        self.workspace = selection.team
        self.chosen: frontend.ListChoice | None = None
        self.generation = 0
        self.read_cancel = threading.Event()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Choose list / workspace", classes="dialog-title")
            yield Static(
                "Loading accessible workspaces…", id="hierarchy-note", markup=False
            )
            yield Select[str](
                [],
                prompt="Workspace",
                id="workspace",
                allow_blank=True,
                disabled=True,
                compact=True,
            )
            yield Input(
                placeholder="Filter spaces, folders and lists",
                id="hierarchy-filter",
                compact=True,
            )
            yield Tree[frontend.ListChoice]("Lists", id="hierarchy-tree")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-hierarchy")
                yield Button(
                    "Use selected list",
                    id="choose-list",
                    disabled=True,
                    variant="primary",
                )

    def on_mount(self) -> None:
        self.run_worker(self.load_workspaces(), exit_on_error=False)
        self.query_one("#cancel-hierarchy", Button).focus()

    def cancel_read(self) -> None:
        self.read_cancel.set()
        self.generation += 1

    def on_unmount(self) -> None:
        self.cancel_read()

    def action_cancel_hierarchy(self) -> None:
        self.cancel_hierarchy()

    async def load_workspaces(self) -> None:
        cancel = self.read_cancel
        generation = self.generation
        try:
            workspaces = await asyncio.wrap_future(
                self.controller.read(
                    lambda service: service.workspaces(),
                    current_scope=True,
                    cancel=cancel,
                )
            )
        except Exception as error:
            if (
                self.is_mounted
                and generation == self.generation
                and not cancel.is_set()
            ):
                self.query_one("#hierarchy-note", Static).update(
                    Text(f"Could not read workspaces: {clean(str(error))}; no retry.")
                )
            return
        if not self.is_mounted or generation != self.generation or cancel.is_set():
            return
        if not workspaces:
            self.query_one("#hierarchy-note", Static).update(
                "No accessible workspaces for this account."
            )
            return
        selector = self.query_one("#workspace", Select)
        selector.set_options(
            (Text(workspace.name), workspace.id) for workspace in workspaces
        )
        selector.disabled = False
        selector.value = (
            self.selection.team
            if any(item.id == self.selection.team for item in workspaces)
            else workspaces[0].id
        )

    @on(Select.Changed, "#workspace")
    def workspace_changed(self, event: Select.Changed) -> None:
        self.cancel_read()
        self.workspace = event.value if isinstance(event.value, str) else ""
        self.choices = ()
        self.chosen = None
        self.query_one("#choose-list", Button).disabled = True
        self.rebuild_tree()
        if not self.workspace:
            self.query_one("#hierarchy-note", Static).update("Choose a workspace.")
            return
        self.read_cancel = threading.Event()
        self.query_one("#hierarchy-note", Static).update(
            "Reading spaces, folders and lists…"
        )
        self.run_worker(
            self.load_hierarchy(self.workspace, self.generation, self.read_cancel),
            exit_on_error=False,
        )

    async def load_hierarchy(
        self, workspace: str, generation: int, cancel: threading.Event
    ) -> None:
        try:
            choices = await asyncio.wrap_future(
                self.controller.read(
                    lambda service: service.hierarchy(workspace),
                    current_scope=workspace == self.selection.team,
                    cancel=cancel,
                )
            )
        except Exception as error:
            if (
                self.is_mounted
                and generation == self.generation
                and not cancel.is_set()
            ):
                self.query_one("#hierarchy-note", Static).update(
                    Text(f"Could not read lists: {clean(str(error))}; no retry.")
                )
            return
        if not self.is_mounted or generation != self.generation or cancel.is_set():
            return
        self.choices = choices
        self.rebuild_tree()
        self.query_one("#hierarchy-note", Static).update(
            f"{len(choices)} lists. This selects one list, not a workspace-wide task feed."
        )
        self.query_one("#hierarchy-filter", Input).focus()

    @on(Input.Changed, "#hierarchy-filter")
    def filter_changed(self) -> None:
        self.rebuild_tree()

    def rebuild_tree(self) -> None:
        tree = self.query_one("#hierarchy-tree", Tree)
        tree.clear()
        tree.root.expand()
        query = self.query_one("#hierarchy-filter", Input).value.casefold().strip()
        spaces: dict[str, TreeNode[frontend.ListChoice]] = {}
        folders: dict[tuple[str, str], TreeNode[frontend.ListChoice]] = {}
        for choice in self.choices:
            searchable = (
                f"{choice.space_name} {choice.folder_name} {choice.name} {choice.id}"
            )
            if query not in searchable.casefold():
                continue
            if choice.space_id not in spaces:
                spaces[choice.space_id] = tree.root.add(
                    Text(choice.space_name), expand=True
                )
            parent = spaces[choice.space_id]
            if choice.folder_name:
                key = (choice.space_id, choice.folder_id)
                if key not in folders:
                    folders[key] = parent.add(Text(choice.folder_name), expand=True)
                parent = folders[key]
            parent.add(
                Text(f"{choice.name}  ({choice.id})"), data=choice, allow_expand=False
            )
        self.chosen = None
        self.query_one("#choose-list", Button).disabled = True

    @on(Tree.NodeSelected, "#hierarchy-tree")
    def list_selected(self, event: Tree.NodeSelected[frontend.ListChoice]) -> None:
        self.chosen = event.node.data
        self.query_one("#choose-list", Button).disabled = self.chosen is None

    @on(Button.Pressed, "#choose-list")
    def choose_list(self) -> None:
        if self.chosen is not None:
            try:
                selection = replace(
                    self.selection, team=self.workspace, list=self.chosen.id, space=""
                )
            except ValueError as error:
                self.query_one("#hierarchy-note", Static).update(
                    Text(f"Could not select list: {clean(str(error))}")
                )
                return
            self.cancel_read()
            self.dismiss(selection)

    @on(Button.Pressed, "#cancel-hierarchy")
    def cancel_hierarchy(self) -> None:
        self.cancel_read()
        self.dismiss(None)


class ReviewModal(DeskModal):
    """The displayed proposal is immutable, even if a newer proposal arrives."""

    def __init__(
        self, controller: frontend.Controller, change: frontend.Change
    ) -> None:
        super().__init__()
        self.controller = controller
        self.change = change
        self.invalidated = False
        self.dispatched = False

    def compose(self) -> ComposeResult:
        change = self.change
        selected = change.selection
        with Vertical(classes="dialog"):
            yield Static("Review task change", classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Static(
                    Text(
                        f"Account: {selected.account}\nWorkspace: {selected.team}\nList: {change.task_list or selected.list}"
                        f"\nCredential source: {selected.credential_env}\nTask: {change.task}\nAction: {change.action}"
                    ),
                    id="review-identity",
                )
                yield Static("Exact current / proposed diff", classes="section-heading")
                yield Static(
                    Text(
                        json.dumps(
                            change.mutation.diff,
                            indent=2,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    id="review-diff",
                )
                yield Static(Text(change.text), id="review-engine")
                yield Static(
                    Text(
                        f"Concurrency digest: {change.mutation.field_digest}\nProposal approval: {change.approval}"
                    ),
                    classes="metadata",
                )
            yield Static(
                "Apply sends once. Selection or refresh invalidates this approval. Unknown outcomes are never retried.",
                id="review-note",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-review")
                yield Button("Apply this change", id="apply-review", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#cancel-review", Button).focus()
        self.set_interval(0.1, self.check_current)
        self.check_current()

    def current(self) -> bool:
        view = self.controller.snapshot()
        pending = view.pending
        return (
            not self.dispatched
            and view.state == "ready"
            and view.selection == self.change.selection
            and view.detail is not None
            and view.detail.task.id == self.change.task
            and pending is not None
            and pending.approval == self.change.approval
        )

    def check_current(self) -> None:
        if self.current() and not self.invalidated:
            return
        self.invalidated = True
        self.query_one("#apply-review", Button).disabled = True
        self.query_one("#review-note", Static).update(
            "This displayed proposal is no longer current. Close it and review the new state; nothing was sent."
        )

    @on(Button.Pressed, "#apply-review")
    def apply_review(self) -> None:
        if self.invalidated or not self.current():
            self.check_current()
            return
        # Never read a replacement proposal's task/approval here.
        try:
            self.controller.confirm(self.change.task, self.change.approval)
        except (ValueError, OSError) as error:
            self.invalidated = True
            self.query_one("#apply-review", Button).disabled = True
            self.query_one("#review-note", Static).update(Text(clean(str(error))))
            return
        self.dispatched = True
        self.dismiss()

    @on(Button.Pressed, "#cancel-review")
    async def cancel_review(self) -> None:
        await self.action_dismiss()

    async def action_dismiss(self, result: None = None) -> None:
        if self.current():
            try:
                self.controller.cancel()
            except ValueError:
                pass
        self.dismiss()


_HELP = """Task desk

↑ / ↓ or j / k   Move through the task queue
Enter             Read the highlighted task
/                 Focus the current-list filter
m                 Toggle only tasks assigned to this account
c                 Toggle closed/done tasks
r                 Refresh this list, keeping the selected task by ID
l                 Choose a workspace and list
a                 Actions: all reports, explicit sync and every task edit
o                 Open the fresh selected task with the guarded browser opener
Escape            Return to the queue in narrow panes, or cancel a dialog
?                 This help
q                 Quit (wait for a dispatched write first)
Tab / Shift+Tab   Move focus; arrows and Page Up/Down scroll native widgets

At wide widths the queue and reader stay side by side. Narrow panes show one
screen at a time. Resizing keeps your task, filter and reader position.

The filter, Mine and Closed controls affect only the currently loaded list.
Sync is explicit and discovers registry names, fields and sprint configuration
for engine reports and edits. It does not mutate remote tasks.

Every edit is staged first, then reviewed with an exact identity and diff.
Cancel has initial focus. Apply sends only that displayed proposal, once.
Stale data is read-only. Errors and unknown write outcomes stay visible;
there are no automatic retries. Markdown links never launch a browser.

Typing in a text field does not trigger the desk shortcuts.
"""


class ClickUpApp(App[int]):
    """Responsive view of a Controller; background completions never touch widgets."""

    TITLE = "ClickUp task desk"
    BINDINGS = [
        Binding("slash", "filter", show=False),
        Binding("l", "lists", show=False),
        Binding("a", "actions", show=False),
        Binding("r", "refresh", show=False),
        Binding("o", "open", show=False),
        Binding("m", "mine", show=False),
        Binding("c", "closed", show=False),
        Binding("escape", "back", show=False),
        Binding("question_mark", "help", show=False),
        Binding("q", "quit", show=False),
    ]
    CSS = """
    Screen { background: $background; }
    #scope { height: 2; padding: 0 1; background: $panel; }
    #freshness { height: auto; min-height: 1; max-height: 4; padding: 0 1; }
    #freshness.stale { color: $warning; }
    #freshness.failed { color: $error; }
    #toolbar { height: 3; padding: 0 1; background: $panel; }
    #toolbar Button { min-width: 10; width: 1fr; margin-right: 1; }
    #desk { height: 1fr; }
    #queue-pane { width: 43%; min-width: 32; border-right: solid $panel; }
    #filter { margin: 0 1; }
    #queue-controls { height: 3; padding: 0 1; }
    #queue-controls Checkbox { width: auto; margin-right: 1; }
    #task-count { height: 1; padding: 0 1; color: $text-muted; }
    #task-queue { height: 1fr; }
    #reader-pane { width: 1fr; }
    #reader { height: 1fr; padding: 1 2; }
    #task-title { height: auto; text-style: bold; margin-bottom: 1; }
    #task-status, #task-meta { height: auto; margin-bottom: 1; }
    .metadata { color: $text-muted; height: auto; }
    .section-heading { height: auto; text-style: bold; margin-top: 1; margin-bottom: 1; }
    #description { height: auto; }
    #comments { height: auto; }
    .comment { height: auto; border-left: solid $panel; padding-left: 1; margin-bottom: 1; }
    .comment-author { height: auto; color: $text-muted; }
    .comment Markdown { height: auto; margin: 0; padding: 0; }
    #desk-help { dock: bottom; height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    .narrow #queue-pane { width: 100%; min-width: 0; border: none; }
    .narrow #reader-pane { width: 100%; }
    .narrow #reader { padding: 1; }
    ModalScreen { align: center middle; background: $background 70%; }
    .dialog { width: 92%; max-width: 100; height: 92%; padding: 0 1; background: $surface; border: thick $primary; }
    .dialog-title { height: auto; text-style: bold; margin-bottom: 1; }
    .dialog-note { height: auto; margin-bottom: 1; }
    .dialog-scroll { height: 1fr; }
    .dialog-buttons { height: 3; align-horizontal: right; }
    .dialog-buttons Button { margin-left: 1; }
    .dialog OptionList, #hierarchy-tree { height: 1fr; }
    .dialog Input, .dialog Select { margin-bottom: 1; }
    .dialog TextArea { height: 1fr; min-height: 4; }
    #value-error, #scope-error { height: auto; color: $error; }
    #review-note, #status-note, #hierarchy-note { height: auto; margin-bottom: 1; }
    #hierarchy-note { max-height: 3; }
    #review-diff, #review-engine { height: auto; margin-bottom: 1; }
    """

    def __init__(self, controller: frontend.Controller) -> None:
        super().__init__()
        self.controller = controller
        self.view = controller.snapshot()
        self._seen_revision = -1
        self._queue_tasks: tuple[frontend.Task, ...] = ()
        self._selected_id = self.view.detail.task.id if self.view.detail else ""
        self._rendered_detail: frontend.Detail | None = None
        self._rendered_selection = self.view.selection
        self._detail_mode = False
        self._narrow = False
        self._rendering = False
        self._expect_output = False
        self._output_title = "Report"
        self._shown_approvals: set[str] = set()
        self._notice = ""

    def compose(self) -> ComposeResult:
        yield Static("ClickUp", id="scope", markup=False)
        yield Static("Loading…", id="freshness", markup=False)
        with Horizontal(id="toolbar"):
            yield Button("Refresh", id="refresh")
            yield Button("Lists", id="lists")
            yield Button("Actions", id="actions")
            yield Button("Space", id="workspace")
        with Horizontal(id="desk"):
            with Vertical(id="queue-pane"):
                yield Input(placeholder="Filter this list  (/)", id="filter")
                with Horizontal(id="queue-controls"):
                    yield Checkbox("Mine", id="mine")
                    yield Checkbox("Closed", id="closed")
                yield Static("", id="task-count", markup=False)
                yield TaskQueue(
                    id="task-queue",
                    cursor_type="row",
                    show_header=True,
                    show_row_labels=False,
                )
            with Vertical(id="reader-pane"):
                with VerticalScroll(id="reader", can_focus=True):
                    yield Static(
                        "Choose a task and press Enter", id="task-title", markup=False
                    )
                    yield Static("", id="task-status", markup=False)
                    yield Static("", id="task-meta", classes="metadata", markup=False)
                    yield Markdown("", id="description", open_links=False)
                    yield Static(
                        "",
                        id="comments-heading",
                        classes="section-heading",
                        markup=False,
                    )
                    yield Vertical(id="comments")
        yield Static("", id="desk-help", markup=False)

    async def on_mount(self) -> None:
        table = self.query_one(TaskQueue)
        table.add_column("Task", key="task", width=24)
        table.add_column("Status", key="status", width=12)
        self._layout(self.size.width)
        await self.poll_view()
        if not self.is_running:
            return
        table.focus()
        self.set_interval(0.1, self.poll_view)
        if (
            self.view.state == "loading"
            and self.view.revision == 0
            and self.view.listing is None
        ):
            self._call(self.controller.refresh)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action not in _DESK_ACTIONS:
            return True
        if len(self.screen_stack) > 1:
            return False
        if isinstance(self.focused, (Input, TextArea)) and action not in {
            "back",
            "filter",
        }:
            return False
        return True

    def on_resize(self, event: Resize) -> None:
        if self.default_screen.is_mounted:
            self._layout(event.size.width)

    def _layout(self, width: int) -> None:
        if not self.is_running:
            return
        was_narrow = self._narrow
        self._narrow = width < 95
        self.default_screen.set_class(self._narrow, "narrow")
        queue = self.query_one("#queue-pane")
        reader = self.query_one("#reader-pane")
        focused = self.focused
        if self._narrow and not was_narrow and focused is not None:
            if reader in focused.ancestors:
                self._detail_mode = True
            elif queue in focused.ancestors:
                self._detail_mode = False
        queue.display = not self._narrow or not self._detail_mode
        reader.display = not self._narrow or self._detail_mode
        self._footer()

    def _footer(self) -> None:
        if self.view.state == "writing":
            text = "Write sent once. Waiting for outcome; no retries."
        elif self._narrow and self._detail_mode:
            text = "Esc queue   a actions   o open   ? help"
        else:
            text = "↑↓/jk move  Enter read  / filter  a actions  ? help"
        self.query_one("#desk-help", Static).update(text)

    async def poll_view(self) -> None:
        # Textual marks the app stopped before pruning screens, but stops timers later.
        if not self.is_running or self._rendering:
            return
        view = self.controller.snapshot()
        if view.revision == self._seen_revision:
            if (
                view.pending is not None
                and view.pending.approval not in self._shown_approvals
                and len(self.screen_stack) == 1
            ):
                self._shown_approvals.add(view.pending.approval)
                self.push_screen(ReviewModal(self.controller, view.pending))
            return
        self._rendering = True
        try:
            self.view = view
            self._seen_revision = view.revision
            if view.selection != self._rendered_selection:
                self._rendered_selection = view.selection
                self._selected_id = ""
                self._detail_mode = False
                self._layout(self.size.width)
            if view.detail is not None:
                self._selected_id = view.detail.task.id
            self._render_scope()
            self._render_queue()
            await self._render_detail()
            if not self.is_running:
                return
            self._render_state()
            if (
                view.pending is not None
                and view.pending.approval not in self._shown_approvals
            ):
                if len(self.screen_stack) == 1:
                    self._shown_approvals.add(view.pending.approval)
                    self.push_screen(ReviewModal(self.controller, view.pending))
            if self._expect_output and view.state != "loading":
                self._expect_output = False
                if view.output:
                    self.push_screen(OutputModal(self._output_title, view.output))
        finally:
            self._rendering = False

    def _render_scope(self) -> None:
        listing = self.view.listing
        selected = self.view.selection
        if listing is None:
            label = f"ClickUp / Workspace {selected.team} / List {selected.list}"
        else:
            parts = [listing.workspace_name or f"Workspace {selected.team}"]
            if listing.space_name:
                parts.append(listing.space_name)
            parts.append(listing.name)
            label = " / ".join(parts)
        self.query_one("#scope", Static).update(
            Text(
                f"{label}\nAccount {selected.account}  ·  {listing.filter_label if listing and listing.filter_label else f'List {selected.list}'}"
            )
        )

    def visible_tasks(self) -> tuple[frontend.Task, ...]:
        listing = self.view.listing
        if listing is None:
            return ()
        query = self.query_one("#filter", Input).value.casefold().strip()
        mine = self.query_one("#mine", Checkbox).value
        closed = self.query_one("#closed", Checkbox).value
        return tuple(
            task
            for task in listing.tasks
            if (closed or task.status_type.casefold() not in _CLOSED_TYPES)
            and (
                not mine
                or any(
                    person.id == self.view.selection.account
                    for person in task.assignees
                )
            )
            and query
            in f"{task.name} {task.id} {task.custom_id} {task.status}".casefold()
        )

    def _render_queue(self) -> None:
        tasks = self.visible_tasks()
        listing = self.view.listing
        total = len(listing.tasks) if listing else 0
        count = f"{len(tasks)} / {total} tasks"
        if not tasks and listing is not None:
            count += " — no matches" if total else " — list is empty"
        self.query_one("#task-count", Static).update(count)
        table = self.query_one(TaskQueue)
        workspace = bool(listing and listing.filter_label)
        if len(table.ordered_columns) != (3 if workspace else 2):
            table.clear(columns=True)
            table.add_column("Task", key="task", width=24)
            table.add_column("Status", key="status", width=12)
            if workspace:
                table.add_column("List", key="list", width=14)
            table.on_resize(Resize(table.size, table.size))
            self._queue_tasks = ()
        if tasks == self._queue_tasks:
            return
        cursor_id = ""
        if table.cursor_row < len(self._queue_tasks):
            cursor_id = self._queue_tasks[table.cursor_row].id
        anchor = self._selected_id or cursor_id
        scroll_x, scroll_y = table.scroll_x, table.scroll_y
        table.clear()
        self._queue_tasks = tasks
        for task in tasks:
            title = Text(task.name, style="bold", overflow="ellipsis", no_wrap=True)
            title.append("\n" + (task.custom_id or task.id), style="dim")
            if task.assignees:
                title.append(
                    "  " + ", ".join(person.name for person in task.assignees),
                    style="dim",
                )
            cells = [title, status_text(task)]
            if workspace:
                cells.append(Text(task.list_name or task.list_id))
            table.add_row(*cells, key=task.id, height=2)
        for index, task in enumerate(tasks):
            if task.id == anchor:
                table.move_cursor(row=index, animate=False, scroll=False)
                break
        table.scroll_to(scroll_x, scroll_y, animate=False, force=True)

    async def _render_detail(self) -> None:
        detail = self.view.detail
        if detail == self._rendered_detail:
            if detail is None:
                self.query_one("#task-title", Static).update(
                    "Loading task…"
                    if self.view.state == "loading" and self._selected_id
                    else "Choose a task and press Enter"
                )
            return
        previous_id = self._rendered_detail.task.id if self._rendered_detail else ""
        self._rendered_detail = detail
        reader = self.query_one("#reader", VerticalScroll)
        position = (reader.scroll_x, reader.scroll_y)
        comments = self.query_one("#comments", Vertical)
        await comments.remove_children()
        if not self.is_running:
            return
        if detail is None:
            self.query_one("#task-title", Static).update(
                "Loading task…"
                if self.view.state == "loading" and self._selected_id
                else "Choose a task and press Enter"
            )
            self.query_one("#task-status", Static).update("")
            self.query_one("#task-meta", Static).update("")
            await self.query_one("#description", Markdown).update("")
            if not self.is_running:
                return
            self.query_one("#comments-heading", Static).update("")
            return
        task = detail.task
        self.query_one("#task-title", Static).update(Text(task.name, style="bold"))
        self.query_one("#task-status", Static).update(status_text(task))
        assignees = ", ".join(person.name for person in task.assignees) or "Not set"
        self.query_one("#task-meta", Static).update(
            Text(
                f"{task.custom_id + '  /  ' if task.custom_id else ''}{task.id}  ·  Home list {task.list_id}"
                f"\nAssignees: {assignees}\nDue: {date_label(task.due)}  ·  Priority: {task.priority or 'Not set'}"
                f"\nPoints: {task.points if task.points != '-' else 'Not set'}  ·  Updated: {date_label(task.updated)}"
            )
        )
        await self.query_one("#description", Markdown).update(
            task.description or "_No description._"
        )
        if not self.is_running:
            return
        self.query_one("#comments-heading", Static).update(
            f"Comments ({len(detail.comments)})"
        )
        for comment in detail.comments:
            depth = min(max(comment.depth, 0), 8)
            prefix = f"Reply depth {comment.depth} · " if comment.depth else ""
            block = Vertical(
                Static(
                    Text(
                        f"{prefix}{comment.author or 'Author unavailable'} · {date_label(comment.date)}"
                    ),
                    classes="comment-author",
                ),
                Markdown(comment.body or "_Empty comment._", open_links=False),
                classes="comment",
            )
            block.styles.margin = (0, 0, 1, depth * 2)
            await comments.mount(block)
            if not self.is_running:
                return
        if previous_id == task.id:
            reader.scroll_to(*position, animate=False, force=True)
        else:
            reader.scroll_home(animate=False)

    def _render_state(self) -> None:
        view = self.view
        state = view.state
        if state == "stale":
            message = "STALE retained data — edits and browser open disabled. Refresh explicitly."
        elif state == "writing":
            message = (
                "Write dispatched once. Waiting for its outcome; no automatic retries."
            )
        elif state == "loading":
            message = "Loading — retained content is not editable."
        elif state in {"ready", "empty"}:
            message = "Fresh current-list data" + (
                " — proposal awaiting review" if view.pending else ""
            )
        else:
            message = f"{state}: no fresh data."
        if view.error:
            message += f"\n{view.error_state or state}: {view.error}"
        elif view.output and not self._expect_output:
            message += "\n" + single_line(view.output)[:220]
        if self._notice:
            message += "\n" + self._notice
        freshness = self.query_one("#freshness", Static)
        freshness.update(Text(message))
        freshness.set_class(state in {"stale", "writing"}, "stale")
        freshness.set_class(bool(view.error), "failed")
        self.query_one("#refresh", Button).disabled = state == "writing"
        self.query_one("#lists", Button).disabled = state == "writing"
        self.query_one("#actions", Button).disabled = state == "writing"
        self._footer()

    def _call(self, callback: Callable[[], object]) -> bool:
        try:
            callback()
        except (
            ValueError,
            OSError,
            frontend.ClickUpError,
            diagnostics.EngineError,
        ) as error:
            self._notice = clean(
                str(
                    frontend.engine_error(error)
                    if isinstance(error, diagnostics.EngineError)
                    else error
                )
            )
            self._render_state()
            return False
        self._notice = ""
        return True

    @on(Input.Changed, "#filter")
    @on(Checkbox.Changed)
    def filter_changed(self) -> None:
        self._render_queue()

    @on(Input.Submitted, "#filter")
    def filter_submitted(self) -> None:
        self.query_one(TaskQueue).focus()

    @on(DataTable.RowSelected, "#task-queue")
    def read_selected(self, event: DataTable.RowSelected) -> None:
        identity = str(event.row_key.value)
        view = self.controller.snapshot()
        if view.state != "ready":
            return
        if view.detail is None or view.detail.task.id != identity:
            if not self._call(lambda: self.controller.select(identity)):
                return
        self._selected_id = identity
        self._detail_mode = True
        self._layout(self.size.width)
        self.query_one("#reader", VerticalScroll).focus()

    @on(Markdown.LinkClicked)
    def blocked_markdown_link(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        event.prevent_default()
        self._notice = "Markdown links are disabled. Use o to open the fresh task's canonical ClickUp link."
        self._render_state()

    @on(Button.Pressed, "#refresh")
    def refresh_button(self) -> None:
        self.action_refresh()

    @on(Button.Pressed, "#lists")
    def lists_button(self) -> None:
        self.action_lists()

    @on(Button.Pressed, "#workspace")
    def workspace_button(self) -> None:
        if self.controller.snapshot().state != "writing":
            self.push_screen(
                WorkspaceModal(self.controller.service), self.workspace_selected
            )

    def workspace_selected(
        self, result: tuple[frontend.Selection, bool, frontend.WorkspaceFilters] | None
    ) -> None:
        if result is None or self.controller.snapshot().state == "writing":
            return
        selection, workspace, filters = result

        def load() -> None:
            future = self.controller.switch_workspace(
                selection, workspace=workspace, filters=filters
            )
            service = self.controller.service

            async def save_default() -> None:
                try:
                    await asyncio.wrap_future(future)
                    view = self.controller.snapshot()
                    if (
                        not workspace
                        or self.controller.service is not service
                        or view.state not in {"ready", "empty"}
                    ):
                        return
                    saved = frontend.load_selection()
                    if replace(saved, space="") == replace(selection, space=""):
                        frontend.save_selection(replace(saved, space=selection.space))
                    else:
                        self._notice = "Space loaded for this pane only; saved account and scope are unchanged."
                except (
                    frontend.ClickUpError,
                    diagnostics.EngineError,
                    ValueError,
                    OSError,
                ):
                    self._notice = "The saved default space was not changed. Only a successful Space load for the saved account and scope can update it."
                if self.is_running:
                    self._render_state()

            self.run_worker(save_default(), exit_on_error=False)

        self._call(load)

    @on(Button.Pressed, "#actions")
    def actions_button(self) -> None:
        self.action_actions()

    def action_filter(self) -> None:
        if self._narrow and self._detail_mode:
            self._detail_mode = False
            self._layout(self.size.width)
        self.query_one("#filter", Input).focus()

    def action_mine(self) -> None:
        widget = self.query_one("#mine", Checkbox)
        widget.value = not widget.value

    def action_closed(self) -> None:
        widget = self.query_one("#closed", Checkbox)
        widget.value = not widget.value

    async def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(TaskQueue).focus()
            return
        if self._narrow and self._detail_mode:
            self._detail_mode = False
            self._layout(self.size.width)
        self.query_one(TaskQueue).focus()

    def action_refresh(self) -> None:
        if self.controller.snapshot().state != "writing":
            self._call(self.controller.refresh)

    def action_lists(self) -> None:
        view = self.controller.snapshot()
        if view.state != "writing":
            self.push_screen(
                HierarchyModal(self.controller, view.selection),
                lambda selected: self.switch_scope(selected, persist=True),
            )

    def switch_scope(
        self, selected: frontend.Selection | None, *, persist: bool
    ) -> None:
        if selected is None:
            return
        if self.controller.snapshot().state == "writing":
            self._notice = (
                "Wait for the dispatched write's outcome before switching scope."
            )
            self._render_state()
            return

        def save_and_switch() -> None:
            if persist:
                frontend.save_selection(selected)
            self.controller.switch(selected)

        self._call(save_and_switch)

    def action_actions(self) -> None:
        view = self.controller.snapshot()
        if view.state != "writing":
            self.push_screen(ActionsModal(view), self.choose_action)

    def choose_action(self, action: str | None) -> None:
        if action is None:
            return
        view = self.controller.snapshot()
        if view.state == "writing":
            return
        if action == "lists":
            self.action_lists()
        elif action == "switch":
            self.push_screen(
                ScopeModal(view.selection),
                lambda selected: self.switch_scope(selected, persist=False),
            )
        elif action == "workspace":
            self.workspace_button()
        elif action == "help":
            self.action_help()
        elif action == "output":
            self.push_screen(OutputModal("Last output / write receipt", view.output))
        elif action == "search":
            self.push_screen(
                ValueModal(
                    "Search",
                    "Search task names across the synced space. Sync first if needed.",
                    submit_label="Search",
                ),
                lambda value: (
                    self.inspect("search", value) if value is not None else None
                ),
            )
        elif action in {item[0] for item in _READS}:
            self.inspect(action)
        elif action in frontend.WRITE_ACTIONS:
            self.edit(action)

    def inspect(self, command: str, argument: str = "") -> None:
        self._expect_output = True
        self._output_title = dict((item[0], item[1]) for item in _READS).get(
            command, "Report"
        )
        if not self._call(lambda: self.controller.inspect(command, argument)):
            self._expect_output = False

    def edit(self, action: str) -> None:
        view = self.controller.snapshot()
        if view.state != "ready" or view.detail is None:
            return
        task = view.detail.task
        selection = view.selection

        # The input belongs to this exact selected task, never whichever task loads later.
        def stage(value: str | None) -> None:
            current = self.controller.snapshot()
            if value is None:
                return
            if (
                current.selection != selection
                or current.detail is None
                or current.detail.task.id != task.id
                or current.state != "ready"
            ):
                self._notice = (
                    "The selected task changed. Open the edit again; nothing was sent."
                )
                self._render_state()
                return
            self._call(lambda: self.controller.stage(action, value))

        if action == "status":
            self.push_screen(StatusModal(self.controller, task), stage)
            return
        due = ""
        if task.due:
            try:
                due = datetime.fromtimestamp(int(task.due) / 1000).strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                pass
        forms = {
            "assignee": (
                "Change assignee",
                "Enter one account ID or exact registry name. Current: "
                + (", ".join(person.name for person in task.assignees) or "Not set"),
                task.assignees[0].id if len(task.assignees) == 1 else "",
                False,
            ),
            "due": (
                "Change due date",
                "Enter YYYY-MM-DD. Current: " + date_label(task.due),
                due,
                False,
            ),
            "description": (
                "Edit description",
                "Markdown description. Tab moves to Cancel / Review change.",
                task.description,
                True,
            ),
            "points": (
                "Change points",
                "Enter a number. Current: "
                + (task.points if task.points != "-" else "Not set"),
                task.points if task.points != "-" else "",
                False,
            ),
            "field": (
                "Change custom field",
                "Enter exact registry FIELD=VALUE. Field identity and current value appear in the review; sync first if needed.",
                "",
                False,
            ),
            "comment": (
                "Add comment",
                "Write a new comment. It is not posted until you review and apply it.",
                "",
                True,
            ),
        }
        title, hint, value, multiline = forms[action]
        self.push_screen(ValueModal(title, hint, value, multiline=multiline), stage)

    def action_open(self) -> None:
        view = self.controller.snapshot()
        if view.state != "ready" or view.detail is None:
            self._notice = (
                "Browser open requires a fresh selected task. Refresh explicitly."
            )
            self._render_state()
            return
        task, selected = view.detail.task, view.selection

        async def open_selected() -> None:
            current = self.controller.snapshot()
            if (
                current.state != "ready"
                or current.selection != selected
                or current.detail is None
                or current.detail.task != task
            ):
                return
            try:
                await asyncio.to_thread(
                    frontend.open_task, task, selected.credential_env
                )
            except (
                ValueError,
                OSError,
                subprocess.SubprocessError,
                frontend.ClickUpError,
            ) as error:
                self._notice = "Could not open task: " + clean(str(error))
                self._render_state()

        self.run_worker(open_selected(), exit_on_error=False)

    def action_help(self) -> None:
        self.push_screen(OutputModal("Keyboard and safety", _HELP))

    async def action_quit(self) -> None:
        if self.controller.snapshot().state == "writing":
            self._notice = (
                "Wait for the dispatched write's outcome. It will not be retried."
            )
            self._render_state()
            return
        self.exit(0)


def run(controller: frontend.Controller) -> int:
    """Run the desk; the entrypoint remains responsible for closing its controller."""
    return ClickUpApp(controller).run() or 0

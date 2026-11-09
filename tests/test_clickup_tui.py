"""Consumer-visible desk transitions, with no network or production mutations."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TextArea,
    Tree,
)

from rexdeck.clickup import frontend as cu
from rexdeck.clickup import tui
from rexdeck.clickup.engine import receipts, runtime

_SELECTION = cu.Selection("42", "900", "101")


def completed[T](value: T) -> concurrent.futures.Future[T]:
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    future.set_result(value)
    return future


def task(
    identifier: str, title: str, *, mine: bool = True, closed: bool = False
) -> cu.Task:
    return cu.Task(
        id=identifier,
        custom_id="",
        name=title,
        status="Closed" if closed else "In progress",
        list_id="101",
        description="## Description\n\n" + "A readable paragraph.\n\n" * 30,
        updated="1794182400000",
        url=f"https://app.clickup.com/t/{identifier}",
        status_type="closed" if closed else "custom",
        status_color="#0088cc",
        assignees=(cu.Assignee("42" if mine else "7", "Me" if mine else "Other"),),
    )


def change(selected_task: cu.Task, value: str, *, nonce: str) -> cu.Change:
    return cu.Change(
        _SELECTION,
        selected_task.id,
        "description",
        value,
        receipts.Mutation(
            selected_task.id, "digest", {"description": {"from": "Old", "to": value}}
        ),
        "Dry run: nothing sent",
        nonce=nonce,
    )


class ReadOnlyHierarchy(cu.Service):
    """Only the two hierarchy reads used by the native picker are available."""

    def __init__(self, selection: cu.Selection) -> None:
        self.selection = selection

    def workspaces(self) -> tuple[cu.Workspace, ...]:
        return (cu.Workspace("900", "Engineering"), cu.Workspace("901", "Operations"))

    def hierarchy(self, team_id: str | None = None) -> tuple[cu.ListChoice, ...]:
        if team_id == "901":
            return (
                cu.ListChoice("201", "Incidents", "20", "Support", "On call", "30"),
                cu.ListChoice("202", "Escalations", "20", "Support", "On call", "31"),
            )
        return (cu.ListChoice("101", "Backlog", "10", "Product"),)


class DeskController(cu.Controller):
    """Faithful immutable View transitions; the engine contract is tested elsewhere."""

    def __init__(self, *, selected: bool = False) -> None:
        tasks = (
            task("one", "First task"),
            task("two", "Second task", mine=False),
            task("three", "Closed task", closed=True),
        )
        listing = cu.Listing("Backlog", (), tasks, 1, "Engineering", "Product")
        self.view = cu.View(
            _SELECTION,
            state="ready",
            listing=listing,
            detail=cu.Detail(
                tasks[0], (cu.Comment("c1", "1794182400000", "A comment", 1, "Ada"),)
            )
            if selected
            else None,
            revision=1,
        )
        self.service = ReadOnlyHierarchy(_SELECTION)
        self.selected: list[str] = []
        self.confirmed: list[tuple[str, str]] = []
        self.staged: list[tuple[str, str]] = []
        self.scopes: list[cu.Selection] = []
        self.refreshes = 0
        self.cancels = 0

    def snapshot(self) -> cu.View:
        return self.view

    def read[T](
        self,
        operation: Callable[[cu.Service], T],
        *,
        current_scope: bool,
        cancel: threading.Event | None = None,
    ) -> concurrent.futures.Future[T]:
        return completed(operation(self.service))

    def transition(self, view: cu.View) -> None:
        self.view = replace(view, revision=self.view.revision + 1)

    def select(self, identity: str) -> concurrent.futures.Future[cu.Detail]:
        if self.view.state != "ready" or self.view.listing is None:
            raise ValueError("Select only from fresh data")
        selected_task = next(
            item for item in self.view.listing.tasks if item.id == identity
        )
        self.selected.append(identity)
        detail = cu.Detail(selected_task, ())
        self.transition(replace(self.view, detail=detail, pending=None))
        return completed(detail)

    def refresh(self) -> concurrent.futures.Future[tuple[cu.Listing, cu.Detail | None]]:
        if self.view.state == "writing" or self.view.listing is None:
            raise ValueError("Cannot refresh now")
        self.refreshes += 1
        self.transition(
            replace(self.view, state="ready", pending=None, error="", error_state="")
        )
        return completed((self.view.listing, self.view.detail))

    def switch(
        self, selection: cu.Selection
    ) -> concurrent.futures.Future[tuple[cu.Listing, cu.Detail | None]]:
        self.scopes.append(selection)
        listing = cu.Listing("Incidents", (), (), 1, "Operations", "Support")
        self.transition(cu.View(selection, state="empty", listing=listing))
        return completed((listing, None))

    def stage(
        self, action: str, value: str
    ) -> concurrent.futures.Future[runtime.Outcome]:
        if self.view.state != "ready" or self.view.detail is None:
            raise ValueError("Fresh task required")
        self.staged.append((action, value))
        pending = change(self.view.detail.task, value, nonce="staged")
        self.transition(replace(self.view, pending=pending))
        future: concurrent.futures.Future[runtime.Outcome] = concurrent.futures.Future()
        return future

    def confirm(
        self, identity: str, approval: str
    ) -> concurrent.futures.Future[tuple[cu.Listing, cu.Detail, str]]:
        pending = self.view.pending
        if (
            self.view.state != "ready"
            or pending is None
            or pending.task != identity
            or pending.approval != approval
        ):
            raise ValueError("Only the exact displayed proposal may be approved")
        self.confirmed.append((identity, approval))
        self.transition(replace(self.view, state="writing", pending=None))
        future: concurrent.futures.Future[tuple[cu.Listing, cu.Detail, str]] = (
            concurrent.futures.Future()
        )
        return future

    def cancel(self) -> None:
        if self.view.pending is None:
            raise ValueError("No proposal")
        self.cancels += 1
        self.transition(replace(self.view, pending=None))

    def close(self) -> None:
        pass


def test_narrow_reader_back_and_resize_preserve_selection_filter_and_scroll() -> None:
    async def scenario() -> None:
        controller = DeskController()
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            assert app.query_one("#queue-pane").display
            assert not app.query_one("#reader-pane").display
            await pilot.press("slash")
            await pilot.press("t", "a", "s", "k", "enter")
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.poll_view()
            assert controller.selected == ["two"]
            assert not app.query_one("#queue-pane").display
            assert app.query_one("#reader-pane").display
            reader = app.query_one("#reader", VerticalScroll)
            reader.scroll_to(y=8, animate=False)
            await pilot.pause()
            position = reader.scroll_y
            await pilot.resize_terminal(140, 40)
            await pilot.pause()
            assert app.query_one("#queue-pane").display
            assert app.query_one("#reader-pane").display
            assert app.query_one("#filter", Input).value == "task"
            assert reader.scroll_y == position
            controller.transition(
                replace(controller.view, output="A harmless new revision")
            )
            await app.poll_view()
            assert controller.selected == ["two"]
            await pilot.resize_terminal(60, 24)
            await pilot.pause()
            await pilot.press("escape")
            assert app.query_one("#queue-pane").display
            assert not app.query_one("#reader-pane").display
            assert (
                controller.view.detail is not None
                and controller.view.detail.task.id == "two"
            )
            assert controller.selected == ["two"]

    asyncio.run(scenario())


def test_filter_typing_does_not_trigger_shortcuts_and_mine_closed_use_typed_data() -> (
    None
):
    async def scenario() -> None:
        controller = DeskController()
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert [item.id for item in app.visible_tasks()] == ["one", "two"]
            await pilot.press("m")
            await pilot.pause()
            assert [item.id for item in app.visible_tasks()] == ["one"]
            await pilot.press("c")
            await pilot.pause()
            assert [item.id for item in app.visible_tasks()] == ["one", "three"]
            await pilot.press("slash", "m", "r", "a", "q")
            await pilot.pause()
            assert app.query_one("#filter", Input).value == "mraq"
            assert app.query_one("#mine", Checkbox).value
            assert app.query_one("#closed", Checkbox).value
            assert controller.refreshes == 0
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_stale_content_cannot_edit_or_open_and_access_failure_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        cu, "open_task", lambda selected, credential_env: opened.append(selected.id)
    )

    async def scenario() -> None:
        controller = DeskController(selected=True)
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            controller.transition(
                replace(
                    controller.view,
                    state="stale",
                    error_state="unknown-outcome",
                    error="Write outcome unknown; no retry",
                )
            )
            await app.poll_view()
            app.edit("comment")
            app.action_open()
            await pilot.pause()
            assert controller.staged == []
            assert opened == []
            assert len(app.screen_stack) == 1
            app.action_actions()
            await pilot.pause()
            options = app.screen.query_one(OptionList)
            for action in cu.WRITE_ACTIONS:
                assert options.get_option(action).disabled
            await pilot.press("escape")
            controller.transition(
                replace(
                    controller.view,
                    state="permission",
                    error_state="permission",
                    error="Access revoked",
                    listing=None,
                    detail=None,
                )
            )
            await app.poll_view()
            assert app.query_one(tui.TaskQueue).row_count == 0
            assert app.query_one("#description", Markdown).table_of_contents == []
            assert app._rendered_detail is None

    asyncio.run(scenario())


def test_replaced_proposal_never_approves_or_cancels_a_newer_proposal() -> None:
    async def scenario() -> None:
        controller = DeskController(selected=True)
        assert controller.view.detail is not None
        first = change(
            controller.view.detail.task, "First proposed description", nonce="first"
        )
        second = change(
            controller.view.detail.task,
            "Different proposed description",
            nonce="second",
        )
        controller.transition(replace(controller.view, pending=first))
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            review = app.screen
            assert isinstance(review, tui.ReviewModal)
            assert app.focused is review.query_one("#cancel-review", Button)
            controller.transition(replace(controller.view, pending=second))
            review.apply_review()
            await pilot.pause()
            assert review.query_one("#apply-review", Button).disabled
            assert controller.confirmed == []
            await pilot.click("#cancel-review")
            await pilot.pause()
            await app.poll_view()
            await pilot.pause()
            assert controller.cancels == 0
            assert controller.view.pending == second
            assert isinstance(app.screen, tui.ReviewModal)
            assert app.screen.change.approval == second.approval
            await pilot.press("tab", "enter")
            await pilot.pause()
            await app.poll_view()
            assert controller.confirmed == [(second.task, second.approval)]
            assert controller.view.state == "writing"
            assert app.query_one("#refresh", Button).disabled
            assert app.query_one("#lists", Button).disabled
            assert app.query_one("#actions", Button).disabled

    asyncio.run(scenario())


def test_description_form_stages_only_and_cancel_remains_default() -> None:
    async def scenario() -> None:
        controller = DeskController(selected=True)
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            app.edit("description")
            await pilot.pause()
            editor = app.screen.query_one(TextArea)
            assert controller.view.detail is not None
            assert editor.text == controller.view.detail.task.description
            editor.load_text("A real proposed description")
            await pilot.press("m", "r", "q")
            assert controller.refreshes == 0
            await pilot.press("tab", "tab", "enter")
            await pilot.pause()
            await app.poll_view()
            await pilot.pause()
            assert len(controller.staged) == 1
            assert controller.confirmed == []
            assert isinstance(app.screen, tui.ReviewModal)
            assert app.focused is app.screen.query_one("#cancel-review", Button)
            await pilot.press("escape")
            await pilot.pause()
            assert controller.cancels == 1
            assert controller.confirmed == []

    asyncio.run(scenario())


def test_hierarchy_workspace_filter_and_explicit_choice_save_only_selected_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[cu.Selection] = []
    monkeypatch.setattr(cu, "save_selection", saved.append)

    async def scenario() -> None:
        controller = DeskController()
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, tui.HierarchyModal)
            picker.query_one("#workspace", Select).value = "901"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = picker.query_one("#hierarchy-tree", Tree)
            assert len(tree.root.children[0].children) == 2
            picker.query_one("#hierarchy-filter", Input).value = "incidents"
            await pilot.pause()
            tree = picker.query_one("#hierarchy-tree", Tree)
            space = tree.root.children[0]
            folder = space.children[0]
            leaf = folder.children[0]
            assert leaf.data is not None and leaf.data.id == "201"
            assert saved == []
            tree.select_node(leaf)
            await pilot.pause()
            cancel = picker.read_cancel
            picker.query_one("#workspace", Select).value = Select.BLANK
            await pilot.pause()
            assert cancel.is_set()
            assert picker.workspace == ""
            assert picker.choices == ()
            assert picker.chosen is None
            assert not tree.root.children
            assert picker.query_one("#choose-list", Button).disabled
            picker.choose_list()
            assert isinstance(app.screen, tui.HierarchyModal)
            assert app.screen is picker
            assert saved == [] and controller.scopes == []
            picker.query_one("#workspace", Select).value = "901"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree.select_node(tree.root.children[0].children[0].children[0])
            await pilot.pause()
            assert picker.chosen is not None
            valid_choice = picker.chosen
            picker.chosen = replace(valid_choice, id="not-numeric")
            picker.choose_list()
            await pilot.pause()
            assert isinstance(app.screen, tui.HierarchyModal)
            assert app.screen is picker
            assert saved == [] and controller.scopes == []
            assert "Could not select list" in str(
                picker.query_one("#hierarchy-note", Static).render()
            )
            picker.chosen = valid_choice
            cancel = picker.read_cancel
            await pilot.click("#choose-list")
            await pilot.pause()
            expected = replace(_SELECTION, team="901", list="201")
            assert saved == [expected]
            assert controller.scopes == [expected]
            assert cancel.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("exit_method", ["escape", "cancel", "dismiss", "quit"])
@pytest.mark.parametrize("loading_workspaces", [False, True])
def test_hierarchy_exit_cancels_discovery_without_changing_scope(
    exit_method: str, loading_workspaces: bool
) -> None:
    class PendingController(DeskController):
        def read[T](
            self,
            operation: Callable[[cu.Service], T],
            *,
            current_scope: bool,
            cancel: threading.Event | None = None,
        ) -> concurrent.futures.Future[T]:
            if loading_workspaces:
                return concurrent.futures.Future()
            return super().read(operation, current_scope=current_scope, cancel=cancel)

    async def scenario() -> None:
        controller = PendingController()
        original_view = controller.snapshot()
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            if not loading_workspaces:
                await app.workers.wait_for_complete()
                await pilot.pause()
            picker = app.screen
            assert isinstance(picker, tui.HierarchyModal)
            cancel = picker.read_cancel
            assert not cancel.is_set()
            if exit_method == "escape":
                await pilot.press("escape")
            elif exit_method == "cancel":
                await pilot.click("#cancel-hierarchy")
            elif exit_method == "dismiss":
                picker.dismiss(None)
            else:
                app.exit()
            await pilot.pause()
        assert cancel.is_set()
        assert controller.scopes == []
        assert controller.snapshot() == original_view

    asyncio.run(scenario())


def test_status_colour_is_literal_and_dates_never_invented() -> None:
    invalid = replace(
        task("one", "Literal [bold] title"),
        status="[red]API text",
        status_color="[blink red]",
    )
    rendered = tui.status_text(invalid)
    assert rendered.plain == "[red]API text"
    assert not rendered.style
    assert tui.date_label("") == "Not set"
    assert tui.date_label("not-a-date") == "Unknown date"


def test_advanced_switch_is_keyboard_accessible_and_does_not_change_saved_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cu, "selection_path", lambda: tmp_path / "selection.json")
    cu.save_selection(_SELECTION)

    async def scenario() -> None:
        controller = DeskController()
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            app.choose_action("switch")
            await pilot.pause()
            await pilot.press("ctrl+a", "ctrl+k", "4", "3", "tab")
            await pilot.press("ctrl+a", "ctrl+k", "9", "0", "1", "tab")
            await pilot.press("ctrl+a", "ctrl+k", "2", "0", "1", "tab")
            await pilot.press("tab", "tab", "enter")
            await pilot.pause()
            await app.poll_view()
            assert app.view.selection == replace(
                _SELECTION, account="43", team="901", list="201"
            )
            assert cu.load_selection() == _SELECTION
            assert app.query_one(tui.TaskQueue).row_count == 0

    asyncio.run(scenario())


def test_async_initial_load_keeps_keyboard_queue_and_status_auth_failure_clears_data() -> (
    None
):
    from test_clickup import Backend, backend_environment

    async def scenario() -> None:
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        app = tui.ClickUpApp(controller)
        try:
            async with app.run_test(size=(140, 40)) as pilot:

                async def ready(*, detail: bool = False) -> None:
                    for _ in range(100):
                        if (
                            app.view.state == "ready"
                            and (not detail or app.view.detail is not None)
                            and not app._rendering
                        ):
                            return
                        await pilot.pause(0.1)
                    raise AssertionError("Task desk did not reach a fresh view")

                await ready()
                queue = app.query_one(tui.TaskQueue)
                assert app.focused is queue
                await pilot.press("j")
                assert queue.cursor_row == 1
                await pilot.press("k", "enter")
                await ready(detail=True)
                await pilot.resize_terminal(60, 24)
                assert app.query_one("#reader-pane").display
                backend.fail = "401"
                app.edit("status")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await app.poll_view()
                assert app.view.state == "authentication"
                assert app.view.detail is None and app.view.listing is None
                assert queue.row_count == 0
        finally:
            controller.close()

    backend = Backend()
    with backend_environment(backend):
        asyncio.run(scenario())

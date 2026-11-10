"""Space aggregation uses the existing HTTP boundary and guarded write engine."""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_clickup import (
    Backend,
    WriteBackend,
    backend_environment,
    settled,
)

from rexdeck.clickup import frontend as cu
from rexdeck.clickup.engine import reads, receipts


class SpaceBackend(WriteBackend):
    filter_assignees = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v2")
        if self.fail:
            return Backend.handle(self, request)
        if path == "/team/10/task":
            query = urllib.parse.parse_qs(request.url.query.decode())
            self.calls.append((path, query, request.method))
            # Two pages with one repeated ID: same deduplication contract as list mode.
            response = super().handle(
                httpx.Request(
                    "GET", "https://api.clickup.com/api/v2/list/100/task", params=query
                )
            )
        else:
            response = super().handle(request)
        data = cu.obj(response.json())
        candidates = cu.rows(data["tasks"]) if "tasks" in data else [data]
        for payload in candidates:
            if payload.get("id") == "a":
                payload["list"] = {"id": "200", "name": "Other list"}
        if path == "/team/10/task" and self.filter_assignees:
            assignees = request.url.params.get_list("assignees[]")
            data["tasks"] = [
                item
                for item in candidates
                if any(
                    str(person["id"]) in assignees
                    for person in cu.rows(item["assignees"])
                )
            ]
        return httpx.Response(response.status_code, json=data, headers=response.headers)


def test_space_pages_filters_and_user_resolution() -> None:
    backend = SpaceBackend()
    selected = cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000")
    with (
        backend_environment(backend),
        patch("rexdeck.clickup.frontend.time.time", return_value=2_000_000_000),
    ):
        service = cu.Service(selected)
        try:
            listing = service.listing()
            assert [task.id for task in listing.tasks] == ["a", "b", "c"]
            assert listing.tasks[0].list_name == "Other list"
            assert listing.pages == 2
            requests = [
                query for path, query, _ in backend.calls if path == "/team/10/task"
            ]
            assert [query["page"] for query in requests] == [["0"], ["1"]]
            for query in requests:
                assert query["space_ids[]"] == ["1000"]
                assert query["assignees[]"] == ["1"]
                assert query["date_updated_gt"] == [
                    str((2_000_000_000 - 14 * 86400) * 1000)
                ]
                assert query["include_closed"] == ["true"]
                assert query["subtasks"] == ["true"]
            assert any(path == "/user" for path, _, _ in backend.calls)
            backend.calls.clear()
            service.filters = cu.WorkspaceFilters("any", "any")
            service.listing()
            query = next(
                query for path, query, _ in backend.calls if path == "/team/10/task"
            )
            assert "assignees[]" not in query and "date_updated_gt" not in query
            backend.calls.clear()
            service.filters = cu.WorkspaceFilters("2", "7d")
            service.listing()
            query = next(
                query for path, query, _ in backend.calls if path == "/team/10/task"
            )
            assert query["assignees[]"] == ["2"]
            assert query["date_updated_gt"] == [str((2_000_000_000 - 7 * 86400) * 1000)]
        finally:
            service.client.close()


def test_space_refuses_other_space_and_other_team() -> None:
    backend = SpaceBackend()
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "999"))
        try:
            with pytest.raises(cu.ClickUpError, match="outside the workspace"):
                service.listing()
            service.selection = replace(service.selection, space="1000")
            backend.routes["/list/200"] = {"space": {"id": "999"}, "statuses": []}
            with pytest.raises(
                cu.ClickUpError, match="outside the selected space"
            ) as outside:
                service.authorize(
                    cu.Task.parse(
                        {
                            "id": "a",
                            "name": "x",
                            "status": {"status": "x"},
                            "list": {"id": "200"},
                            "url": "https://app.clickup.com/t/a",
                        }
                    )
                )
            assert outside.value.state == "task-scope"
            backend.routes["/list/200"] = {"space": {"id": "1001"}, "statuses": []}
            backend.routes["/team/10/space"] = {
                "spaces": [{"id": "1000"}, {"id": "1001"}]
            }
            with pytest.raises(cu.ClickUpError, match="outside the selected space"):
                service.authorize(replace(service.listing().tasks[0], list_id="200"))
        finally:
            service.client.close()


@pytest.fixture
def workspace_controller(
    tmp_path: Path,
) -> Iterator[tuple[SpaceBackend, cu.Controller]]:
    from rexdeck.clickup.engine import registry
    from rexdeck.clickup.engine.records import Member, Topology

    backend = SpaceBackend()
    with (
        backend_environment(backend),
        patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(tmp_path / "config"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
            },
        ),
    ):
        controller = cu.Controller(
            cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000")
        )
        seed = registry.seed_registry(
            account_id="1",
            team_id="10",
            space_id="1000",
            space_name="Space",
            list_id="200",
            list_name="Other",
        )
        registry.rewrite(
            replace(
                seed,
                topology=Topology(
                    spaces=(seed.seeds.space,),
                    folders=(),
                    lists=(seed.seeds.list,),
                    members=(Member("1", "user_one"), Member("2", "user_two")),
                    custom_fields={},
                ),
            ),
            now="2026-11-10T00:00:00Z",
            path=controller.service.registry_path().with_name("list-200.yaml"),
        )
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            yield backend, controller
        finally:
            controller.close()


def test_cross_list_write_uses_home_registry_and_list_bound_approval(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
) -> None:
    backend, controller = workspace_controller
    backend.calls.clear()
    settled(controller.stage("points", "2"))
    # One identity/workspace authorization; no recursive preflights in scoped mutate.
    assert sum(path == "/user" for path, _, _ in backend.calls) == 1
    assert sum(path == "/team/10/space" for path, _, _ in backend.calls) == 1
    change = controller.snapshot().pending
    assert change is not None and change.task_list == "200"
    assert change.approval != replace(change, task_list="100").approval
    settled(controller.confirm("a", change.approval))
    assert backend.write_calls == [("/task/a", {"points": 2})]
    detail = controller.snapshot().detail
    assert detail is not None and detail.task.points == "2"


@pytest.mark.parametrize(
    "failure,state",
    [("429", "rate-limited"), ("network", "network"), ("403", "permission")],
)
def test_space_failures_refresh_without_restart(failure: str, state: str) -> None:
    backend = SpaceBackend()
    with backend_environment(backend):
        controller = cu.Controller(
            cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000")
        )
        try:
            settled(controller.refresh())
            backend.fail = failure
            from rexdeck.clickup.engine import diagnostics

            with pytest.raises(diagnostics.EngineError):
                settled(controller.refresh())
            assert controller.snapshot().error_state == state
            if failure == "429":
                assert "Retry-After: 30" in controller.snapshot().error
            backend.fail = None
            settled(controller.refresh())
            assert controller.snapshot().state == "ready"
            os.environ.pop("CLICKUP_TEST_A")
            with pytest.raises(cu.ClickUpError):
                settled(controller.refresh())
            assert controller.snapshot().state == "missing-credential"
            os.environ["CLICKUP_TEST_A"] = "token-a"
            settled(controller.refresh())
            assert controller.snapshot().state == "ready"
        finally:
            controller.close()


def test_optional_space_config_and_exact_known_keys(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
        selected = cu.Selection("1", "10", "100")
        cu.save_selection(selected)
        document = asdict(selected)
        del document["space"]
        cu.selection_path().write_text(json.dumps(document))
        assert cu.load_selection() == selected
        assert cu.configure(["select", "--space", "1000"]) == 0
        assert cu.load_selection().space == "1000"
        document["unexpected"] = "1000"
        cu.selection_path().write_text(json.dumps(document))
        with pytest.raises(ValueError, match="fields"):
            cu.load_selection()


def test_list_approval_digest_remains_unchanged() -> None:
    mutation = receipts.Mutation("a", "digest", {"points": {"before": 1, "after": 2}})
    change = cu.Change(
        cu.Selection("1", "10", "100"),
        "a",
        "points",
        "2",
        mutation,
        "proposal",
        nonce="fixed",
    )
    selection = asdict(change.selection)
    del selection["space"]
    expected = receipts.digest_json(
        {
            "selection": selection,
            "task": "a",
            "action": "points",
            "value": "2",
            "diff": mutation.diff,
            "field_digest": mutation.field_digest,
            "nonce": "fixed",
        }
    )
    assert change.approval == expected
    assert (
        replace(change, selection=replace(change.selection, space="1000")).approval
        == expected
    )


def test_team_page_ceiling_is_not_a_partial_success() -> None:
    backend = Backend()
    backend.routes["/team/10/task"] = {"tasks": [], "last_page": False}
    with backend_environment(backend), patch.object(reads, "PAGE_LIMIT", 2):
        # A nonempty response with no final page must fail explicitly.
        from test_clickup import task

        backend.routes["/team/10/task"] = {"tasks": [task("a")], "last_page": False}
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000"))
        try:
            from rexdeck.clickup.engine import diagnostics

            with pytest.raises(diagnostics.EngineError) as error:
                service.listing()
            assert error.value.diagnostic.code == "result/page-ceiling"
        finally:
            service.client.close()


def test_workspace_dialog_filters_and_saved_default(tmp_path: Path) -> None:
    import asyncio

    from textual.widgets import Input, Select

    from rexdeck.clickup import tui

    async def scenario() -> None:
        backend = SpaceBackend()
        with (
            backend_environment(backend),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}),
        ):
            selection = cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000")
            cu.save_selection(selection)
            controller = cu.Controller(selection)
            try:
                settled(controller.refresh())
                app = tui.ClickUpApp(controller)
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.click("#workspace")
                    assert isinstance(app.screen, tui.WorkspaceModal)
                    app.screen.query_one("#workspace-assignee", Input).value = "any"
                    app.screen.query_one("#workspace-lookback", Select).value = "7d"
                    await pilot.pause()
                    await pilot.click("#load-workspace")
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    await app.poll_view()
                    assert controller.service.filters == cu.WorkspaceFilters(
                        "any", "7d"
                    )
                    assert cu.load_selection() == selection
                    query = [
                        query
                        for path, query, _ in backend.calls
                        if path == "/team/10/task"
                    ][-1]
                    assert "assignees[]" not in query and "date_updated_gt" in query
                    assert "List" in [
                        str(column.label)
                        for column in app.query_one(tui.TaskQueue).ordered_columns
                    ]
            finally:
                controller.close()

    asyncio.run(scenario())


def test_space_malformed_response_and_task_move_refuse_writes() -> None:
    from test_clickup import task

    from rexdeck.clickup.engine import diagnostics

    backend = SpaceBackend()
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000"))
        try:
            backend.fail = "malformed"
            with pytest.raises(diagnostics.EngineError) as error:
                service.listing()
            assert cu.engine_error(error.value).state == "malformed"
            backend.fail = None
            selected = replace(cu.Task.parse(task("a")), list_id="100")
            with pytest.raises(cu.ClickUpError, match="moved lists"):
                service.mutate(selected, "points", "2")
            assert backend.write_calls == []
            backend.routes["/list/200"] = {
                "space": {"id": "1000"},
                "statuses": [
                    {"status": "Other workflow", "type": "custom", "color": "#abcdef"}
                ],
            }
            assert service.task_statuses(replace(selected, list_id="200")) == (
                cu.Status("Other workflow", "custom", "#abcdef"),
            )
        finally:
            service.client.close()


def test_team_endpoint_rate_limit_is_once_and_refreshable() -> None:
    from rexdeck.clickup.engine import diagnostics

    class LimitedBackend(SpaceBackend):
        limited = True
        team_calls = 0

        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/team/10/task"):
                self.team_calls += 1
                if self.limited:
                    return httpx.Response(429, json={}, headers={"Retry-After": "37"})
            return super().handle(request)

    backend = LimitedBackend()
    with backend_environment(backend):
        controller = cu.Controller(
            cu.Selection("1", "10", "100", "CLICKUP_TEST_A", "1000")
        )
        try:
            with pytest.raises(diagnostics.EngineError):
                settled(controller.refresh())
            assert backend.team_calls == 1
            assert controller.snapshot().error_state == "rate-limited"
            assert "Retry-After: 37" in controller.snapshot().error
            backend.limited = False
            settled(controller.refresh())
            assert controller.snapshot().state == "ready"
            assert backend.team_calls == 3
        finally:
            controller.close()


def test_space_write_readback_after_reassignment_leaves_filter(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
) -> None:
    backend, controller = workspace_controller
    settled(controller.stage("assignee", "2"))
    proposed = controller.snapshot().pending
    assert proposed is not None
    backend.filter_assignees = True
    settled(controller.confirm("a", proposed.approval))
    snapshot = controller.snapshot()
    assert snapshot.state == "ready" and not snapshot.error
    assert snapshot.detail is not None and snapshot.detail.task.assignees[0].id == "2"
    assert snapshot.listing is not None and not snapshot.listing.tasks
    assert "Applied; task no longer matches the current filters" in snapshot.output
    assert "may have succeeded" not in snapshot.output
    assert len(backend.write_calls) == 1


def test_space_confirmation_refuses_changed_home_list(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
) -> None:
    backend, controller = workspace_controller
    settled(controller.stage("points", "2"))
    proposed = controller.snapshot().pending
    assert proposed is not None
    wrong_list = replace(proposed, task_list="100")
    controller.view = replace(controller.snapshot(), pending=wrong_list)
    with pytest.raises(ValueError, match="exact proposal"):
        controller.confirm("a", wrong_list.approval)
    assert backend.write_calls == []


def test_space_changed_remote_value_refuses_digest(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
) -> None:
    from rexdeck.clickup.engine import diagnostics

    backend, controller = workspace_controller
    settled(controller.stage("points", "2"))
    proposed = controller.snapshot().pending
    assert proposed is not None
    backend.points = 3
    with pytest.raises(diagnostics.EngineError) as error:
        settled(controller.confirm("a", proposed.approval))
    assert error.value.diagnostic.code == "state/changed-under-us"
    assert backend.write_calls == [] and controller.snapshot().pending is None


def test_space_task_scope_failure_retains_queue_and_recovers(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
) -> None:
    backend, controller = workspace_controller
    listing = controller.snapshot().listing
    backend.routes["/list/200"] = {"space": {"id": "999"}, "statuses": []}
    with pytest.raises(cu.ClickUpError) as error:
        settled(controller.select("a"))
    assert error.value.state == "task-scope"
    assert controller.snapshot().listing == listing
    assert controller.snapshot().state == "stale"
    backend.routes.clear()
    settled(controller.refresh())
    assert controller.snapshot().state == "ready"
    backend.calls.clear()
    settled(controller.select("a"))
    assert not any(
        path in {"/user", "/team", "/team/10/space"} for path, _, _ in backend.calls
    )
    assert sum(path == "/list/200" for path, _, _ in backend.calls) == 1


@pytest.mark.parametrize("list_id", ["../delete", "100/x", "", "١٠٠", "100\\n"])
def test_task_list_identifier_is_ascii_digits(list_id: str) -> None:
    from test_clickup import task

    payload = task("a")
    payload["list"] = {"id": list_id}
    with pytest.raises(cu.ClickUpError, match="list identifier"):
        cu.Task.parse(payload)


@pytest.mark.parametrize("workspace", [False, True])
def test_narrow_queue_columns_preserve_list_mode(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
    workspace: bool,
) -> None:
    import asyncio

    from textual.events import Resize
    from textual.geometry import Size

    from rexdeck.clickup import tui

    _, controller = workspace_controller
    settled(
        controller.switch_workspace(
            controller.snapshot().selection, workspace=workspace
        )
    )

    async def scenario() -> None:
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(44, 24)) as pilot:
            await pilot.pause()
            table = app.query_one(tui.TaskQueue)
            table.on_resize(Resize(Size(30, 10), Size(30, 10)))
            columns = table.ordered_columns
            assert [str(column.label) for column in columns] == (
                ["Task", "Status", "List"] if workspace else ["Task", "Status"]
            )
            assert columns[0].width == (5 if workspace else 15)
            assert columns[1].width == 9
            app.view = replace(app.view, listing=None, state="loading")
            app._render_queue()
            assert len(table.ordered_columns) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["pane-local", "list-mode", "failed", "success"])
def test_workspace_save_only_after_valid_matching_space_load(
    workspace_controller: tuple[SpaceBackend, cu.Controller],
    case: str,
) -> None:
    import asyncio

    from rexdeck.clickup import tui

    backend, controller = workspace_controller
    saved = replace(controller.snapshot().selection, space="")
    cu.save_selection(saved)
    original_file = cu.selection_path().read_bytes()
    if case == "pane-local":
        settled(controller.switch(replace(saved, list="200")))
    if case == "failed":
        backend.fail = "403"
    pane = replace(controller.snapshot().selection, space="1000")

    async def scenario() -> None:
        app = tui.ClickUpApp(controller)
        async with app.run_test(size=(100, 36)) as pilot:
            app.workspace_selected((pane, case != "list-mode", cu.WorkspaceFilters()))
            await app.workers.wait_for_complete()
            await pilot.pause()
            if case == "success":
                assert cu.load_selection() == replace(saved, space="1000")
            else:
                assert cu.selection_path().read_bytes() == original_file
            if case == "pane-local":
                assert "pane only" in app._notice
                assert controller.snapshot().selection.list == "200"

    asyncio.run(scenario())

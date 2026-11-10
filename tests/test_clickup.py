"""Consumer-visible ClickUp contracts at the HTTP and native-host boundaries."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from rexdeck import dependencies, integrations, lifecycle
from rexdeck.clickup import frontend as cu
from rexdeck.clickup.engine import diagnostics, registry, runtime
from rexdeck.clickup.engine.records import CustomField, Member, Topology
from rexdeck.clickup.text import clean, single_line


def task(identifier: str, name: str = "Task") -> dict[str, object]:
    return {
        "id": identifier,
        "custom_id": "PROJ-1" if identifier == "a" else None,
        "name": name,
        "status": {"status": "native review", "type": "custom", "color": "#123456"},
        "list": {"id": "100"},
        "description": "Long description\nsecond line",
        "date_updated": "123",
        "url": f"https://app.clickup.com/t/{identifier}",
        "assignees": [],
        "due_date": None,
        "custom_fields": [],
    }


class Backend:
    """A deterministic HTTP boundary, not an alternate implementation of Service."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]], str]] = []
        self.fail: str | None = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.delay_a = False
        self.empty = False
        self.missing_metadata = False
        self.ecode: str | None = None
        self.comments_requested = 0
        self.changed = False
        self.routes: dict[str, dict[str, object]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).startswith("https://api.clickup.com/api/v2/")
        path = request.url.path.removeprefix("/api/v2")
        query = urllib.parse.parse_qs(request.url.query.decode())
        self.calls.append((path, query, request.method))
        if self.fail:
            if self.fail == "network":
                raise httpx.ConnectError("unavailable", request=request)
            if self.fail == "malformed":
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(
                int(self.fail),
                json={"ECODE": self.ecode},
                headers={"Retry-After": "30"} if self.fail == "429" else {},
            )
        if path in self.routes:
            return httpx.Response(200, json=self.routes[path])
        data: dict[str, object]
        if path == "/user":
            data = {
                "user": {
                    "id": 2 if request.headers.get("Authorization") == "token-b" else 1
                }
            }
        elif path == "/team":
            data = {
                "teams": [
                    {"id": "10", "name": "Engineering workspace"},
                    {"id": "20", "name": "Sales workspace"},
                ]
            }
        elif path.endswith("/space"):
            data = {"spaces": [{"id": "1000"}]}
        elif path in ("/list/100", "/list/200"):
            data = {
                "id": path.rsplit("/", 1)[-1],
                "name": "Selected list",
                "space": {"id": "1000", "name": "Product"},
                "statuses": [
                    {"status": "native review", "type": "custom", "color": "#123456"}
                ],
            }
        elif path == "/list/100/task":
            if self.delay_a:
                self.started.set()
                assert self.release.wait(5)
            page = query["page"][0]
            data = {
                "tasks": [task("a", "Changed" if self.changed else "First"), task("b")]
                if page == "0"
                else [task("b"), task("c", "Last page")],
                "last_page": page == "1",
            }
            if self.empty or (self.missing_metadata and page == "2"):
                data = {"tasks": [], "last_page": True}
            if self.missing_metadata:
                data.pop("last_page", None)
        elif path == "/list/200/task":
            data = {"tasks": [task("z", "Account B only")], "last_page": True}
        elif path.endswith("/comment"):
            self.comments_requested += 1
            data = {
                "comments": [
                    {
                        "id": str(i),
                        "date": str(2000 - i),
                        "comment_text": f"Comment {i}",
                    }
                    for i in (range(25) if "start" not in query else range(24, 28))
                ]
            }
        elif path in ("/task/a", "/task/PROJ-1"):
            data = task("a", "Changed" if self.changed else "First")
        elif path == "/task/z":
            data = task("z", "Account B only")
        else:
            raise AssertionError(f"Unexpected read endpoint: {path}")
        return httpx.Response(200, json=data)


@contextmanager
def backend_environment(backend: Backend) -> Iterator[None]:
    real_client = httpx.Client

    def isolated_client(**kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(
            lambda request: backend.handle(request)
        )
        return real_client(**kwargs)  # type: ignore[arg-type]

    with (
        patch.dict(
            os.environ, {"CLICKUP_TEST_A": "token-a", "CLICKUP_TEST_B": "token-b"}
        ),
        patch.object(httpx, "Client", side_effect=isolated_client),
        patch.object(time, "sleep"),
    ):
        yield


def settled[T](future: concurrent.futures.Future[T]) -> None:
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    future.result(timeout=5)
    assert event.wait(5)


def test_all_pages_deduplicated_and_custom_id_detail_comments() -> None:
    backend = Backend()
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        listing = service.listing()
        assert listing.pages == 2
        assert [t.id for t in listing.tasks] == ["a", "b", "c"]
        assert listing.statuses == (cu.Status("native review", "custom", "#123456"),)
        assert backend.calls[-1][1]["page"] == ["1"]
        detail = service.detail(listing.tasks[0], "PROJ-1")
        assert detail.task.id == "a"
        assert detail.task.custom_id == "PROJ-1"
        assert detail.task.description == "Long description\nsecond line"
        assert detail.task.status == "native review"
        assert detail.task.status_type == "custom"
        assert detail.task.status_color == "#123456"
        assert len(detail.comments) == 28
        custom_request = next(
            query for path, query, _ in backend.calls if path == "/task/PROJ-1"
        )
        assert custom_request["custom_task_ids"] == ["true"]
        assert custom_request["team_id"] == ["10"]
        older_request = next(
            query
            for path, query, _ in backend.calls
            if path == "/task/a/comment" and "start" in query
        )
        assert older_request == {"start": ["1976"], "start_id": ["24"]}


def test_controller_comments_merge_and_refresh_selected_detail() -> None:
    backend = Backend()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            settled(controller.select("PROJ-1"))
            detail = controller.snapshot().detail
            assert detail is not None and len(detail.comments) == 28
            backend.changed = True
            settled(controller.refresh())
            view = controller.snapshot()
            assert view.detail is not None and view.detail.task.name == "Changed"
            assert backend.comments_requested == 4
        finally:
            controller.close()


def test_delayed_account_a_response_cannot_repopulate_b() -> None:
    backend = Backend()
    backend.delay_a = True
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        first = controller.refresh()
        try:
            assert backend.started.wait(5)
            second = controller.switch(cu.Selection("2", "20", "200", "CLICKUP_TEST_B"))
            cleared = controller.snapshot()
            assert cleared.detail is None
            settled(second)
            backend.release.set()
            settled(first)
            view = controller.snapshot()
            assert view.selection.account == "2"
            assert view.listing is not None
            assert [t.id for t in view.listing.tasks] == ["z"]
            assert view.detail is None
            with pytest.raises(ValueError, match="loaded list"):
                controller.select("a")
        finally:
            backend.release.set()
            controller.close()


def test_switch_clears_selected_detail_and_opener_target() -> None:
    backend = Backend()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            settled(controller.switch(cu.Selection("2", "20", "200", "CLICKUP_TEST_B")))
            assert controller.snapshot().detail is None
            assert controller.snapshot().listing is not None
        finally:
            controller.close()


@pytest.mark.parametrize(
    ("failure", "state"),
    [
        ("401", "authentication"),
        ("403", "permission"),
        ("404", "not-found"),
        ("429", "rate-limited"),
        ("network", "network"),
        ("malformed", "malformed"),
    ],
)
def test_http_error_distinctions(failure: str, state: str) -> None:
    backend = Backend()
    backend.fail = failure
    with backend_environment(backend), pytest.raises(diagnostics.EngineError) as raised:
        cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A")).listing()
    assert cu.engine_error(raised.value).state == state
    assert "token-a" not in str(raised.value)
    if state == "rate-limited":
        assert "Retry-After: 30" in str(cu.engine_error(raised.value))


def test_network_error_retains_explicit_stale_data_but_auth_clears_it() -> None:
    backend = Backend()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            for failure, expected in (("network", "stale"), ("401", "authentication")):
                backend.fail = failure
                future = controller.refresh()
                event = threading.Event()
                future.add_done_callback(lambda _: event.set())
                with pytest.raises(diagnostics.EngineError):
                    future.result(timeout=5)
                assert event.wait(5)
                assert controller.snapshot().state == expected
            assert controller.snapshot().listing is None
            assert controller.snapshot().detail is None
        finally:
            controller.close()


def test_account_binding_and_missing_credential() -> None:
    backend = Backend()
    with backend_environment(backend), pytest.raises(cu.ClickUpError) as raised:
        cu.Service(cu.Selection("2", "10", "100", "CLICKUP_TEST_A")).listing()
    assert raised.value.state == "account-mismatch"
    assert [path for path, _, _ in backend.calls] == ["/user"]
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(cu.ClickUpError) as missing,
    ):
        cu.Client(cu.Selection("1", "10", "100", "CLICKUP_MISSING_TOKEN_REF")).get(
            "/user"
        )
    assert missing.value.state == "missing-credential"


@pytest.mark.parametrize(
    "url",
    [
        "http://app.clickup.com/t/a",
        "https://app.clickup.com.evil/t/a",
        "https://user@app.clickup.com/t/a",
        "https://app.clickup.com:443/t/a",
        "https://app.clickup.com/t/b",
        "https://app.clickup.com/t/a?next=evil",
        "https://app.clickup.com/t/a#x",
        "https://app.clickup.com/t/a\n",
    ],
)
def test_deep_links_reject_noncanonical_destinations(url: str) -> None:
    parsed = replace(cu.Task.parse(task("a")), url=url)
    with pytest.raises(cu.ClickUpError):
        cu.validated_url(parsed)


def test_opener_receives_exactly_one_url_argument(tmp_path: Path) -> None:
    opener = tmp_path / "browser-stub"
    destination = tmp_path / "links.json"
    opener.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(destination)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    opener.chmod(0o700)
    with patch.dict(os.environ, {"BROWSER": str(opener)}):
        cu.open_task(cu.Task.parse(task("a")))
    assert json.loads(destination.read_text()) == ["https://app.clickup.com/t/a"]


def test_control_sequences_and_credential_echo_do_not_render() -> None:
    assert (
        clean("safe\x1b[31mRED\x1b[0m\x1b]52;c;evil\x07\x00\x9b\ntext")
        == "safeRED\ntext"
    )
    backend = Backend()
    with backend_environment(backend):
        client = cu.Client(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        with patch.object(
            backend,
            "handle",
            return_value=httpx.Response(200, json={"echo": "token-a"}),
        ):
            assert client.get("/user") == {"echo": "[REDACTED]"}
        with pytest.raises(diagnostics.EngineError):
            client.get("https://evil.example/user")
        with pytest.raises(diagnostics.EngineError):
            client.get("/task/a/../delete")


def test_selection_only_persists_nonsecret_reference(tmp_path: Path) -> None:
    with patch.dict(
        os.environ, {"XDG_CONFIG_HOME": str(tmp_path), "CLICKUP_TEST_A": "token-a"}
    ):
        selected = cu.Selection("1", "10", "100", "CLICKUP_TEST_A")
        cu.save_selection(selected)
        assert cu.load_selection() == selected
        saved = cu.selection_path().read_text()
        assert "CLICKUP_TEST_A" in saved and "token-a" not in saved
        assert cu.selection_path().stat().st_mode & 0o777 == 0o600


def test_owned_frontend_qualifies_package_interpreter_without_path_probe(
    tmp_path: Path,
) -> None:
    descriptor = integrations.get("clickup")
    assert descriptor.versions == ()
    assert descriptor.args == ("-I", "-m", "rexdeck.clickup")
    with (
        patch.object(
            dependencies, "_resolve", side_effect=AssertionError("PATH probe")
        ),
        patch.object(
            dependencies, "_probe", side_effect=AssertionError("version probe")
        ),
    ):
        qualified = dependencies.ensure("clickup")
    assert qualified.path == str(Path(sys.executable).absolute())
    setup = lifecycle.Setup(
        tmp_path,
        tmp_path,
        tmp_path / "init.lua",
        tmp_path,
        "unix:///tmp/private.sock",
        plugin="clickup",
    )
    with patch.object(
        lifecycle,
        "_executable",
        side_effect=lambda name: (
            "/trusted/rex"
            if name == "rex"
            else (_ for _ in ()).throw(AssertionError("PATH frontend lookup"))
        ),
    ):
        tools = lifecycle._selected_tools(setup)
    assert tools[descriptor.tool] == qualified.path
    assert tools["rex"] == "/trusted/rex"


def test_missing_pagination_metadata_probes_until_empty() -> None:
    backend = Backend()
    backend.missing_metadata = True
    with backend_environment(backend):
        listing = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A")).listing()
    assert listing.pages == 3
    assert [item.id for item in listing.tasks] == ["a", "b", "c"]
    assert backend.calls[-1][1]["page"] == ["2"]


def test_empty_success_is_not_a_transport_failure() -> None:
    backend = Backend()
    backend.empty = True
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            view = controller.snapshot()
            assert view.state == "empty" and not view.error
            assert view.listing is not None and view.listing.tasks == ()
        finally:
            controller.close()


def test_http_401_permission_code_is_not_mislabeled_expired_authentication() -> None:
    backend = Backend()
    backend.fail, backend.ecode = "401", "OAUTH_023"
    with backend_environment(backend), pytest.raises(diagnostics.EngineError) as raised:
        cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A")).listing()
    assert cu.engine_error(raised.value).state == "permission"


def test_malformed_custom_id_is_a_visible_error_not_a_stuck_worker() -> None:
    data = task("a")
    data["custom_id"] = "../not-a-task"
    with pytest.raises(cu.ClickUpError) as raised:
        cu.Task.parse(data)
    assert raised.value.state == "malformed"


def test_client_redirect_handler_refuses_authorization_forwarding() -> None:
    paths: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(302, headers={"Location": "https://evil.example/target"})

    real_client = httpx.Client

    def isolated_client(**kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(redirect)
        return real_client(**kwargs)  # type: ignore[arg-type]

    with (
        patch.dict(os.environ, {"CLICKUP_TEST_A": "token-a"}),
        patch.object(httpx, "Client", side_effect=isolated_client),
    ):
        client = cu.Client(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            with pytest.raises(diagnostics.EngineError) as raised:
                client.get("/user")
            assert raised.value.diagnostic.evidence["status"] == 302
            assert paths == ["/api/v2/user"]
        finally:
            client.close()


class WriteBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self.status = "native review"
        self.due: object = None
        self.assignees: list[dict[str, object]] = []
        self.field_value: object = "before"
        self.description = "Original body"
        self.points: object = None
        self.posted_comment = ""
        self.write_calls: list[tuple[str, dict[str, object]]] = []
        self.fail_write = False
        self.reject_write = False
        self.reject_reads_after_write = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v2")
        if (
            request.method == "GET"
            and self.reject_reads_after_write
            and self.write_calls
        ):
            return httpx.Response(400, json={"ECODE": "ITEM_000"})
        if request.method in {"PUT", "POST"}:
            assert path in {"/task/a", "/task/a/comment", "/task/a/field/field-x"}
            body = cu.obj(json.loads(request.content))
            self.write_calls.append((path, body))
            if self.fail_write:
                return httpx.Response(503, json={})
            if self.reject_write:
                return httpx.Response(
                    400,
                    json={"err": "not a valid points selection", "ECODE": "ITEM_223"},
                )
            if path.endswith("/comment"):
                self.posted_comment = str(body["comment_text"])
            elif "/field/" in path:
                self.field_value = body["value"]
            elif "status" in body:
                self.status = str(body["status"])
            elif "due_date" in body:
                self.due = body["due_date"]
            elif "markdown_content" in body:
                self.description = str(body["markdown_content"])
            elif "points" in body:
                self.points = body["points"]
            elif "assignees" in body:
                self.assignees = [{"id": 2, "username": "user_two"}]
            return httpx.Response(200, json={})
        response = super().handle(request)
        data = cu.obj(response.json())
        if path == "/list/100":
            data["statuses"] = [
                *cu.rows(data["statuses"]),
                {"status": "active", "type": "custom", "color": "#ffffff"},
            ]
        candidates = cu.rows(data["tasks"]) if "tasks" in data else [data]
        for payload in candidates:
            if payload.get("id") == "a":
                payload["status"] = {
                    "status": self.status,
                    "type": "custom",
                    "color": "#123456",
                }
                payload["due_date"] = self.due
                payload["assignees"] = self.assignees
                payload["markdown_description"] = self.description
                payload["points"] = self.points
                payload["custom_fields"] = [
                    {
                        "id": "field-x",
                        "name": "Disposable",
                        "type": "short_text",
                        "value": self.field_value,
                    }
                ]
        if (
            path.endswith("/comment")
            and self.posted_comment
            and not request.url.params.get("start")
        ):
            data["comments"] = [
                {
                    "id": "100",
                    "date": "3000",
                    "comment_text": self.posted_comment,
                },
                *cu.rows(data["comments"])[:24],
            ]
        return httpx.Response(200, json=data)


@contextmanager
def writable_controller(tmp_path: Path) -> Iterator[tuple[WriteBackend, cu.Controller]]:
    backend = WriteBackend()
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
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        seed = registry.seed_registry(
            account_id="1",
            team_id="10",
            space_id="1000",
            space_name="Sample",
            list_id="100",
            list_name="Selected list",
        )
        topology = Topology(
            spaces=(seed.seeds.space,),
            folders=(),
            lists=(seed.seeds.list,),
            members=(Member("1", "user_one"), Member("2", "user_two")),
            custom_fields={
                "100": (CustomField("field-x", "Disposable", "short_text", ()),)
            },
        )
        registry.rewrite(
            replace(seed, topology=topology),
            now=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            path=controller.service.registry_path(),
        )
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            yield backend, controller
        finally:
            controller.close()


@pytest.mark.parametrize(
    ("action", "value", "endpoint"),
    [
        ("status", "active", "/task/a"),
        ("assignee", "2", "/task/a"),
        ("due", "2026-12-01", "/task/a"),
        ("field", "Disposable=after", "/task/a/field/field-x"),
        ("description", "Updated **Markdown** body", "/task/a"),
        ("points", "2.5", "/task/a"),
        ("comment", "Sample write proof", "/task/a/comment"),
    ],
)
def test_engine_write_requires_exact_confirmation_and_refreshes(
    tmp_path: Path, action: str, value: str, endpoint: str
) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage(action, value))
        proposed = controller.snapshot().pending
        assert proposed is not None and proposed.task == "a"
        assert not backend.write_calls
        with pytest.raises(ValueError):
            controller.confirm("wrong-task", proposed.approval)
        with pytest.raises(ValueError):
            controller.confirm("a", "wrong-digest")
        settled(controller.confirm("a", proposed.approval))
        assert len(backend.write_calls) == 1 and backend.write_calls[0][0] == endpoint
        view = controller.snapshot()
        assert view.pending is None and view.state == "ready"
        assert "Refreshed task and comments" in view.output
        assert view.detail is not None
        if action == "status":
            assert view.detail.task.status == "active"
        if action == "comment":
            assert any(comment.body == value for comment in view.detail.comments)
        if action == "description":
            assert view.detail.task.description == value
        if action == "points":
            assert view.detail.task.points == "2.5"
        with pytest.raises(ValueError):
            controller.confirm("a", proposed.approval)
        assert len(backend.write_calls) == 1


@pytest.mark.parametrize("expiry", ["refresh", "cancel", "switch"])
def test_confirmation_expires_with_context(tmp_path: Path, expiry: str) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        proposed = controller.snapshot().pending
        assert proposed is not None
        if expiry == "refresh":
            settled(controller.refresh())
        elif expiry == "cancel":
            controller.cancel()
        else:
            settled(controller.switch(cu.Selection("2", "20", "200", "CLICKUP_TEST_B")))
        with pytest.raises(ValueError):
            controller.confirm("a", proposed.approval)
        assert not backend.write_calls and controller.snapshot().pending is None


def test_changed_remote_value_refuses_staged_digest(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        proposed = controller.snapshot().pending
        assert proposed is not None
        backend.status = "moved remotely"
        with pytest.raises(diagnostics.EngineError):
            settled(controller.confirm("a", proposed.approval))
        assert not backend.write_calls
        assert controller.snapshot().pending is None

        snapshot = controller.snapshot()
        assert "Proposal not sent:" in snapshot.output
        assert "Nothing was written" in snapshot.output
        assert "status no longer holds what the dry run recorded" in snapshot.output
        assert "NOT retried" in snapshot.output
        assert "Refresh before any new proposal." in snapshot.output
        assert "may have succeeded" not in snapshot.output


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        ("unavailable", "Write may have succeeded"),
        ("rejected", "ClickUp refused this write; it was not applied"),
        ("refresh-rejected", "Write may have succeeded"),
    ],
)
def test_failed_write_is_not_retried_or_replayable(
    tmp_path: Path, failure: str, outcome: str
) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("points", "2.5"))
        proposed = controller.snapshot().pending
        assert proposed is not None
        backend.fail_write = failure == "unavailable"
        backend.reject_write = failure == "rejected"
        backend.reject_reads_after_write = failure == "refresh-rejected"
        with pytest.raises((diagnostics.EngineError, cu.ClickUpError)):
            settled(controller.confirm("a", proposed.approval))
        if failure == "refresh-rejected":
            assert backend.points == 2.5
        assert len(backend.write_calls) == 1
        snapshot = controller.snapshot()
        assert "NOT retried" in snapshot.output and outcome in snapshot.output
        if failure == "rejected":
            assert snapshot.error_state == "rejected"
            assert "ITEM_223" in snapshot.error
            assert "not a valid points selection" not in snapshot.error
        with pytest.raises(ValueError):
            controller.confirm("a", proposed.approval)


class _UnexpectedRegistryError(Exception):
    """Stands in for registry/cache errors outside the handled error types."""


@pytest.mark.parametrize("dispatched", [False, True])
def test_unexpected_write_failure_ends_writing_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        proposed = controller.snapshot().pending
        assert proposed is not None

        def broken(*args: object, **kwargs: object) -> object:
            if dispatched:
                writer = args[0]
                assert isinstance(writer, cu.Service)
                writer.client.put("/task/a", {"status": "active"})
            raise _UnexpectedRegistryError("corrupt registry")

        monkeypatch.setattr(cu.Service, "mutate", broken)
        with pytest.raises(_UnexpectedRegistryError):
            settled(controller.confirm("a", proposed.approval))
        snapshot = controller.snapshot()
        assert snapshot.state != "writing"
        assert "NOT retried" in snapshot.output
        assert snapshot.error_state == (
            "write-uncertain" if dispatched else "write-not-sent"
        )
        assert (
            "Write may have succeeded" if dispatched else "Proposal not sent:"
        ) in snapshot.output
        assert ("Nothing was written" in snapshot.output) is not dispatched
        assert len(backend.write_calls) == int(dispatched)
        monkeypatch.undo()
        settled(controller.refresh())
        assert controller.snapshot().state == "ready"


def test_switch_during_confirmed_preflight_prevents_stale_write(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        proposed = controller.snapshot().pending
        assert proposed is not None
        started, release = threading.Event(), threading.Event()
        original = cu.Service.mutate

        def blocked(
            service: cu.Service,
            task: cu.Task,
            action: str,
            value: str,
            digest: str | None = None,
        ) -> runtime.Outcome:
            if digest is not None:
                started.set()
                assert release.wait(5)
            return original(service, task, action, value, digest)

        with patch.object(cu.Service, "mutate", blocked):
            future = controller.confirm("a", proposed.approval)
            try:
                assert started.wait(5)
                settled(
                    controller.switch(cu.Selection("2", "20", "200", "CLICKUP_TEST_B"))
                )
            finally:
                release.set()
            with pytest.raises(cu.ClickUpError, match="no longer current"):
                settled(future)
        assert not backend.write_calls
        assert controller.snapshot().selection.account == "2"
        assert controller.snapshot().detail is None


def test_proposal_code_binds_value_diff_scope_and_nonce(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        first = controller.snapshot().pending
        assert first is not None
        settled(controller.stage("status", "native review"))
        second = controller.snapshot().pending
        assert second is not None
        assert first.mutation.field_digest == second.mutation.field_digest
        assert first.approval != second.approval
        with pytest.raises(ValueError):
            controller.confirm("a", first.approval)
        for changed in (
            replace(second, task="other"),
            replace(second, action="comment"),
            replace(second, value="different"),
            replace(second, selection=cu.Selection("2", "10", "100", "CLICKUP_TEST_B")),
            replace(
                second, mutation=replace(second.mutation, diff={"different": True})
            ),
            replace(second, mutation=replace(second.mutation, field_digest="changed")),
            replace(second, nonce="new-nonce"),
        ):
            assert changed.approval != second.approval
        settled(controller.stage("status", "native review"))
        third = controller.snapshot().pending
        assert third is not None and third.approval != second.approval
        assert not backend.write_calls


def test_proposal_and_inspect_errors_keep_fresh_list_ready(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (_, controller):
        with pytest.raises(ValueError, match="exact native status"):
            settled(controller.stage("status", "not-a-status"))
        assert controller.snapshot().state == "ready"
        assert controller.snapshot().listing is not None
        with patch.object(
            cu.Service,
            "read",
            side_effect=cu.ClickUpError("registry/unsynced", "Run sync"),
        ):
            with pytest.raises(cu.ClickUpError):
                settled(controller.inspect("tasks"))
        assert controller.snapshot().state == "ready"
        assert controller.snapshot().detail is not None


def test_empty_identity_never_selects_empty_custom_id(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        before = len(backend.calls)
        for identity in ("", " ", "\t"):
            with pytest.raises(ValueError, match="nonempty"):
                controller.select(identity)
        assert len(backend.calls) == before
        assert controller.snapshot().detail is not None


def test_refresh_cannot_hide_in_flight_write_outcome(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (backend, controller):
        settled(controller.stage("status", "active"))
        proposed = controller.snapshot().pending
        assert proposed is not None
        started, release = threading.Event(), threading.Event()
        original = cu.Service.mutate

        def blocked(
            service: cu.Service,
            task: cu.Task,
            action: str,
            value: str,
            digest: str | None = None,
        ) -> runtime.Outcome:
            if digest is not None:
                started.set()
                assert release.wait(5)
            return original(service, task, action, value, digest)

        with patch.object(cu.Service, "mutate", blocked):
            future = controller.confirm("a", proposed.approval)
            try:
                assert started.wait(5)
                with pytest.raises(ValueError, match="write outcome"):
                    controller.refresh()
                assert controller.snapshot().state == "writing"
            finally:
                release.set()
            settled(future)
        assert len(backend.write_calls) == 1
        assert controller.snapshot().state == "ready"
        assert "Refreshed task" in controller.snapshot().output


@pytest.mark.parametrize("root", ["", "relative"])
def test_frontend_cache_rejects_nonabsolute_xdg(tmp_path: Path, root: str) -> None:
    with writable_controller(tmp_path) as (_, controller):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": root}):
            with pytest.raises(cu.ClickUpError, match="nonempty absolute"):
                settled(controller.stage("status", "active"))
        assert controller.snapshot().state == "ready"


def test_frontend_cache_restricts_existing_parent_permissions(tmp_path: Path) -> None:
    with writable_controller(tmp_path) as (_, controller):
        cache_root = tmp_path / "cache"
        directories = []
        for component in ("rexdeck", "clickup", "1", "10", "100"):
            cache_root /= component
            cache_root.mkdir(parents=True, exist_ok=True)
            cache_root.chmod(0o755)
            directories.append(cache_root)
        settled(controller.stage("status", "active"))
        assert all(path.stat().st_mode & 0o777 == 0o700 for path in directories)
        assert (cache_root / "cache.sqlite3").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "name", ["PATH", "HOME", "CLICKUP_", "CLICKUP_lower", "CLICKUP_TOKEN;bad"]
)
def test_credential_reference_requires_clickup_namespace(name: str) -> None:
    with pytest.raises(ValueError):
        cu.Selection("1", "10", "100", name)


def test_opener_does_not_inherit_clickup_credentials(tmp_path: Path) -> None:
    opener = tmp_path / "opener"
    opener.write_text("#!/bin/sh\nexit 0\n")
    opener.chmod(0o700)
    with (
        patch.dict(
            os.environ,
            {
                "BROWSER": str(opener),
                "CLICKUP_API_TOKEN": "synthetic-default",
                "CLICKUP_TEST_A": "synthetic-selected",
                "CLICKUP_TEST_B": "synthetic-other",
                "SAFE_SETTING": "retained",
            },
        ),
        patch("rexdeck.clickup.frontend.subprocess.run") as opened,
    ):
        cu.open_task(cu.Task.parse(task("a")), "CLICKUP_TEST_A")
    environment = opened.call_args.kwargs["env"]
    assert not any(name.startswith("CLICKUP_") for name in environment)
    assert environment["SAFE_SETTING"] == "retained"


def test_labels_are_single_line_and_invisible_formats_are_removed() -> None:
    payload = task("a", "name\nspoof\u202e\u200b\u2066")
    payload["description"] = "description\nstill multiline\u200d"
    parsed = cu.Task.parse(payload)
    assert parsed.name == "name spoof"
    assert parsed.description == "description\nstill multiline"
    assert single_line("one\r\ntwo\rthree\tfour") == "one two three four"
    assert clean("normal\u202e\u2067\u200b\ufeff") == "normal"


def test_optional_display_metadata_is_real_sanitized_and_not_invented() -> None:
    payload = task("a")
    payload.update(
        assignees=[{"id": 7, "username": "Dev\n\x1b[31mAlice\u202e"}],
        due_date="1796083200000",
        priority={"id": "2", "priority": "High\n\u200b"},
    )
    parsed = cu.Task.parse(payload)
    assert parsed.assignees == (cu.Assignee("7", "Dev Alice"),)
    assert parsed.due == "1796083200000"
    assert parsed.priority == "High "
    minimal = task("b")
    for key in ("assignees", "due_date", "priority"):
        minimal.pop(key, None)
    absent = cu.Task.parse(minimal)
    assert absent.assignees == () and absent.due == "" and absent.priority == ""
    backend = Backend()
    backend.routes["/task/a/comment"] = {
        "comments": [
            {
                "id": "1",
                "date": "2000",
                "comment_text": "Body",
                "user": {"username": "Author\n\x1b]52;c;evil\x07\u202e"},
            },
            {"id": "2", "date": "1000", "comment_text": "No author"},
        ]
    }
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            listing = service.listing()
            assert listing.workspace_name == "Engineering workspace"
            assert listing.space_name == "Product"
            detail = service.detail(listing.tasks[0], "a")
            assert {comment.id: comment.author for comment in detail.comments} == {
                "1": "Author ",
                "2": "",
            }
        finally:
            service.client.close()


def test_hierarchy_enumerates_foldered_and_folderless_unarchived_lists() -> None:
    backend = Backend()
    backend.routes.update(
        {
            "/team/20/space": {
                "spaces": [
                    {"id": "2000", "name": "Sales\n\u202e"},
                    {"id": "3000", "name": "Old", "archived": True},
                ]
            },
            "/space/2000/folder": {
                "folders": [
                    {
                        "id": "2100",
                        "name": "Deals\x1b[31m",
                        "lists": [
                            {"id": "201", "name": "Pipeline"},
                            {"id": "202", "name": "Old", "archived": True},
                        ],
                    },
                    {
                        "id": "2200",
                        "name": "Deals",
                        "lists": [{"id": "206", "name": "Other pipeline"}],
                    },
                    {"name": "Old", "archived": True, "lists": [{"id": "203"}]},
                ]
            },
            "/space/2000/list": {
                "lists": [
                    {"id": "204", "name": "Inbox"},
                    {"id": "205", "archived": True},
                ]
            },
        }
    )
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            assert service.workspaces() == (
                cu.Workspace("10", "Engineering workspace"),
                cu.Workspace("20", "Sales workspace"),
            )
            assert service.hierarchy("20") == (
                cu.ListChoice("201", "Pipeline", "2000", "Sales ", "Deals", "2100"),
                cu.ListChoice(
                    "206", "Other pipeline", "2000", "Sales ", "Deals", "2200"
                ),
                cu.ListChoice("204", "Inbox", "2000", "Sales "),
            )
            hierarchy_calls = [
                query
                for path, query, _ in backend.calls
                if path.startswith(("/team/20/", "/space/"))
            ]
            assert hierarchy_calls and all(
                query == {"archived": ["false"]} for query in hierarchy_calls
            )
        finally:
            service.client.close()


@pytest.mark.parametrize("operation", ["workspaces", "hierarchy"])
def test_hierarchy_rejects_another_token_account_before_discovery(
    operation: str,
) -> None:
    backend = Backend()
    with backend_environment(backend):
        service = cu.Service(cu.Selection("2", "10", "100", "CLICKUP_TEST_A"))
        try:
            with pytest.raises(cu.ClickUpError) as raised:
                if operation == "workspaces":
                    service.workspaces()
                else:
                    service.hierarchy()
            assert raised.value.state == "account-mismatch"
            assert [path for path, _, _ in backend.calls] == ["/user"]
        finally:
            service.client.close()


@pytest.mark.parametrize("team", [None, "99"])
def test_hierarchy_refuses_inaccessible_selected_or_requested_workspace(
    team: str | None,
) -> None:
    backend = Backend()
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "99", "100", "CLICKUP_TEST_A"))
        try:
            with pytest.raises(cu.ClickUpError) as raised:
                service.hierarchy(team)
            assert raised.value.state == "permission"
            assert [path for path, _, _ in backend.calls] == ["/user", "/team"]
        finally:
            service.client.close()


def test_native_status_choices_use_task_home_list_not_tracked_list() -> None:
    backend = Backend()
    backend.routes["/list/300"] = {
        "statuses": [{"status": "Home\nreview", "type": "custom", "color": "#abcdef"}]
    }
    with backend_environment(backend):
        service = cu.Service(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            home_task = replace(cu.Task.parse(task("a")), list_id="300")
            assert service.task_statuses(home_task) == (
                cu.Status("Home review", "custom", "#abcdef"),
            )
        finally:
            service.client.close()


@pytest.mark.parametrize("failure", ["401", "403"])
@pytest.mark.parametrize("current_scope", [False, True])
@pytest.mark.parametrize("preflight", ["/user", "/team"])
def test_dialog_reads_clear_authoritative_access_failures_only(
    failure: str, current_scope: bool, preflight: str
) -> None:
    backend = Backend()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            original_handle = backend.handle

            def denied(request: httpx.Request) -> httpx.Response:
                if request.url.path == f"/api/v2{preflight}":
                    return httpx.Response(int(failure), json={})
                return original_handle(request)

            backend.handle = denied  # type: ignore[method-assign]
            future = controller.read(
                lambda service: service.hierarchy("20"), current_scope=current_scope
            )
            with pytest.raises(diagnostics.EngineError):
                future.result(timeout=5)
            view = controller.snapshot()
            assert view.listing is None and view.detail is None
            assert view.state == (
                "authentication" if failure == "401" else "permission"
            )
            assert view.selection == cu.Selection("1", "10", "100", "CLICKUP_TEST_A")
        finally:
            controller.close()


def test_late_dialog_access_failure_cannot_clear_a_newer_refresh() -> None:
    backend = Backend()
    started, release = threading.Event(), threading.Event()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))

        def denied(service: cu.Service) -> None:
            started.set()
            assert release.wait(5)
            raise cu.ClickUpError("permission", "Old dialog request was denied")

        try:
            settled(controller.refresh())
            future = controller.read(denied, current_scope=True)
            assert started.wait(5)
            settled(controller.refresh())
            release.set()
            with pytest.raises(cu.ClickUpError):
                future.result(timeout=5)
            view = controller.snapshot()
            assert view.state == "ready"
            assert view.listing is not None and view.listing.tasks[0].id == "a"
            assert view.error == ""
        finally:
            release.set()
            controller.close()


def test_other_workspace_permission_failure_preserves_selected_list() -> None:
    backend = Backend()
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            before = controller.snapshot()
            original_handle = backend.handle

            def denied(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/api/v2/team/20/space":
                    return httpx.Response(403, json={})
                return original_handle(request)

            backend.handle = denied  # type: ignore[method-assign]
            future = controller.read(
                lambda service: service.hierarchy("20"), current_scope=False
            )
            with pytest.raises(diagnostics.EngineError):
                future.result(timeout=5)
            assert controller.snapshot() == before
        finally:
            controller.close()


@pytest.mark.parametrize("action", ["cancel", "close", "switch"])
def test_hierarchy_cancellation_stops_following_http_requests(action: str) -> None:
    backend = Backend()
    started, release, cancel = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    with backend_environment(backend):
        controller = cu.Controller(cu.Selection("1", "10", "100", "CLICKUP_TEST_A"))
        try:
            settled(controller.refresh())
            settled(controller.select("a"))
            before = controller.snapshot()
            first_call = len(backend.calls)
            original_handle = backend.handle

            def blocked(request: httpx.Request) -> httpx.Response:
                response = original_handle(request)
                if request.url.path == "/api/v2/user":
                    started.set()
                    assert release.wait(5)
                return response

            backend.handle = blocked  # type: ignore[method-assign]
            future = controller.read(
                lambda service: service.hierarchy("20"),
                current_scope=False,
                cancel=cancel,
            )
            assert started.wait(5)
            switched = None
            if action == "cancel":
                cancel.set()
            elif action == "close":
                controller.close()
            else:
                switched = controller.switch(
                    cu.Selection("1", "20", "200", "CLICKUP_TEST_A")
                )
            release.set()
            with pytest.raises(concurrent.futures.CancelledError):
                future.result(timeout=5)
            if switched is not None:
                settled(switched)
                assert controller.snapshot().selection.list == "200"
                assert controller.snapshot().error == ""
            else:
                assert [path for path, _, _ in backend.calls[first_call:]] == ["/user"]
                assert controller.snapshot() == before
            if action == "cancel":
                # A completed worker must not leak its cancellation into later reads.
                assert controller.read(
                    lambda service: service.workspaces(), current_scope=False
                ).result(timeout=5) == (
                    cu.Workspace("10", "Engineering workspace"),
                    cu.Workspace("20", "Sales workspace"),
                )
        finally:
            release.set()
            controller.close()

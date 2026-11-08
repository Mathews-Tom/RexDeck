"""Shared fixtures: synthetic payloads, a routed transport, and the registry guard.

Zero live API calls. Every request in this suite is answered by an
`httpx.MockTransport` built from the JSON under `fixtures/`, so a test that
reaches ClickUp fails as an unrouted path rather than quietly succeeding
against the remote workspace.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from rexdeck.clickup.engine import client, registry, runtime
from rexdeck.clickup.engine.client import Client
from rexdeck.clickup.engine.records import ListRecord, Registry, Seeds, Space, Topology

FIXTURES = Path(__file__).parent / "fixtures"


def main(arguments: list[str], *, pin_write: bool = False) -> int:
    """Invoke against the synthetic selection, optionally staging an explicit pin."""
    from rexdeck.clickup.engine.commands import main as cli_main

    selected = list(arguments)
    if "--registry-path" not in selected:
        selected.extend(("--registry-path", str(registry.registry_path())))
    if pin_write:
        preview = [arg for arg in selected if arg not in ("--quiet", "--json")]
        output = StringIO()
        with redirect_stdout(output):
            result = cli_main([*preview, "--dry-run", "--json"])
        assert result == 0, output.getvalue()
        digest = json.loads(output.getvalue())["mutation"]["field_digest"]
        selected.extend(("--expect-digest", digest))
    return cli_main(selected)


def point_registry(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Redirect every default registry access to one test-owned path."""

    def location(*, account_id: str | None = None, team_id: str | None = None) -> Path:
        del account_id, team_id
        return path

    monkeypatch.setattr(registry, "registry_path", location)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use synthetic local storage and make accidental network requests fail."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("CLICKUP_API_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(client, "MIN_REQUEST_INTERVAL", 0.0)
    path = tmp_path / "config" / "rexdeck" / "clickup" / "registry.yaml"
    path.parent.mkdir(parents=True)
    point_registry(monkeypatch, path)
    path.write_text(unsynced_yaml(), encoding="utf-8")

    def deny_network(
        self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        del self
        raise AssertionError(
            f"live transport forbidden: {request.method} {request.url.path}"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)


def unsynced_yaml() -> str:
    """Render neutral seeds with a valid digest and no completed sync stamp."""
    seeds = seed_registry()
    body = registry.normalised(seeds)
    document = {
        "schema_version": body["schema_version"],
        "digest": registry.compute_digest(seeds),
        "synced_at": None,
        "workspace": body["workspace"],
        "seeds": body["seeds"],
        "discovered": body["discovered"],
    }
    return yaml.safe_dump(document, sort_keys=False, width=10_000)


def fixture(name: str) -> dict[str, Any]:
    """Return one synthetic response body."""
    loaded = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"fixture {name} is not a JSON object")
    return loaded


def api(routes: Mapping[str, object]) -> httpx.MockTransport:
    """Build a transport answering `routes`, keyed by path under the API root."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v2")
        if path not in routes:
            raise AssertionError(f"unrouted request: {request.method} {path}")
        answer = routes[path]
        if isinstance(answer, httpx.Response):
            return answer
        if callable(answer):
            produced = answer(request)
            if not isinstance(produced, httpx.Response):
                raise AssertionError(f"route {path} returned {type(produced).__name__}")
            return produced
        return httpx.Response(200, json=answer)

    return httpx.MockTransport(handle)


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Install a routed transport and hand the test its mutable routing table."""
    routes: dict[str, Any] = {}
    transport = api(routes)

    def build(token: str) -> Client:
        return Client(token, transport=transport)

    monkeypatch.setattr(runtime, "Client", build)
    yield routes


WALK_ROUTES: dict[str, str] = {
    "/space/700000000001": "space",
    "/space/700000000001/folder": "folders",
    "/space/700000000001/list": "space_lists",
    "/team": "team",
    "/list/70000009999/field": "list_fields",
    "/list/70000000101/field": "list_fields",
    "/list/70000000201/field": "list_fields",
}


def walk_routes() -> dict[str, Any]:
    """Return the routes one full `sync` walk needs."""
    return {path: fixture(name) for path, name in WALK_ROUTES.items()}


def seed_registry() -> Registry:
    """Return synthetic registry seeds with nothing discovered."""
    space = Space(id="700000000001", name="Sample Space")
    return Registry(
        schema_version=1,
        digest=None,
        synced_at=None,
        team_id="7000000001",
        seeds=Seeds(
            space=space,
            list=ListRecord(
                id="70000009999",
                name="Project Gamma",
                space_id=space.id,
                folder_id=None,
            ),
            me_id="710000001",
        ),
        topology=Topology(
            spaces=(), folders=(), lists=(), members=(), custom_fields={}
        ),
    )


@pytest.fixture
def synced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a synced registry to a temporary path and point the engine at it.

    Written through `rewrite`, never hand-rendered: a test that asserts on this
    file's content has to assert against an order a real write produces.
    """
    path = tmp_path / "sample_registry.yaml"
    point_registry(monkeypatch, path)
    registry.rewrite(_walked(), now="2026-09-13T09:00:00Z", path=path)
    return path


def _walked() -> Registry:
    """Return a registry carrying the topology the synthetic walk finds."""
    seeds = seed_registry()
    return Registry(
        schema_version=seeds.schema_version,
        digest=None,
        synced_at=None,
        team_id=seeds.team_id,
        seeds=seeds.seeds,
        topology=walked_topology(),
    )


def walked_topology() -> Topology:
    """Build the topology the synthetic fixtures describe, without a walk."""
    from rexdeck.clickup.engine.records import CustomField, FieldOption, Folder, Member

    space_id = "700000000001"
    channel = CustomField(
        id="10000000-0000-4000-8000-000000000001",
        name="Channel",
        type="drop_down",
        options=(
            FieldOption(
                id="20000000-0000-4000-8000-000000000001",
                name="Paid Search",
                orderindex=0,
            ),
            FieldOption(
                id="20000000-0000-4000-8000-000000000002",
                name="Paid Social",
                orderindex=1,
            ),
        ),
    )
    fields = (
        channel,
        CustomField(
            id="10000000-0000-4000-8000-000000000002",
            name="Budget",
            type="number",
            options=(),
        ),
        CustomField(
            id="10000000-0000-4000-8000-000000000003",
            name="Signed off",
            type="checkbox",
            options=(),
        ),
        CustomField(
            id="10000000-0000-4000-8000-000000000004",
            name="Shape",
            type="location",
            options=(),
        ),
    )
    lists = (
        ListRecord(
            id="70000009999",
            name="Project Gamma",
            space_id=space_id,
            folder_id="70000000001",
        ),
        ListRecord(
            id="70000000101",
            name="Project Beta",
            space_id=space_id,
            folder_id="70000000001",
        ),
        ListRecord(
            id="70000000201", name="Sample Inbox", space_id=space_id, folder_id=None
        ),
    )
    return Topology(
        spaces=(Space(id=space_id, name="Sample Space"),),
        folders=(Folder(id="70000000001", name="Sample Folder", space_id=space_id),),
        lists=lists,
        members=(
            Member(id="710000001", username="user_one"),
            Member(id="710000002", username="user_two"),
        ),
        custom_fields={record.id: fields for record in lists},
    )

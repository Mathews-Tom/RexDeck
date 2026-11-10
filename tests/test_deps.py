"""Public API fixtures, error mapping, large graphs and recoverable view states."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from rexdeck.catalogue.deps import INTEGRATION
from rexdeck.deps.api import (
    NO_ADVISORIES,
    Client,
    Coordinates,
    DepsError,
    Snapshot,
    parse_graph,
    parse_osv,
    validate_url,
)
from rexdeck.deps.app import DepsApp, report

PACKAGE = Coordinates("pypi", "requests", "2.19.0")
KEY = {"system": "PYPI", "name": "requests", "version": "2.19.0"}
VERSION = {
    "versionKey": KEY,
    "licenses": ["Apache-2.0"],
    "advisoryKeys": [{"id": "GHSA-9hjg-9r4m-mvj7"}],
    "links": [{"label": "Homepage", "url": "https://example.org"}],
}
GRAPH = {
    "nodes": [
        {"versionKey": KEY, "relation": "SELF", "errors": []},
        {
            "versionKey": {"system": "PYPI", "name": "urllib3", "version": "1.23.0"},
            "relation": "DIRECT",
            "errors": [],
        },
    ],
    "error": "",
}
OSV = {
    "vulns": [
        {
            "id": "GHSA-9hjg-9r4m-mvj7",
            "summary": "Credential leak",
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}],
            "database_specific": {"severity": "MODERATE"},
        }
    ]
}
SNAPSHOT = Snapshot(
    PACKAGE, ("Apache-2.0",), parse_graph(GRAPH)[0], (), "2026-11-10T00:00:00+00:00"
)


def fixture_client() -> tuple[Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.url.host == "api.osv.dev":
            payload = json.loads(request.content)
            token = payload.get("page_token")
            return httpx.Response(
                200, json={"next_page_token": "second"} if token is None else OSV
            )
        if path.endswith(":dependencies"):
            data: Any = GRAPH
        elif "/versions/" in path:
            data = VERSION
        else:
            data = {
                "versions": [
                    {"versionKey": {**KEY, "version": "9.0"}, "isDefault": False},
                    {"versionKey": KEY, "isDefault": True},
                ]
            }
        return httpx.Response(200, json=data)

    return Client(httpx.MockTransport(handle)), requests


def test_default_version_metadata_and_token_only_osv_page() -> None:
    client, calls = fixture_client()
    snap = client.fetch(Coordinates("pypi", "requests"))
    assert snap.package == PACKAGE
    assert snap.licenses == ("Apache-2.0",)
    assert snap.dependencies[0].package.name == "urllib3"
    assert snap.dependencies[0].relation == "direct"
    assert {a.source for a in snap.advisories} == {"deps.dev", "OSV"}
    assert snap.advisories[0].severity == "Not provided"
    assert "MODERATE" in snap.advisories[1].severity
    assert snap.fetched_at.endswith("+00:00")
    queries = [json.loads(c.content) for c in calls if c.method == "POST"]
    assert queries == [
        {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.19.0"},
        {
            "package": {"name": "requests", "ecosystem": "PyPI"},
            "version": "2.19.0",
            "page_token": "second",
        },
    ]
    assert all(
        c.method == "GET" or str(c.url) == "https://api.osv.dev/v1/query" for c in calls
    )


@pytest.mark.parametrize(
    "system,name,encoded",
    [
        ("npm", "@a/b", "%40a%2Fb"),
        ("go", "github.com/a/b", "github.com%2Fa%2Fb"),
        ("maven", "group:artifact", "group%3Aartifact"),
        ("pypi", "requests", "requests"),
        ("cargo", "serde", "serde"),
        ("nuget", "newtonsoft.json", "newtonsoft.json"),
        ("rubygems", "rack", "rack"),
    ],
)
def test_components_encoded(system: str, name: str, encoded: str) -> None:
    package = Coordinates(system, name, "1/2#3")
    assert package.path == f"/systems/{system}/packages/{encoded}/versions/1%2F2%233"
    assert package.url == f"https://deps.dev/{system}/{encoded}/1%2F2%233"
    validate_url(package.url, package)


@pytest.mark.parametrize(
    "status,state",
    [
        (404, "empty"),
        (429, "rate-limit"),
        (401, "permission"),
        (403, "permission"),
        (500, "network"),
        (503, "network"),
        (400, "error"),
        (302, "error"),
    ],
)
def test_http_error_mapping(status: int, state: str) -> None:
    client = Client(
        httpx.MockTransport(
            lambda request: httpx.Response(status, text="private-marker")
        )
    )
    with pytest.raises(DepsError) as caught:
        client.fetch(PACKAGE)
    assert caught.value.state == state
    assert "private-marker" not in str(caught.value)


def test_timeout_mapping() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private-marker", request=request)

    with pytest.raises(DepsError) as caught:
        Client(httpx.MockTransport(handle)).fetch(PACKAGE)
    assert caught.value.state == "network"
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "null",
        "not json",
        "{}",
        '{"versionKey": [], "licenses": []}',
        '{"versions": null}',
    ],
)
def test_malformed_is_recoverable(body: str) -> None:
    client = Client(httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    with pytest.raises(DepsError) as caught:
        client.fetch(PACKAGE)
    assert caught.value.state == "error"


def test_no_default_never_guesses_latest() -> None:
    client = Client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"versions": [{"versionKey": KEY}]}
            )
        )
    )
    with pytest.raises(DepsError, match="enter a version"):
        client.fetch(Coordinates("pypi", "requests"))


def test_repeated_osv_token_fails_loudly() -> None:
    client, _ = fixture_client()
    original = client.transport
    assert isinstance(original, httpx.MockTransport)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.osv.dev":
            return httpx.Response(200, json={"next_page_token": "same"})
        return original.handle_request(request)

    with pytest.raises(DepsError) as caught:
        Client(httpx.MockTransport(handle)).fetch(PACKAGE)
    assert caught.value.state == "error"


def test_large_graph_paging_and_incomplete_notice() -> None:
    nodes = list(GRAPH["nodes"])
    for number in range(120):
        nodes.append(
            {
                "versionKey": {**KEY, "name": f"indirect-{number}"},
                "relation": "INDIRECT",
                "errors": [],
            }
        )
    deps, notice = parse_graph({"nodes": nodes, "error": "resolver timed out"})
    snap = replace(SNAPSHOT, dependencies=deps, graph_notice=notice)
    assert len(deps) == 121
    assert "direct: urllib3" in report(snap, 1)
    assert "indirect-119" not in report(snap, 1)
    assert "indirect-119" in report(snap, 3)
    assert "Incomplete graph: resolver timed out" in report(snap, 1)
    assert "page 3/3" in report(snap, 3)


def test_all_displayed_api_strings_are_plain_sanitized() -> None:
    bad = "\x1b[31mred\x1b[0m\u202e\x00[link]"
    parsed = parse_osv(
        {
            "vulns": [
                {"id": bad, "summary": bad, "severity": [{"type": bad, "score": bad}]}
            ]
        }
    )
    snap = replace(SNAPSHOT, licenses=(bad,), advisories=parsed, graph_notice=bad)
    rendered = report(snap, 1)
    assert (
        "\x1b" not in rendered and "\u202e" not in rendered and "\x00" not in rendered
    )
    assert "red[link]" in rendered
    assert "Not provided" in report(replace(SNAPSHOT, licenses=()), 1)


@pytest.mark.parametrize(
    "url",
    [
        "http://deps.dev/pypi/requests/2.19.0",
        "https://deps.dev.evil.test/pypi/requests/2.19.0",
        "https://deps.dev@evil.test/pypi/requests/2.19.0",
        PACKAGE.url + "?x=1",
        PACKAGE.url + "#x",
        PACKAGE.url + "\n",
        "file:///tmp/a",
        "https://deps.dev/pypi/other/2.19.0",
        "https://[invalid",
    ],
)
def test_browser_url_validation(url: str) -> None:
    with pytest.raises(DepsError):
        validate_url(url, PACKAGE)


def test_browser_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    Client().open_package(PACKAGE)
    assert calls == [["/usr/bin/open", PACKAGE.url]]


def test_truthful_wording_and_owned_descriptor() -> None:
    assert "none known" in NO_ADVISORIES.lower()
    assert "clean" not in report(SNAPSHOT, 1).lower()
    assert "clean" not in Path("plugins/deps/README.md").read_text().lower()
    assert INTEGRATION.context == "directory"
    assert INTEGRATION.owned_module == "rexdeck.deps"
    assert INTEGRATION.args == ("-I", "-m", "rexdeck.deps")
    assert any("leave the machine" in c for c in INTEGRATION.capabilities)


def test_ui_stale_retry_new_selection_and_narrow_resize() -> None:
    class Flaky(Client):
        broken = False

        def fetch(
            self, selected: Coordinates, *, cancelled: Callable[[], bool] | None = None
        ) -> Snapshot:
            if self.broken:
                raise DepsError("network", "Offline; r retries.")
            return replace(SNAPSHOT, package=selected)

    async def scenario() -> None:
        client = Flaky()
        app = DepsApp(client)
        async with app.run_test(size=(40, 25)) as pilot:
            app.query_one("#name", Input).value = "requests"
            app.query_one("#version", Input).value = "2.19.0"
            app.apply()
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            assert app.snapshot is not None
            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            wide_height = app.query_one("#content", Static).size.height
            await pilot.resize_terminal(40, 25)
            await pilot.pause()
            body = app.query_one("#report", VerticalScroll)
            assert body.size.width <= 40
            assert app.query_one("#content", Static).size.height > wide_height
            fetched = app.snapshot.fetched_at
            client.broken = True
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "stale"
            assert app.snapshot.fetched_at == fetched
            client.broken = False
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            client.broken = True
            app.query_one("#name", Input).value = "other"
            app.apply()
            await app.workers.wait_for_complete()
            assert app.state == "network" and app.snapshot is None
            await pilot.resize_terminal(100, 30)
            assert app.size.width == 100

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state", ["empty", "rate-limit", "permission", "network", "error"]
)
def test_ui_initial_failure_can_refresh_without_restart(state: str) -> None:
    class Recovering(Client):
        failed = True

        def fetch(
            self, selected: Coordinates, *, cancelled: Callable[[], bool] | None = None
        ) -> Snapshot:
            if self.failed:
                raise DepsError(state, "Fixture failure; r retries.")
            return SNAPSHOT

    async def scenario() -> None:
        client = Recovering()
        app = DepsApp(client)
        async with app.run_test() as pilot:
            app.query_one("#name", Input).value = "requests"
            app.apply()
            assert app.state == "loading"
            await app.workers.wait_for_complete()
            assert app.state == state
            client.failed = False
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"

    asyncio.run(scenario())


@pytest.mark.parametrize("system", ["go", "nuget", "rubygems", "pypi"])
def test_unavailable_graph_keeps_licenses_and_advisories(system: str) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith(":dependencies"):
            return httpx.Response(404)
        if request.url.host == "api.osv.dev":
            return httpx.Response(200, json=OSV)
        return httpx.Response(
            200, json={**VERSION, "versionKey": {**KEY, "system": system.upper()}}
        )

    snap = Client(httpx.MockTransport(handle)).fetch(
        Coordinates(system, "requests", "2.19.0")
    )
    rendered = report(snap, 1)
    assert "Apache-2.0" in rendered
    assert "Credential leak" in rendered
    assert "GHSA-9hjg-9r4m-mvj7" in rendered
    assert f"Dependency graph not provided by deps.dev for {system}" in rendered
    assert "No dependencies reported" not in rendered
    graphs = [c for c in calls if c.url.path.endswith(":dependencies")]
    assert len(graphs) == (1 if system == "pypi" else 0)


def test_many_advisory_keys_do_not_issue_per_key_requests() -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "/advisories/" not in request.url.path
        if request.url.host == "api.osv.dev":
            return httpx.Response(200, json={})
        if request.url.path.endswith(":dependencies"):
            return httpx.Response(200, json=GRAPH)
        return httpx.Response(
            200,
            json={**VERSION, "advisoryKeys": [{"id": f"GHSA-{n}"} for n in range(745)]},
        )

    snap = Client(httpx.MockTransport(handle)).fetch(PACKAGE)
    assert len(calls) == 3
    assert len(snap.advisories) == 745
    assert snap.advisories[-1].id == "GHSA-744"
    assert all(a.severity == "Not provided" for a in snap.advisories)
    assert "CVSS3 0" not in report(snap, 1)


@pytest.mark.parametrize(
    "unscored",
    [
        {"id": "GHSA-unscored"},
        {"id": "GHSA-unscored", "severity": [], "database_specific": {}},
    ],
)
def test_unscored_osv_advisory_is_not_numeric_zero(unscored: dict[str, Any]) -> None:
    advisories = parse_osv({"vulns": [unscored]})
    assert advisories[0].severity == "Not provided"
    assert "CVSS3 0" not in report(replace(SNAPSHOT, advisories=advisories), 1)


@pytest.mark.parametrize("stop_after", ["version", "graph", "osv-page"])
def test_superseded_fetch_stops_before_next_request(stop_after: str) -> None:
    superseded = False
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal superseded
        calls.append(request)
        if request.url.host == "api.osv.dev":
            superseded = stop_after == "osv-page"
            return httpx.Response(200, json={"next_page_token": "another"})
        if request.url.path.endswith(":dependencies"):
            superseded = stop_after == "graph"
            return httpx.Response(200, json=GRAPH)
        superseded = stop_after == "version"
        return httpx.Response(200, json=VERSION)

    with pytest.raises(DepsError) as caught:
        Client(httpx.MockTransport(handle)).fetch(PACKAGE, cancelled=lambda: superseded)
    assert caught.value.state == "cancelled"
    assert len(calls) == {"version": 1, "graph": 2, "osv-page": 3}[stop_after]


def test_ui_superseding_fetch_cancels_old_request_sequence() -> None:
    started = Event()
    release = Event()
    returned = Event()
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
            returned.set()
            return httpx.Response(200, json=VERSION)
        if request.url.host == "api.osv.dev":
            return httpx.Response(200, json={})
        if request.url.path.endswith(":dependencies"):
            return httpx.Response(200, json=GRAPH)
        return httpx.Response(200, json=VERSION)

    async def scenario() -> None:
        app = DepsApp(Client(httpx.MockTransport(handle)))
        async with app.run_test() as pilot:
            app.query_one("#name", Input).value = "requests"
            app.query_one("#version", Input).value = "2.19.0"
            app.apply()
            assert await asyncio.to_thread(started.wait, 2)
            try:
                app.action_refresh()
                await asyncio.gather(
                    *(worker.wait() for worker in app.workers), return_exceptions=True
                )
                assert app.state == "ready"
            finally:
                release.set()
            assert await asyncio.to_thread(returned.wait, 2)
            await pilot.pause()
            assert len(calls) == 4
            assert sum(c.url.path.endswith(":dependencies") for c in calls) == 1

    asyncio.run(scenario())

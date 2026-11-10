"""OCI fixtures, anonymous auth, safe pagination and recoverable UI states."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import replace
from typing import Any

import httpx
import pytest
from textual.widgets import DataTable, Input, Static

from rexdeck.catalogue.images import INTEGRATION
from rexdeck.images.api import (
    ACCEPT,
    DOCKER_INDEX,
    DOCKER_MANIFEST,
    OCI_INDEX,
    OCI_MANIFEST,
    PAGE_SIZE,
    Client,
    ImagesError,
    Manifest,
    Reference,
    TagPage,
    challenge,
    parse_manifest,
    validate_url,
)
from rexdeck.images.app import ImagesApp, report

REF = Reference.parse("nginx")
CHALLENGE = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/nginx:pull"'
INDEX: dict[str, Any] = {
    "schemaVersion": 2,
    "mediaType": OCI_INDEX,
    "manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}},
        {"platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
        {},
    ],
}
SINGLE = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": {}, "layers": []}
PAGE = TagPage(("latest", "stable"), "stable", "2026-11-10T00:00:00Z")
MANIFEST = Manifest(
    "latest",
    "sha256:" + "a" * 64,
    OCI_INDEX,
    ("linux/amd64", "linux/arm64/v8"),
    "2026-11-10T00:00:01Z",
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("nginx", "docker.io/library/nginx"),
        ("library/nginx", "docker.io/library/nginx"),
        ("docker.io/nginx", "docker.io/library/nginx"),
        ("registry-1.docker.io/library/nginx", "docker.io/library/nginx"),
        ("index.docker.io/team/image", "docker.io/team/image"),
        ("ghcr.io/owner/x", "ghcr.io/owner/x"),
        ("quay.io/org/repo", "quay.io/org/repo"),
    ],
)
def test_normalization(value: str, expected: str) -> None:
    assert Reference.parse(value).normalized == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://ghcr.io/x/y",
        "nginx:latest",
        "a@sha256:abc",
        "../foo",
        "ghcr.io/X/y",
        "localhost/x",
        "user:secret@ghcr.io/x",
        "ghcr.io/a//b",
        "ghcr.io/a/%2e",
        "x\x1b/y",
    ],
)
def test_bad_reference(value: str) -> None:
    with pytest.raises(ValueError):
        Reference.parse(value)


def test_bearer_challenge() -> None:
    realm, params = challenge(CHALLENGE, REF)
    assert realm == "https://auth.docker.io/token"
    assert params == {
        "service": "registry.docker.io",
        "scope": "repository:library/nginx:pull",
    }
    ghcr = Reference.parse("ghcr.io/owner/x")
    assert (
        challenge('bearer realm="https://ghcr.io/token",service="ghcr.io"', ghcr)[1][
            "scope"
        ]
        == "repository:owner/x:pull"
    )
    assert (
        challenge(
            'Bearer realm="https://auth.docker.io/token",service="with,comma"', REF
        )[1]["service"]
        == "with,comma"
    )


@pytest.mark.parametrize(
    "header",
    [
        'Bearer realm="http://auth.docker.io/token"',
        'Bearer realm="https://evil.test/token"',
        'Bearer realm="https://auth.docker.io@evil.test/token"',
        'Bearer realm="https://auth.docker.io/token?scope=push"',
        'Bearer realm="https://auth.docker.io/token",scope="repository:library/nginx:push"',
        'Bearer realm="https://auth.docker.io/token",realm="https://evil.test"',
    ],
)
def test_untrusted_challenge(header: str) -> None:
    with pytest.raises(ValueError):
        challenge(header, REF)


@pytest.mark.parametrize(
    "realm",
    [
        "https://auth.docker.io/to\tken",
        "https://auth.docker.io/to\rken",
        "https://auth.docker.io/to\nken",
        "https://auth.docker.io/token?",
    ],
)
def test_realm_must_be_printable_and_round_trip(realm: str) -> None:
    with pytest.raises(ValueError, match="Untrusted anonymous token realm"):
        challenge(f'Bearer realm="{realm}"', REF)
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(
            401, headers={"WWW-Authenticate": f'Bearer realm="{realm}"'}
        )

    client = Client(httpx.MockTransport(handle))
    client._tokens[REF] = "cached-secret-token"
    with pytest.raises(ImagesError) as caught:
        client.tags(REF)
    assert caught.value.state == "error"
    assert "cached-secret-token" not in str(caught.value)
    assert calls == ["registry-1.docker.io"]


def test_docker_token_realm_is_not_trusted_for_ghcr() -> None:
    with pytest.raises(ValueError, match="Untrusted anonymous token realm"):
        challenge(
            'Bearer realm="https://auth.docker.io/token"',
            Reference.parse("ghcr.io/owner/x"),
        )


def test_basic_auth_is_unsupported() -> None:
    with pytest.raises(
        ImagesError, match="private registries are unsupported"
    ) as caught:
        challenge('Basic realm="registry"', REF)
    assert caught.value.state == "permission"


def test_token_pagination_and_manifest_never_request_blobs() -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "/blobs/" not in request.url.path
        assert request.method == "GET"
        if request.url.host == "auth.docker.io":
            assert "authorization" not in request.headers
            assert request.url.params["scope"] == "repository:library/nginx:pull"
            assert request.url.params["service"] == "registry.docker.io"
            return httpx.Response(200, json={"token": "secret-token"})
        assert request.headers["accept"] == ACCEPT
        if request.headers.get("authorization") != "Bearer secret-token":
            return httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE})
        if "/manifests/" in request.url.path:
            return httpx.Response(
                200, json=INDEX, headers={"Docker-Content-Digest": MANIFEST.digest}
            )
        assert request.url.params["n"] == str(PAGE_SIZE)
        if request.url.params.get("last"):
            assert request.url.params["last"] == "stable"
            return httpx.Response(200, json={"tags": ["v1"]})
        return httpx.Response(
            200,
            json={"tags": ["latest", "stable"]},
            headers={
                "Link": '</v2/library/nginx/tags/list?n=30&last=stable>; rel="next"'
            },
        )

    client = Client(httpx.MockTransport(handle))
    first = client.tags(REF)
    assert first.tags == ("latest", "stable") and first.next_cursor == "stable"
    second = client.tags(REF, first.next_cursor)
    assert second.tags == ("v1",) and second.next_cursor is None
    manifest = client.manifest(REF, "latest")
    assert manifest.platforms == ("linux/amd64", "linux/arm64/v8")
    assert manifest.digest == MANIFEST.digest
    assert len([c for c in calls if c.url.host == "auth.docker.io"]) == 1
    assert len(calls) == 5


def test_access_token_and_expiry_rechallenge() -> None:
    token_calls = 0
    request_calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, request_calls
        if request.url.host == "auth.docker.io":
            token_calls += 1
            return httpx.Response(200, json={"access_token": f"token-{token_calls}"})
        request_calls += 1
        if request_calls in (1, 3):
            return httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE})
        return httpx.Response(200, json={"tags": None})

    client = Client(httpx.MockTransport(handle))
    assert client.tags(REF).tags == ()
    assert client.tags(REF).tags == ()
    assert token_calls == 2


@pytest.mark.parametrize(
    "link",
    [
        '<https://evil.test/v2/library/nginx/tags/list?last=latest>; rel="next"',
        '</v2/library/nginx/blobs/x?last=latest>; rel="next"',
        '</v2/library/nginx/tags/list?last=other>; rel="next"',
        '</v2/library/nginx/tags/list?last=latest&last=other>; rel="next"',
        '</v2/library/nginx/tags/list?last=latest&token=secret>; rel="next"',
    ],
)
def test_pagination_cannot_redirect_requests(link: str) -> None:
    client = Client(
        httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"tags": ["latest"]}, headers={"Link": link}
            )
        )
    )
    with pytest.raises(ImagesError, match="pagination"):
        client.tags(REF)


def test_full_page_without_link_uses_last() -> None:
    tags = [f"v{i}" for i in range(PAGE_SIZE)]
    client = Client(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"tags": tags}))
    )
    assert client.tags(REF).next_cursor == tags[-1]
    with pytest.raises(ImagesError):
        client.tags(REF, tags[-1])


@pytest.mark.parametrize(
    "kind", [OCI_INDEX, DOCKER_INDEX, OCI_MANIFEST, DOCKER_MANIFEST]
)
def test_manifest_type_and_digest(kind: str) -> None:
    data = dict(INDEX if kind in (OCI_INDEX, DOCKER_INDEX) else SINGLE)
    data["mediaType"] = kind
    body = json.dumps(data).encode()
    manifest = parse_manifest(httpx.Response(200, content=body), "latest")
    assert manifest.digest == "sha256:" + hashlib.sha256(body).hexdigest()
    assert manifest.media_type == kind
    assert bool(manifest.platforms) == (kind in (OCI_INDEX, DOCKER_INDEX))
    data.pop("mediaType")
    assert (
        parse_manifest(
            httpx.Response(
                200, json=data, headers={"Content-Type": kind + "; charset=utf-8"}
            ),
            "v1",
        ).media_type
        == kind
    )


@pytest.mark.parametrize(
    "code,state",
    [
        (401, "permission"),
        (403, "permission"),
        (404, "empty"),
        (429, "rate-limit"),
        (500, "network"),
        (302, "error"),
    ],
)
def test_http_mapping_hides_payload(code: int, state: str) -> None:
    client = Client(
        httpx.MockTransport(
            lambda r: httpx.Response(code, text="secret-private-payload")
        )
    )
    with pytest.raises(ImagesError) as caught:
        client.tags(REF)
    assert caught.value.state == state
    assert "secret" not in str(caught.value)


def test_redirect_with_location_is_not_followed() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "evil.test":
            return httpx.Response(200, json={"tags": ["stolen"]})
        return httpx.Response(
            302, headers={"Location": "https://evil.test/v2/library/nginx/tags/list"}
        )

    with pytest.raises(ImagesError) as caught:
        Client(httpx.MockTransport(handle)).tags(REF)
    assert caught.value.state == "error"
    assert calls == ["registry-1.docker.io"]


def test_invalid_url_maps_to_error_without_token_diagnostics() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("cached-secret-token")

    client = Client(httpx.MockTransport(handle))
    client._tokens[REF] = "cached-secret-token"
    with pytest.raises(ImagesError) as caught:
        client.tags(REF)
    assert caught.value.state == "error"
    assert "cached-secret-token" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_network_mapping() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-url", request=request)

    with pytest.raises(ImagesError) as caught:
        Client(httpx.MockTransport(handle)).tags(REF)
    assert caught.value.state == "network" and "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [{}, {"tags": ["escape\x1b"]}, {"tags": "latest"}, {"tags": ["latest", "latest"]}],
)
def test_malformed_tags(payload: dict[str, Any]) -> None:
    with pytest.raises(ImagesError):
        Client(httpx.MockTransport(lambda r: httpx.Response(200, json=payload))).tags(
            REF
        )


@pytest.mark.parametrize(
    "payload",
    [{}, {"schemaVersion": 2, "mediaType": "unknown"}, {**INDEX, "manifests": [None]}],
)
def test_malformed_manifest(payload: dict[str, Any]) -> None:
    with pytest.raises(ImagesError):
        Client(
            httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ).manifest(REF, "latest")


def test_plain_sanitized_display() -> None:
    result = parse_manifest(
        httpx.Response(
            200,
            json={
                **INDEX,
                "manifests": [
                    {
                        "platform": {
                            "os": "linux\x1b[31m\u202e",
                            "architecture": "[link]",
                        }
                    }
                ],
            },
        ),
        "latest",
    )
    assert result.platforms == ("linux/[link]",)
    assert "\x1b" not in report(REF, PAGE, result)
    assert "config blobs are not fetched" in report(
        REF, PAGE, replace(MANIFEST, platforms=())
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://hub.docker.com/_/nginx",
        "https://hub.docker.com/_/nginx?x",
        "https://hub.docker.com@evil.test/_/nginx",
        "https://hub.docker.com/_/other",
        "https://hub.docker.com/_/nginx#fragment",
    ],
)
def test_url_validation(url: str) -> None:
    with pytest.raises(ImagesError):
        validate_url(url, REF)


def test_browser_argv_and_ghcr_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    Client().open_reference(REF)
    assert calls == [["/usr/bin/open", "https://hub.docker.com/_/nginx"]]
    assert (
        Reference.parse("team/image").web_url
        == "https://hub.docker.com/r/team/image/tags"
    )
    with pytest.raises(ImagesError, match="refusing to guess"):
        Client().open_reference(Reference.parse("ghcr.io/astral-sh/uv"))
    assert len(calls) == 1


def test_descriptor_disclosures() -> None:
    assert INTEGRATION.context == "directory"
    assert INTEGRATION.args == ("-I", "-m", "rexdeck.images")
    assert INTEGRATION.owned_module == "rexdeck.images"
    assert any("leave the machine" in v for v in INTEGRATION.capabilities)
    assert any(
        "not a vulnerability or signature verdict" in v
        for v in INTEGRATION.capabilities
    )


def test_ui_refresh_paging_selection_stale_and_narrow() -> None:
    class Flaky(Client):
        fail = False

        def tags(self, reference: Reference, cursor: str = "") -> TagPage:
            if self.fail:
                raise ImagesError("network", "Simulated offline; r retries.")
            return replace(PAGE, tags=("v1",), next_cursor=None) if cursor else PAGE

        def manifest(self, reference: Reference, tag: str) -> Manifest:
            if self.fail:
                raise ImagesError("network", "Simulated offline; r retries.")
            return replace(MANIFEST, tag=tag)

    async def scenario() -> None:
        client = Flaky()
        app = ImagesApp(client)
        async with app.run_test(size=(40, 35)) as pilot:
            app.query_one(Input).value = "nginx"
            app.apply()
            await app.workers.wait_for_complete()
            assert app.state == "ready" and app.reference == REF
            table = app.query_one(DataTable)
            assert table.row_count == 2
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert app.manifest and app.manifest.platforms == MANIFEST.platforms
            assert "sha256:" in str(app.query_one("#content", Static).render())
            client.fail = True
            app.action_refresh()
            await app.workers.wait_for_complete()
            assert app.state == "stale" and app.manifest
            client.fail = False
            app.action_refresh()
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            app.action_next()
            await app.workers.wait_for_complete()
            assert (
                app.page_number == 1 and table.row_count == 1 and app.manifest is None
            )
            app.action_previous()
            await app.workers.wait_for_complete()
            assert app.page_number == 0 and table.row_count == 2
            app.query_one(Input).value = "ghcr.io/owner/x"
            app.apply()
            await app.workers.wait_for_complete()
            assert app.manifest is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state", ["network", "permission", "rate-limit", "empty", "error"]
)
def test_initial_failure_recovers(state: str) -> None:
    class Recovering(Client):
        fail = True

        def tags(self, reference: Reference, cursor: str = "") -> TagPage:
            if self.fail:
                raise ImagesError(state, "Simulated refusal; r retries.")
            return PAGE

    async def scenario() -> None:
        client = Recovering()
        app = ImagesApp(client)
        async with app.run_test(size=(40, 35)):
            app.query_one(Input).value = "nginx"
            app.apply()
            await app.workers.wait_for_complete()
            assert app.state == state
            client.fail = False
            app.action_refresh()
            await app.workers.wait_for_complete()
            assert app.state == "ready" and app.page == PAGE

    asyncio.run(scenario())

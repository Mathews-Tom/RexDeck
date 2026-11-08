"""Mock-only origin, redaction, credentials, and process-wide pacing contracts."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from rexdeck.clickup.engine import client, credentials
from rexdeck.clickup.engine.diagnostics import EngineError

TOKEN = "synthetic-test-token"


def test_successful_response_redacts_echoed_token_in_nested_keys_and_values() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == TOKEN
        return httpx.Response(
            200,
            json={
                "name": f"prefix {TOKEN} suffix",
                "nested": [{TOKEN: f"echo {TOKEN}"}],
            },
        )

    connection = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        payload = connection.get("/task/synaa0001")
        assert connection.calls == 1
        assert TOKEN not in json.dumps(payload)
        assert "[REDACTED]" in json.dumps(payload)
        assert not hasattr(connection, "fetched")
    finally:
        connection.close()


@pytest.mark.parametrize("content", (b"not json", b"[]", b"null", b"\xff"))
def test_invalid_response_payload_is_a_coded_finding_without_body_contents(
    content: bytes,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=content)

    connection = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(EngineError) as caught:
            connection.get("/task/synaa0001")
        assert caught.value.diagnostic.code == "transport/bad-response"
        assert connection.calls == 1
        assert TOKEN not in str(caught.value.diagnostic)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("body", "ecode"),
    (
        ({"err": f"echo {TOKEN}", "ECODE": "ITEM_223"}, "ITEM_223"),
        ({"err": "x", "ECODE": f"BAD {TOKEN}"}, None),
        ({"err": "x"}, None),
        ([], None),
    ),
)
def test_http_400_is_a_refused_request_keeping_only_a_well_formed_ecode(
    body: object, ecode: str | None
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json=body)

    connection = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(EngineError) as caught:
            connection.put("/task/synaa0001", {"points": 2.5})
        finding = caught.value.diagnostic
        assert finding.code == "request/rejected"
        assert finding.evidence["ecode"] == ecode
        assert TOKEN not in str(finding) and "echo" not in str(finding)
        assert connection.calls == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "path",
    (
        "https://example.invalid/task/synaa0001",
        "//example.invalid/task/synaa0001",
        "/../task/synaa0001",
        "/task/%2e%2e/synaa0001",
        "/task/synaa0001?token=value",
        "/task/synaa0001#fragment",
        "/task\\synaa0001",
        "/task/with space",
    ),
)
def test_nonlocal_or_ambiguous_paths_never_reach_the_transport(path: str) -> None:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    connection = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(EngineError) as caught:
            connection.get(path)
        assert caught.value.diagnostic.code == "usage/bad-path"
        assert sent == []
        assert connection.calls == 0
    finally:
        connection.close()


def test_redirects_do_not_forward_authorization_or_issue_a_second_request() -> None:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            302, headers={"Location": "https://example.invalid/private"}
        )

    connection = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(EngineError) as caught:
            connection.get("/task/synaa0001")
        assert caught.value.diagnostic.code == "transport/bad-response"
        assert len(sent) == 1
        assert sent[0].url.host == "api.clickup.com"
    finally:
        connection.close()


def test_pacing_is_shared_across_clients_without_real_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    sleeps: list[float] = []
    starts: list[float] = []

    def now() -> float:
        return clock[0]

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        starts.append(clock[0])
        return httpx.Response(200, json={})

    monkeypatch.setattr(client, "MIN_REQUEST_INTERVAL", 1.0)
    monkeypatch.setattr(client, "_last_request_start", None)
    monkeypatch.setattr(time, "monotonic", now)
    monkeypatch.setattr(time, "sleep", sleep)
    first = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    second = client.Client(TOKEN, transport=httpx.MockTransport(handle))
    try:
        first.get("/task/synaa0001")
        second.get("/task/synaa0002")
    finally:
        first.close()
        second.close()
    assert starts == [100.0, 101.0]
    assert sleeps == [1.0]


def test_credentials_are_read_only_from_the_inherited_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLICKUP_API_TOKEN", f"  {TOKEN}  ")
    assert credentials.read_token() == TOKEN
    monkeypatch.setenv("CLICKUP_API_TOKEN", "   ")
    with pytest.raises(EngineError) as caught:
        credentials.read_token()
    assert caught.value.diagnostic.code == "auth/missing-credential"
    assert TOKEN not in str(caught.value.diagnostic)


@pytest.mark.parametrize(
    "name",
    (
        "",
        "HOME",
        "PATH",
        "CLICKUP_",
        "CLICKUP_token",
        "CLICKUP_TOKEN\n",
        "CLICKUP_A-B",
        "CLICKUP_ＴＯＫＥＮ",
    ),
)
def test_credential_environment_names_are_namespace_restricted(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name or "HOME", TOKEN)
    with pytest.raises(ValueError):
        credentials.validate_env_name(name)
    with pytest.raises(ValueError):
        credentials.read_token(name)


@pytest.mark.parametrize("name", ("CLICKUP_API_TOKEN", "CLICKUP_ACCOUNT_2"))
def test_valid_credential_environment_names_are_accepted(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, TOKEN)
    credentials.validate_env_name(name)
    assert credentials.read_token(name) == TOKEN


def test_write_guard_is_rechecked_after_pacing_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    approved = [True]
    sent: list[httpx.Request] = []
    checked: list[tuple[str, str]] = []

    class GuardedClient(client.Client):
        def _before_send(self, method: str, path: str) -> None:
            checked.append((method, path))
            if method != "GET" and not approved[0]:
                raise PermissionError("approval revoked while pacing")

    def sleep(delay: float) -> None:
        clock[0] += delay
        approved[0] = False

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(client, "MIN_REQUEST_INTERVAL", 1.0)
    monkeypatch.setattr(client, "_last_request_start", 100.0)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", sleep)
    connection = GuardedClient(TOKEN, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(PermissionError, match="approval revoked"):
            connection.put("/task/synaa0001", {"status": "in review"})
        assert checked[-1] == ("PUT", "/task/synaa0001")
        assert clock[0] == 101.0
        assert sent == []
        assert connection.calls == 0
        connection.get("/task/synaa0001")
        assert [request.method for request in sent] == ["GET"]
    finally:
        connection.close()

"""Every write endpoint stages, pins, sends once, and fails without leaking bodies."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from rexdeck.clickup.engine import registry
from rexdeck.clickup.engine.records import CustomField, FieldOption

from .conftest import fixture, main

TASK_ID = "synaa0001"
TASK = f"/task/{TASK_ID}"
COMMENTS = f"{TASK}/comment"
FIELD_ID = "10000000-0000-4000-8000-000000000001"
FIELD = f"{TASK}/field/{FIELD_ID}"
TOKEN = "synthetic-test-token"
BODY_MARKER = "private-response-body-must-not-be-emitted"


@dataclass(frozen=True)
class WriteCase:
    name: str
    arguments: tuple[str, ...]
    method: str
    path: str
    body: dict[str, object]


CASES = (
    WriteCase(
        "status",
        ("touch", TASK_ID, "--status", "in review"),
        "PUT",
        TASK,
        {"status": "in review"},
    ),
    WriteCase(
        "assignee",
        ("touch", TASK_ID, "--assignee", "user_two"),
        "PUT",
        TASK,
        {"assignees": {"add": [710000002], "rem": [710000001]}},
    ),
    WriteCase(
        "due",
        ("touch", TASK_ID, "--due", "2026-10-01"),
        "PUT",
        TASK,
        {"due_date": int(datetime(2026, 10, 1, 12).timestamp() * 1000)},
    ),
    WriteCase(
        "description",
        ("touch", TASK_ID, "--description", "Synthetic **Markdown** body"),
        "PUT",
        TASK,
        {"markdown_content": "Synthetic **Markdown** body"},
    ),
    WriteCase(
        "points",
        ("touch", TASK_ID, "--points", "3"),
        "PUT",
        TASK,
        {"points": 3},
    ),
    WriteCase(
        "custom-field",
        ("touch", TASK_ID, "--field", "Channel=Paid Social"),
        "POST",
        FIELD,
        {"value": "20000000-0000-4000-8000-000000000002"},
    ),
    WriteCase(
        "comment",
        ("comment", TASK_ID, "--text", "Synthetic acknowledgement"),
        "POST",
        COMMENTS,
        {"comment_text": "Synthetic acknowledgement", "notify_all": False},
    ),
)


def install_routes(
    route: dict[str, Any], sent: list[httpx.Request], *, failure: str | None = None
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            payload = (
                fixture("task")
                if request.url.path.endswith(TASK)
                else fixture("task_comments")
            )
            return httpx.Response(200, json=payload)
        sent.append(request)
        if failure == "connect":
            raise httpx.ConnectError(f"{TOKEN}: {BODY_MARKER}", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(f"{TOKEN}: {BODY_MARKER}", request=request)
        if failure is not None:
            return httpx.Response(
                int(failure),
                headers={"Retry-After": "30"},
                json={"err": f"{TOKEN}: {BODY_MARKER}"},
            )
        return httpx.Response(200, json=fixture("comment_created"))

    route[TASK] = answer
    route[COMMENTS] = answer
    route[FIELD] = answer


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_each_write_dry_run_then_pinned_apply_sends_exactly_the_staged_body(
    case: WriteCase,
    route: dict[str, Any],
    synced: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del synced
    sent: list[httpx.Request] = []
    install_routes(route, sent)

    assert main([*case.arguments, "--dry-run", "--json"]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert sent == []
    assert staged["mutation"]["diff"]
    digest = staged["mutation"]["field_digest"]
    assert len(digest) == 64

    assert main([*case.arguments, "--expect-digest", digest, "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert len(sent) == 1
    assert sent[0].method == case.method
    assert sent[0].url.path == f"/api/v2{case.path}"
    assert json.loads(sent[0].content) == case.body
    assert applied["mutation"] == staged["mutation"]
    assert all(
        finding["code"] != "state/unpinned" for finding in applied["diagnostics"]
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_each_write_refuses_a_wrong_digest_before_mutating(
    case: WriteCase,
    route: dict[str, Any],
    synced: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del synced
    sent: list[httpx.Request] = []
    install_routes(route, sent)

    assert main([*case.arguments, "--expect-digest", "0" * 64, "--json"]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert sent == []
    assert receipt["diagnostics"][0]["code"] == "state/changed-under-us"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("failure", ("429", "500", "connect", "timeout"))
def test_each_write_failure_is_not_retried_and_does_not_emit_secret_bodies(
    case: WriteCase,
    failure: str,
    route: dict[str, Any],
    synced: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del synced
    sent: list[httpx.Request] = []
    install_routes(route, sent, failure=failure)

    assert main([*case.arguments, "--json"], pin_write=True) == 1
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert len(sent) == 1
    assert sent[0].method == case.method
    assert sent[0].url.path == f"/api/v2{case.path}"
    assert TOKEN not in captured.out + captured.err
    assert BODY_MARKER not in captured.out + captured.err
    codes = {finding["code"] for finding in receipt["diagnostics"]}
    expected = (
        "transport/unreachable"
        if failure in {"connect", "timeout"}
        else "transport/rate-limited"
        if failure == "429"
        else "transport/bad-response"
    )
    assert expected in codes
    assert "mutation" not in receipt


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    (
        ("checkbox", "yes", True),
        ("checkbox", "no", False),
        ("currency", "14.50", 14.5),
        ("number", "12", 12),
        ("date", "2026-10-01", int(datetime(2026, 10, 1, 12).timestamp() * 1000)),
        ("drop_down", "Sample option", "20000000-0000-4000-8000-000000000010"),
        ("email", "sample@example.invalid", "sample@example.invalid"),
        ("phone", "+1 202 555 0100", "+1 202 555 0100"),
        ("short_text", "Synthetic short text", "Synthetic short text"),
        ("text", "Synthetic long text", "Synthetic long text"),
        ("url", "https://example.invalid/item", "https://example.invalid/item"),
    ),
)
def test_every_writable_custom_field_type_uses_the_field_endpoint(
    kind: str,
    raw: str,
    expected: object,
    route: dict[str, Any],
    synced: Path,
) -> None:
    loaded = registry.load(synced)
    field = CustomField(
        id=FIELD_ID,
        name="Sample attribute",
        type=kind,
        options=(
            FieldOption(
                id="20000000-0000-4000-8000-000000000010",
                name="Sample option",
                orderindex=0,
            ),
        )
        if kind == "drop_down"
        else (),
    )
    topology = replace(loaded.topology, custom_fields={loaded.seeds.list.id: (field,)})
    registry.rewrite(
        replace(loaded, topology=topology), now="2026-11-01T00:00:00Z", path=synced
    )
    sent: list[httpx.Request] = []
    install_routes(route, sent)

    assert (
        main(
            ["touch", TASK_ID, "--field", f"Sample attribute={raw}", "--quiet"],
            pin_write=True,
        )
        == 0
    )
    assert len(sent) == 1
    assert sent[0].method == "POST"
    assert sent[0].url.path == f"/api/v2{FIELD}"
    assert json.loads(sent[0].content) == {"value": expected}


def test_combined_touch_writes_core_fields_then_each_custom_field_once(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    install_routes(route, sent)
    extra_field = f"{TASK}/field/10000000-0000-4000-8000-000000000003"
    route[extra_field] = route[FIELD]
    argv = [
        "touch",
        TASK_ID,
        "--status",
        "in review",
        "--assignee",
        "user_two",
        "--due",
        "2026-10-01",
        "--field",
        "Channel=Paid Social",
        "--field",
        "Signed off=no",
    ]
    assert main([*argv, "--dry-run", "--json"]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert sent == []
    assert (
        main([*argv, "--expect-digest", staged["mutation"]["field_digest"], "--json"])
        == 0
    )
    assert [(request.method, request.url.path) for request in sent] == [
        ("PUT", f"/api/v2{TASK}"),
        ("POST", f"/api/v2{FIELD}"),
        ("POST", f"/api/v2{extra_field}"),
    ]
    assert json.loads(sent[0].content) == {
        "status": "in review",
        "assignees": {"add": [710000002], "rem": [710000001]},
        "due_date": int(datetime(2026, 10, 1, 12).timestamp() * 1000),
    }
    assert json.loads(sent[2].content) == {"value": False}

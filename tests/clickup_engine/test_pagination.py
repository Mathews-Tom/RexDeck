"""Paired comment cursors, overlap handling, replies, and full-thread pins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from rexdeck.clickup.engine import reads
from rexdeck.clickup.engine.client import Client
from rexdeck.clickup.engine.diagnostics import EngineError

from .conftest import api, fixture, main

TASK_ID = "synaa0001"
COMMENTS = f"/task/{TASK_ID}/comment"


def comments(count: int) -> list[dict[str, Any]]:
    """Newest-first synthetic comments, including equal-date cursor boundaries."""
    return [
        {
            "id": str(75000000000 + index),
            "comment_text": f"Synthetic comment {index}",
            "user": {"id": 710000001, "username": "user_one"},
            "resolved": False,
            "reply_count": "0",
            "date": str(1800000000000 - (index // 2) * 1000),
        }
        for index in range(count)
    ]


def test_comment_tree_pages_with_both_cursor_parts_and_preserves_replies() -> None:
    payloads = comments(31)
    payloads[0]["reply_count"] = "2"
    requests: list[httpx.Request] = []
    reply_requests: list[httpx.Request] = []
    boundary = payloads[24]

    def page(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert "start" not in request.url.params
            assert "start_id" not in request.url.params
            return httpx.Response(200, json={"comments": payloads[:25]})
        assert dict(request.url.params) == {
            "start": boundary["date"],
            "start_id": boundary["id"],
        }
        # APIs can repeat the cursor item. It must not appear twice in the tree.
        return httpx.Response(200, json={"comments": payloads[24:]})

    def replies(request: httpx.Request) -> httpx.Response:
        reply_requests.append(request)
        return httpx.Response(200, json=fixture("comment_replies"))

    client = Client(
        "synthetic-test-token",
        transport=api(
            {
                COMMENTS: page,
                f"/comment/{payloads[0]['id']}/reply": replies,
            }
        ),
    )
    try:
        tree = reads.comment_tree(client, TASK_ID)
    finally:
        client.close()

    assert len(requests) == 2
    assert len(reply_requests) == 1
    assert [raw["id"] for raw, depth in tree if depth == 0] == [
        raw["id"] for raw in payloads
    ]
    assert [raw["comment_text"] for raw, depth in tree if depth == 1] == [
        "Reply body one.",
        "Reply body two.",
    ]
    assert [depth for _, depth in tree[:4]] == [0, 1, 1, 0]


def test_an_exactly_full_comment_page_checks_the_next_cursor_page() -> None:
    payloads = comments(25)
    requests: list[httpx.Request] = []

    def page(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"comments": payloads if len(requests) == 1 else []}
        )

    client = Client("synthetic-test-token", transport=api({COMMENTS: page}))
    try:
        result = reads.paged_comments(client, TASK_ID)
    finally:
        client.close()

    assert [row["id"] for row in result] == [row["id"] for row in payloads]
    assert len(requests) == 2
    assert requests[1].url.params["start"] == payloads[-1]["date"]
    assert requests[1].url.params["start_id"] == payloads[-1]["id"]


def test_a_repeated_comment_cursor_is_an_error_not_an_infinite_loop() -> None:
    payloads = comments(25)
    requests: list[httpx.Request] = []

    def page(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"comments": payloads})

    client = Client("synthetic-test-token", transport=api({COMMENTS: page}))
    try:
        with pytest.raises(EngineError) as caught:
            reads.paged_comments(client, TASK_ID)
    finally:
        client.close()

    assert caught.value.diagnostic.code == "result/repeated-cursor"
    assert len(requests) == 2


def test_comment_pin_covers_membership_beyond_the_first_25_comments(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    payloads = comments(31)
    sent: list[httpx.Request] = []
    seen_cursors: list[str | None] = []

    def page(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            sent.append(request)
            return httpx.Response(200, json=fixture("comment_created"))
        cursor = request.url.params.get("start_id")
        seen_cursors.append(cursor)
        if cursor is None:
            return httpx.Response(200, json={"comments": payloads[:25]})
        assert cursor == payloads[24]["id"]
        assert request.url.params["start"] == payloads[24]["date"]
        return httpx.Response(200, json={"comments": payloads[24:]})

    route[COMMENTS] = page
    argv = ["comment", TASK_ID, "--text", "Synthetic acknowledgement"]
    assert main([*argv, "--dry-run", "--json"]) == 0
    staged = json.loads(capsys.readouterr().out)
    digest = staged["mutation"]["field_digest"]

    # Only the final item changes, outside the first page's membership.
    payloads[-1] = {**payloads[-1], "id": "75000000999"}
    assert main([*argv, "--expect-digest", digest, "--json"]) == 1
    refused = json.loads(capsys.readouterr().out)

    assert sent == []
    assert seen_cursors == [None, payloads[24]["id"], None, payloads[24]["id"]]
    assert refused["diagnostics"][0]["code"] == "state/changed-under-us"


def test_comment_page_ceiling_refuses_an_incomplete_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    payloads = comments(75)
    monkeypatch.setattr(reads, "PAGE_LIMIT", 2)

    def page(request: httpx.Request) -> httpx.Response:
        offset = len(requests) * 25
        requests.append(request)
        return httpx.Response(200, json={"comments": payloads[offset : offset + 25]})

    connection = Client("synthetic-test-token", transport=api({COMMENTS: page}))
    try:
        with pytest.raises(EngineError) as caught:
            reads.paged_comments(connection, TASK_ID)
    finally:
        connection.close()
    assert caught.value.diagnostic.code == "result/page-ceiling"
    assert len(requests) == 2


def test_a_short_terminal_comment_page_needs_no_cursor_date() -> None:
    connection = Client(
        "synthetic-test-token",
        transport=api({COMMENTS: {"comments": [{"id": "75000000001"}]}}),
    )
    try:
        assert reads.paged_comments(connection, TASK_ID) == [{"id": "75000000001"}]
    finally:
        connection.close()

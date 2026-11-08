"""The write verbs end to end, through a routed transport. Zero live calls.

Both verbs read before they write, and both share a path between the read and
the write — `PUT /task/{id}` follows `GET /task/{id}`, and the comment POST
follows the comment GET. The routes here answer on the method for that reason,
which is also how a test gets hold of the body that was actually sent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from .conftest import fixture, main

TASK = "/task/synaa0001"
COMMENTS = "/task/synaa0001/comment"
CHANNEL = "/task/synaa0001/field/10000000-0000-4000-8000-000000000001"
BUDGET = "/task/synaa0001/field/10000000-0000-4000-8000-000000000002"


def _receipt(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = json.loads(capsys.readouterr().out)
    assert isinstance(captured, dict)
    return captured


def _mutation(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    mutation = _receipt(capsys)["mutation"]
    assert isinstance(mutation, dict)
    return mutation


def _task_routes(
    sent: list[httpx.Request], *, overrides: dict[str, Any] | None = None
) -> object:
    """Answer `GET /task/{id}` from the fixture and record whatever is written."""
    payload = {**fixture("task"), **(overrides or {})}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            sent.append(request)
        return httpx.Response(200, json=payload)

    return answer


def _recording(sent: list[httpx.Request], payload: object) -> object:
    """Answer with `payload` and keep the request, so a test can read its body."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=payload)

    return answer


# ------------------------------------------------------------------------------- touch


def test_touch_dry_run_prints_the_digest_and_sends_nothing(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)

    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )

    mutation = _mutation(capsys)
    assert sent == []
    assert mutation["target"] == "synaa0001"
    diff = mutation["diff"]
    assert isinstance(diff, dict)
    assert diff["status"]["from"] == "in progress"
    assert diff["status"]["to"] == "in review"
    assert len(str(mutation["field_digest"])) == 64


def test_touch_applies_the_status_it_staged(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)

    assert (
        main(["touch", "synaa0001", "--status", "in review", "--json"], pin_write=True)
        == 0
    )

    del capsys
    assert [request.method for request in sent] == ["PUT"]
    assert json.loads(sent[0].content) == {"status": "in review"}


def test_the_field_digest_ignores_activity_on_a_field_the_write_does_not_touch(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A whole-object digest would abort here; ClickUp bumps `date_updated` freely."""
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent, overrides={"date_updated": "1757462400000"})
    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )
    first = _mutation(capsys)["field_digest"]

    route[TASK] = _task_routes(
        sent, overrides={"date_updated": "1799999999999", "name": "Task alpha renamed"}
    )
    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )

    assert _mutation(capsys)["field_digest"] == first


def test_a_pin_that_still_matches_applies_and_carries_no_finding(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)
    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )
    pinned = str(_mutation(capsys)["field_digest"])

    assert (
        main(
            [
                "touch",
                "synaa0001",
                "--status",
                "in review",
                "--expect-digest",
                pinned,
                "--json",
            ]
        )
        == 0
    )

    receipt = _receipt(capsys)
    assert receipt["diagnostics"] == []
    assert [request.method for request in sent] == ["PUT"]


def test_a_stale_pin_refuses_and_names_the_field_that_moved(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)
    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )
    pinned = str(_mutation(capsys)["field_digest"])

    route[TASK] = _task_routes(
        sent,
        overrides={"status": {"status": "blocked", "color": "#000", "type": "custom"}},
    )
    assert (
        main(
            [
                "touch",
                "synaa0001",
                "--status",
                "in review",
                "--expect-digest",
                pinned,
                "--json",
            ]
        )
        == 1
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    finding = diagnostics[0]
    assert finding["code"] == "state/changed-under-us"
    assert finding["message"] == "status no longer holds what the dry run recorded"
    assert finding["evidence"]["expected"] == pinned
    assert finding["evidence"]["fields"]["status"]["current"] == "blocked"
    assert sent == []


def test_a_pin_over_several_fields_names_every_field_it_covered(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two fields under one sha256: the engine knows one moved, not which."""
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)
    argv = ["touch", "synaa0001", "--status", "in review", "--due", "2026-10-01"]
    assert main([*argv, "--dry-run", "--json"]) == 0
    pinned = str(_mutation(capsys)["field_digest"])

    route[TASK] = _task_routes(
        sent,
        overrides={"status": {"status": "blocked", "color": "#000", "type": "custom"}},
    )
    assert main([*argv, "--expect-digest", pinned, "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    finding = diagnostics[0]
    assert finding["message"] == (
        "the fields this write pins no longer hold what the dry run recorded"
    )
    assert sorted(finding["evidence"]["fields"]) == ["due_date", "status"]


def test_the_two_receipts_together_name_the_field_the_refusal_could_not(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One sha256 cannot attribute a multi-field mismatch; two receipts can."""
    del synced
    route[TASK] = _task_routes([])
    argv = ["touch", "synaa0001", "--status", "in review", "--due", "2026-10-01"]
    assert main([*argv, "--dry-run", "--json"]) == 0
    staged = _mutation(capsys)
    pinned = str(staged["field_digest"])
    diff = staged["diff"]
    assert isinstance(diff, dict)

    route[TASK] = _task_routes(
        [],
        overrides={"status": {"status": "blocked", "color": "#000", "type": "custom"}},
    )
    assert main([*argv, "--expect-digest", pinned, "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    finding = diagnostics[0]
    now = finding["evidence"]["fields"]
    moved = [name for name in diff if diff[name]["digest"] != now[name]["digest"]]
    assert moved == ["status"]
    assert finding["supported_fixes"][0] == "mutation.diff"


@pytest.mark.parametrize(
    "arguments",
    (
        ["touch", "synaa0001", "--status", "in review"],
        ["comment", "synaa0001", "--text", "Synthetic comment"],
    ),
)
def test_an_unpinned_write_is_refused_without_transport(
    route: dict[str, Any],
    synced: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)

    assert main([*arguments, "--json"]) == 2

    receipt = _receipt(capsys)
    assert sent == []
    assert receipt["budget"]["api_calls_made"] == 0
    assert receipt["diagnostics"][0]["code"] == "usage/missing-expect-digest"


def test_a_dry_run_is_not_an_unpinned_write(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing was written, so there is nothing to warn about having written."""
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )

    assert _receipt(capsys)["diagnostics"] == []


def test_touch_with_no_field_to_set_is_a_usage_error_before_any_call(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced, route

    assert main(["touch", "synaa0001", "--dry-run", "--json"]) == 2

    receipt = _receipt(capsys)
    diagnostics = receipt["diagnostics"]
    budget = receipt["budget"]
    assert isinstance(diagnostics, list) and isinstance(budget, dict)
    assert diagnostics[0]["code"] == "usage/nothing-to-write"
    assert budget["api_calls_made"] == 0


def test_setting_one_assignee_removes_the_one_it_displaces(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ClickUp spells assignees as a delta, so `add` alone would append."""
    del synced, capsys
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)

    assert (
        main(
            ["touch", "synaa0001", "--assignee", "user_two", "--quiet"], pin_write=True
        )
        == 0
    )

    assert json.loads(sent[0].content) == {
        "assignees": {"add": [710000002], "rem": [710000001]}
    }


def test_the_due_date_is_sent_as_the_epoch_clickup_stores(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced, capsys
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)

    assert (
        main(["touch", "synaa0001", "--due", "2026-10-01", "--quiet"], pin_write=True)
        == 0
    )

    assert json.loads(sent[0].content) == {"due_date": 1790812800000}


def test_a_malformed_due_date_names_the_flag_that_carried_it(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parser is shared with `--due-before`; naming that flag here would mislead."""
    del synced, route

    assert (
        main(["touch", "synaa0001", "--due", "01/10/2026", "--dry-run", "--json"]) == 2
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["supported_fixes"] == ["--due YYYY-MM-DD"]


@pytest.mark.parametrize("given", ["-1", "three", "nan", "inf", ""])
def test_invalid_sprint_points_are_refused_before_any_call(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str], given: str
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _recording(sent, {})

    assert main(["touch", "synaa0001", "--points", given, "--dry-run", "--json"]) == 2

    assert sent == []
    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "usage/bad-points"


def test_fractional_sprint_points_stay_fractional_and_stage_the_held_value(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent, overrides={"points": 2})
    argv = ["touch", "synaa0001", "--points", "0.5"]
    assert main([*argv, "--dry-run", "--json"]) == 0
    diff = _mutation(capsys)["diff"]["points"]
    assert (diff["from"], diff["to"]) == ("2", "0.5")

    assert main([*argv, "--quiet"], pin_write=True) == 0

    assert json.loads(sent[0].content) == {"points": 0.5}


def test_a_description_edit_stages_the_markdown_form_it_replaces(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Staging the plain text would hide formatting the replacement discards.

    Like ClickUp, the route returns the Markdown form only when asked for it.
    """
    del synced
    payload = {
        **fixture("task"),
        "description": "Old plain",
        "markdown_description": "Old **bold**",
    }

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.params.get("include_markdown_description") == "true":
            return httpx.Response(200, json=payload)
        return httpx.Response(
            200, json={k: v for k, v in payload.items() if k != "markdown_description"}
        )

    route[TASK] = answer

    assert (
        main(["touch", "synaa0001", "--description", "New", "--dry-run", "--json"]) == 0
    )

    assert _mutation(capsys)["diff"]["description"]["from"] == "Old **bold**"


# ----------------------------------------------------------------------- custom fields


def test_a_custom_field_goes_through_the_endpoint_update_task_does_not_carry(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`PUT /task/{id}` has no `custom_fields` body; ClickUp requires the field endpoint."""
    del synced, capsys
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)
    route[CHANNEL] = _recording(sent, {})

    assert (
        main(
            ["touch", "synaa0001", "--field", "Channel=Paid Social", "--quiet"],
            pin_write=True,
        )
        == 0
    )

    assert [request.method for request in sent] == ["POST"]
    assert json.loads(sent[0].content) == {
        "value": "20000000-0000-4000-8000-000000000002"
    }


def test_a_dropdown_diff_reads_as_option_names_not_as_uuids(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(
            [
                "touch",
                "synaa0001",
                "--field",
                "Channel=Paid Social",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    diff = _mutation(capsys)["diff"]
    assert isinstance(diff, dict)
    assert diff["field:Channel"]["from"] == "Paid Search"
    assert diff["field:Channel"]["to"] == "Paid Social"


def test_a_number_field_is_sent_as_a_number(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced, capsys
    sent: list[httpx.Request] = []
    route[TASK] = _task_routes(sent)
    route[BUDGET] = _recording(sent, {})

    assert (
        main(
            ["touch", "synaa0001", "--field", "Budget=2400", "--quiet"], pin_write=True
        )
        == 0
    )

    assert json.loads(sent[0].content) == {"value": 2400}


def test_a_value_the_field_type_refuses_offers_the_values_it_accepts(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(
            [
                "touch",
                "synaa0001",
                "--field",
                "Channel=Billboards",
                "--dry-run",
                "--json",
            ]
        )
        == 2
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "usage/bad-field-value"
    assert diagnostics[0]["supported_fixes"] == ["Paid Search", "Paid Social"]


def test_an_unwritable_field_type_is_refused_rather_than_guessed_at(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writing the wrong shape into a field ClickUp accepts is a silent corruption."""
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(["touch", "synaa0001", "--field", "Shape=here", "--dry-run", "--json"])
        == 2
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "usage/unsupported-field-type"
    assert diagnostics[0]["evidence"]["type"] == "location"


def test_an_unknown_field_name_offers_the_names_the_list_actually_has(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(
            [
                "touch",
                "synaa0001",
                "--field",
                "Chanel=Paid Social",
                "--dry-run",
                "--json",
            ]
        )
        == 2
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "usage/unknown-field"
    assert diagnostics[0]["supported_fixes"] == ["Channel", "Budget", "Signed off"]


def test_a_field_argument_that_is_not_name_equals_value_is_a_usage_error(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(["touch", "synaa0001", "--field", "Channel", "--dry-run", "--json"]) == 2
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "usage/bad-field"


# ----------------------------------------------------------------------------- comment


def test_comment_dry_run_pins_the_membership_of_the_thread_it_is_joining(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            sent.append(request)
        return httpx.Response(200, json=fixture("task_comments"))

    route[COMMENTS] = answer

    assert (
        main(["comment", "synaa0001", "--text", "Looks right", "--dry-run", "--json"])
        == 0
    )

    assert sent == []
    diff = _mutation(capsys)["diff"]
    assert isinstance(diff, dict)
    assert diff["comments"]["from"] == "2 comments"
    assert diff["comments"]["to"] == "Looks right"


def test_comment_posts_the_body_without_paging_everyone_watching(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced, capsys
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            sent.append(request)
            return httpx.Response(200, json=fixture("comment_created"))
        return httpx.Response(200, json=fixture("task_comments"))

    route[COMMENTS] = answer

    assert (
        main(
            ["comment", "synaa0001", "--text", "Looks right", "--quiet"], pin_write=True
        )
        == 0
    )

    assert json.loads(sent[0].content) == {
        "comment_text": "Looks right",
        "notify_all": False,
    }


def _appended(extra_id: str) -> dict[str, Any]:
    """Return the recorded thread with one more comment on the end of it.

    An append rather than a replacement, because ClickUp returns comments
    oldest-first: a pin on the first element survives this untouched, so this is
    the exact shape a thread-head pin cannot see.
    """
    existing = fixture("task_comments")["comments"]
    assert isinstance(existing, list)
    return {
        "comments": [
            *existing,
            {
                "id": extra_id,
                "comment_text": "Third comment body.",
                "user": {"id": 710000002, "username": "user_two"},
                "resolved": False,
                "reply_count": "0",
                "date": "1757721600000",
            },
        ]
    }


def _pin_a_comment(capsys: pytest.CaptureFixture[str]) -> str:
    assert (
        main(["comment", "synaa0001", "--text", "Looks right", "--dry-run", "--json"])
        == 0
    )
    return str(_mutation(capsys)["field_digest"])


def test_comment_refuses_when_a_reply_landed_between_the_stage_and_the_apply(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case the verb exists to catch, and the one an ordering-dependent pin misses."""
    del synced
    route[COMMENTS] = fixture("task_comments")
    pinned = _pin_a_comment(capsys)

    route[COMMENTS] = _appended("73000000009")
    assert (
        main(
            [
                "comment",
                "synaa0001",
                "--text",
                "Looks right",
                "--expect-digest",
                pinned,
                "--json",
            ]
        )
        == 1
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "state/changed-under-us"
    assert (
        diagnostics[0]["message"]
        == "comments no longer holds what the dry run recorded"
    )


def test_comment_refuses_when_a_comment_was_deleted_under_the_pin(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sorted ID set catches a thread shrinking as well as one growing."""
    del synced
    route[COMMENTS] = fixture("task_comments")
    pinned = _pin_a_comment(capsys)

    thread = fixture("task_comments")["comments"]
    assert isinstance(thread, list)
    route[COMMENTS] = {"comments": thread[:1]}
    assert (
        main(
            [
                "comment",
                "synaa0001",
                "--text",
                "Looks right",
                "--expect-digest",
                pinned,
                "--json",
            ]
        )
        == 1
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "state/changed-under-us"


def test_a_comment_carrying_no_id_is_a_receipt_rather_than_a_key_error(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pin is built from IDs, and this verb's whole output is a receipt."""
    del synced
    route[COMMENTS] = {"comments": [{"comment_text": "no id on this one"}]}

    assert (
        main(["comment", "synaa0001", "--text", "Looks right", "--dry-run", "--json"])
        == 1
    )

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "response/unexpected-shape"
    assert diagnostics[0]["evidence"]["path"] == COMMENTS


def test_a_pin_survives_clickup_returning_the_same_thread_in_another_order(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Order is the vendor's business; membership is what the pin is about."""
    del synced
    route[COMMENTS] = fixture("task_comments")
    pinned = _pin_a_comment(capsys)

    thread = fixture("task_comments")["comments"]
    assert isinstance(thread, list)
    route[COMMENTS] = {"comments": list(reversed(thread))}
    assert (
        main(
            [
                "comment",
                "synaa0001",
                "--text",
                "Looks right",
                "--expect-digest",
                pinned,
                "--quiet",
            ]
        )
        == 0
    )


def test_a_task_with_no_comments_yet_still_pins_a_thread_state(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[COMMENTS] = {"comments": []}

    assert main(["comment", "synaa0001", "--text", "First", "--dry-run", "--json"]) == 0

    diff = _mutation(capsys)["diff"]
    assert isinstance(diff, dict)
    assert diff["comments"]["from"] == "0 comments"


# ---------------------------------------------------------------------------- receipts


def test_write_dry_runs_keep_receipt_call_accounting(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])
    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--json"])
        == 0
    )
    touched = _receipt(capsys)
    route[COMMENTS] = fixture("task_comments")
    assert (
        main(["comment", "synaa0001", "--text", "Looks right", "--dry-run", "--json"])
        == 0
    )
    commented = _receipt(capsys)
    assert touched["budget"] == {"api_calls_made": 1}
    assert commented["budget"] == {"api_calls_made": 1}
    assert touched["mutation"]["target"] == "synaa0001"
    assert commented["mutation"]["target"] == "synaa0001"


def test_a_write_prints_the_mutation_and_no_entity_listing(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASK] = _task_routes([])

    assert (
        main(["touch", "synaa0001", "--status", "in review", "--dry-run", "--quiet"])
        == 0
    )

    printed = capsys.readouterr().out
    assert printed.startswith("target   synaa0001")
    assert "applied  no — dry run" in printed
    assert "Task alpha" not in printed


def test_a_read_verb_emits_no_mutation_block_at_all(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route["/list/70000009999/task"] = fixture("list_tasks")

    assert main(["tasks", "--json"]) == 0

    assert "mutation" not in _receipt(capsys)


def test_touch_help_carries_the_staging_flags_on_its_own(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["touch", "--help"])

    described = capsys.readouterr().out
    assert "--dry-run" in described
    assert "--expect-digest" in described

"""Projection correctness against recorded payloads, custom fields especially."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from rexdeck.clickup import tui
from rexdeck.clickup.engine import projections, runtime
from rexdeck.clickup.engine.diagnostics import EngineError
from rexdeck.clickup.engine.records import CustomField, FieldOption

from .conftest import fixture, walked_topology

FIELDS = walked_topology().custom_fields["70000009999"]


def _tasks() -> list[dict[str, Any]]:
    tasks = fixture("list_tasks")["tasks"]
    assert isinstance(tasks, list)
    return tasks


# ---------------------------------------------------------------------------- rows


def test_a_task_row_carries_the_five_columns_and_not_the_other_thirty_keys() -> None:
    row = projections.task_row(_tasks()[0])

    assert row == projections.TaskRow(
        id="synaa0001",
        status="in progress",
        name="Task alpha",
        assignee="user_one",
        due="2025-09-20",
    )


def test_a_task_with_no_assignee_reads_as_absent_rather_than_as_an_empty_column() -> (
    None
):
    assert projections.task_row(_tasks()[1]).assignee == projections.ABSENT


def test_a_task_with_no_due_date_reads_as_absent() -> None:
    assert projections.task_row(_tasks()[1]).due == projections.ABSENT


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/Los_Angeles"])
def test_calendar_dates_stay_local(zone: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as local:
        local.setenv("TZ", zone)
        time.tzset()
        try:
            midnight = runtime.epoch_ms("2026-11-17", fix="--due")
            assert datetime.fromtimestamp(midnight / 1000) == datetime(2026, 11, 17)
            assert projections.epoch_date(midnight) == "2026-11-17"
            normalized = int(datetime(2026, 11, 17, 4).timestamp() * 1000)
            assert projections.epoch_date(normalized) == "2026-11-17"
            abbreviation = datetime(2026, 11, 17, 4).astimezone().tzname()
            assert tui.date_label(str(normalized)) == f"2026-11-17 04:00 {abbreviation}"
        finally:
            local.undo()
            time.tzset()


# ------------------------------------------------------------------- custom fields


def test_a_dropdown_value_spelled_as_an_option_id_resolves_to_the_option_name() -> None:
    resolved, findings = projections.resolve_fields(
        _tasks()[0]["custom_fields"], FIELDS
    )

    assert dict(resolved)["Channel"] == "Paid Social"
    assert findings == ()


def test_a_dropdown_value_spelled_as_an_orderindex_resolves_to_the_option_name() -> (
    None
):
    """ClickUp spells a chosen dropdown two ways; the registry carries both keys."""
    resolved, _ = projections.resolve_fields(fixture("task")["custom_fields"], FIELDS)

    assert dict(resolved)["Channel"] == "Paid Search"


def test_a_checkbox_renders_as_yes_or_no() -> None:
    resolved, _ = projections.resolve_fields(fixture("task")["custom_fields"], FIELDS)

    assert dict(resolved)["Signed off"] == "yes"


def test_a_number_field_renders_its_value() -> None:
    resolved, _ = projections.resolve_fields(fixture("task")["custom_fields"], FIELDS)

    assert dict(resolved)["Budget"] == "1200"


def test_a_field_type_this_engine_does_not_render_is_shown_raw_and_warned_about_once() -> (
    None
):
    resolved, findings = projections.resolve_fields(
        fixture("task")["custom_fields"], FIELDS
    )

    assert dict(resolved)["Shape"] == '{"lat": 0, "lng": 0}'
    unknown = [one for one in findings if one.code == "projection/unknown-field-type"]
    assert len(unknown) == 1
    assert unknown[0].evidence["type"] == "location"


def test_a_field_the_registry_has_no_definition_for_is_a_finding_not_a_guess() -> None:
    _, findings = projections.resolve_fields(fixture("task")["custom_fields"], FIELDS)

    unknown = [one for one in findings if one.code == "projection/unknown-field"]
    assert len(unknown) == 1
    assert unknown[0].subject == "10000000-0000-4000-8000-000000000009"
    assert unknown[0].supported_fixes == (projections.RESYNC,)


def test_an_option_the_registry_cannot_name_is_a_finding_not_a_bare_uuid() -> None:
    """An unknown field warns and an unknown option did not — same staleness, one fix."""
    channel = next(one for one in FIELDS if one.name == "Channel")
    blobs = [
        {
            "id": channel.id,
            "name": "Channel",
            "type": "drop_down",
            "value": "op-added-later",
        }
    ]

    resolved, findings = projections.resolve_fields(blobs, FIELDS)

    assert dict(resolved)["Channel"] == "op-added-later"
    unknown = [one for one in findings if one.code == "projection/unknown-option"]
    assert len(unknown) == 1
    assert unknown[0].subject == "Channel"
    assert unknown[0].evidence["option"] == "op-added-later"
    assert unknown[0].supported_fixes == (projections.RESYNC,)


def test_a_custom_field_blob_that_is_not_an_object_raises_rather_than_vanishing() -> (
    None
):
    """An unset field carries no `value` and is skipped; a shape violation is not."""
    with pytest.raises(EngineError) as caught:
        projections.resolve_fields(["not an object"], FIELDS)

    assert caught.value.diagnostic.code == "response/unexpected-shape"


def test_a_field_carrying_no_value_at_all_is_simply_unset() -> None:
    channel = next(one for one in FIELDS if one.name == "Channel")

    resolved, findings = projections.resolve_fields([{"id": channel.id}], FIELDS)

    assert resolved == ()
    assert findings == ()


def test_an_undefined_field_is_dropped_rather_than_printed_under_its_uuid() -> None:
    resolved, _ = projections.resolve_fields(fixture("task")["custom_fields"], FIELDS)

    assert "10000000-0000-4000-8000-000000000009" not in dict(resolved)


def test_resolution_uses_the_cached_definition_not_the_name_the_payload_carries() -> (
    None
):
    renamed = (
        CustomField(
            id="10000000-0000-4000-8000-000000000002",
            name="Budget approved",
            type="number",
            options=(),
        ),
    )

    resolved, _ = projections.resolve_fields(
        [
            {
                "id": "10000000-0000-4000-8000-000000000002",
                "name": "Budget",
                "value": "1200",
            }
        ],
        renamed,
    )

    assert resolved == (("Budget approved", "1200"),)


def test_a_labels_field_names_every_option_chosen() -> None:
    labels = (
        CustomField(
            id="cf-labels",
            name="Tags",
            type="labels",
            options=(
                FieldOption(id="a", name="urgent", orderindex=0),
                FieldOption(id="b", name="client", orderindex=1),
            ),
        ),
    )

    resolved, _ = projections.resolve_fields(
        [{"id": "cf-labels", "value": ["a", "b"]}], labels
    )

    assert resolved == (("Tags", "urgent | client"),)


def test_a_users_field_names_the_people_not_their_ids() -> None:
    users = (CustomField(id="cf-users", name="Reviewer", type="users", options=()),)

    resolved, _ = projections.resolve_fields(
        [{"id": "cf-users", "value": [{"id": 1, "username": "user_two"}]}], users
    )

    assert resolved == (("Reviewer", "user_two"),)


def test_a_date_field_renders_as_a_utc_calendar_date() -> None:
    dates = (CustomField(id="cf-date", name="Kickoff", type="date", options=()),)

    resolved, _ = projections.resolve_fields(
        [{"id": "cf-date", "value": "1758326400000"}], dates
    )

    assert resolved == (("Kickoff", "2025-09-20"),)


def test_an_empty_value_reads_as_absent_rather_than_as_the_string_none() -> None:
    fields = (CustomField(id="cf-text", name="Note", type="text", options=()),)

    resolved, _ = projections.resolve_fields([{"id": "cf-text", "value": None}], fields)

    assert resolved == (("Note", projections.ABSENT),)


# ------------------------------------------------------------------------ comments


def test_comments_flatten_in_order_with_replies_one_level_deeper() -> None:
    comments = fixture("task_comments")["comments"]
    replies = fixture("comment_replies")["comments"]
    assert isinstance(comments, list) and isinstance(replies, list)

    flattened = [
        projections.comment_line(comments[0], depth=0),
        *(projections.comment_line(reply, depth=1) for reply in replies),
        projections.comment_line(comments[1], depth=0),
    ]

    assert [line.depth for line in flattened] == [0, 1, 1, 0]
    assert [line.author for line in flattened] == [
        "user_one",
        "user_two",
        "user_one",
        "user_two",
    ]


def test_reply_count_reads_the_string_clickup_sends() -> None:
    comments = fixture("task_comments")["comments"]
    assert isinstance(comments, list)

    assert projections.reply_count(comments[0]) == 2
    assert projections.reply_count(comments[1]) == 0


# -------------------------------------------------------------------------- sprint


def test_the_sprint_rollup_counts_each_status_once() -> None:
    payloads = _tasks()
    rows = [projections.task_row(payload) for payload in payloads]

    view = projections.sprint_view(rows, payloads)

    assert view.total == 3
    assert view.statuses == (("blocked", 1), ("complete", 1), ("in progress", 1))


def test_a_blocked_status_and_a_blocker_tag_both_count_as_blocked() -> None:
    payloads = _tasks()
    rows = [projections.task_row(payload) for payload in payloads]

    view = projections.sprint_view(rows, payloads)

    assert [row.id for row in view.blocked] == ["synaa0002", "synaa0003"]


def test_unassigned_is_computed_from_the_projected_row() -> None:
    payloads = _tasks()
    rows = [projections.task_row(payload) for payload in payloads]

    view = projections.sprint_view(rows, payloads)

    assert [row.id for row in view.unassigned] == ["synaa0002"]


# ------------------------------------------------------------------------ rendering


def test_an_empty_result_says_none_rather_than_printing_a_bare_header() -> None:
    assert projections.render_tasks([]).endswith("(none)")


def test_the_table_pads_every_column_to_its_widest_value() -> None:
    rendered = projections.table(("A", "BB"), [("x", "y"), ("longer", "z")])

    assert rendered.splitlines() == ["A       BB", "x       y", "longer  z"]


def test_no_rendered_line_ends_in_whitespace() -> None:
    """Asserted over the renderer, not the one header that showed the bug.

    A column set nobody has written yet is the case this is for: any table whose
    last column holds values shorter than its own name padded the header past
    the widest cell under it.
    """
    rendered = projections.table(
        ("ID", "STATUS", "ASSIGNEE"),
        [("1", "open", "-"), ("22", "in review", "user_one")],
    )

    assert rendered.splitlines() == [
        "ID  STATUS     ASSIGNEE",
        "1   open       -",
        "22  in review  user_one",
    ]
    assert not any(line != line.rstrip() for line in rendered.splitlines())


def test_a_long_name_is_never_clipped_because_a_silent_loss_is_worse_than_a_ragged_row() -> (
    None
):
    name = "x" * 200
    rendered = projections.render_tasks(
        [projections.TaskRow(id="1", status="open", name=name, assignee="-", due="-")]
    )

    assert name in rendered


def test_a_payload_missing_a_field_the_projection_reads_fails_rather_than_defaulting() -> (
    None
):
    with pytest.raises(ValueError, match="'name'"):
        projections.task_row({"id": "synaa0001"})

"""Every verb end to end, through a routed transport. Zero live calls, every exit code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from rexdeck.clickup.engine import registry
from rexdeck.clickup.engine.records import ListRecord, Registry, Topology

from .conftest import (
    fixture,
    main,
    point_registry,
    seed_registry,
    unsynced_yaml,
    walk_routes,
)

TASKS = "/list/70000009999/task"
TEAM_TASKS = "/team/7000000001/task"


def _receipt(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = json.loads(capsys.readouterr().out)
    assert isinstance(captured, dict)
    return captured


# ---------------------------------------------------------------------------- sync


def test_sync_walks_the_seed_space_and_reports_what_it_found(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route.update(walk_routes())

    assert main(["sync", "--json"]) == 0

    receipt = _receipt(capsys)
    budget = receipt["budget"]
    assert isinstance(budget, dict)
    # One space, its folders, its folderless lists, the team, and one field
    # call per list found.
    assert budget["api_calls_made"] == 7


def test_sync_without_write_registry_reports_and_writes_nothing(
    route: dict[str, Any], synced: Path
) -> None:
    route.update(walk_routes())
    before = synced.read_bytes()

    assert main(["sync"]) == 0
    assert synced.read_bytes() == before


def test_sync_write_registry_stamps_the_file_it_writes(
    route: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fresh.yaml"
    # Build a synthetic registry with no discovered topology.
    registry.rewrite(seed_registry(), now="2026-01-01T00:00:00Z", path=path)
    decoy = tmp_path / "unselected.yaml"
    decoy.write_text("not a valid registry", encoding="utf-8")
    point_registry(monkeypatch, decoy)
    decoy_before = decoy.read_bytes()
    route.update(walk_routes())

    assert main(["sync", "--registry-path", str(path), "--write-registry"]) == 0
    assert decoy.read_bytes() == decoy_before

    written = registry.load(path)
    assert written.synced_at is not None
    assert [record.name for record in written.topology.lists] == [
        "Project Beta",
        "Sample Inbox",
        "Project Gamma",
    ]


def test_sync_is_exempt_from_unsynced_because_it_is_the_fix(
    route: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "never.yaml"
    path.write_text(unsynced_yaml(), encoding="utf-8")
    point_registry(monkeypatch, path)
    route.update(walk_routes())

    assert main(["sync"]) == 0


def test_the_walk_records_members_as_id_and_username_and_never_an_email(
    route: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fresh.yaml"
    path.write_bytes(registry.registry_path().read_bytes())
    point_registry(monkeypatch, path)
    route.update(walk_routes())

    assert main(["sync", "--write-registry"]) == 0

    assert "@example.com" not in path.read_text(encoding="utf-8")
    assert [member.username for member in registry.load(path).topology.members] == [
        "user_one",
        "user_two",
    ]


# --------------------------------------------------------------------------- tasks


def test_tasks_prints_one_line_per_task(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["ID", "STATUS", "NAME", "ASSIGNEE", "DUE"]
    assert len(out.strip().splitlines()) == 4


def test_tasks_resolves_me_from_the_registry_without_spending_a_call(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--assignee", "me", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    # One call: the filter. `me` came off the seeds, so no resolve call exists
    # to get the ordering wrong.
    assert budget["api_calls_made"] == 1


def test_tasks_sends_the_resolved_assignee_id_rather_than_the_username(
    route: dict[str, Any], synced: Path
) -> None:
    del synced
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json=fixture("list_tasks"))

    route[TASKS] = answer

    assert main(["tasks", "--assignee", "user_two", "--quiet"]) == 0
    assert "assignees%5B%5D=710000002" in sent[0]


def test_tasks_projects_rows_without_fetching_each_task(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 1


def test_tasks_truncates_at_the_row_ceiling_and_says_so(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--max-rows", "1", "--json"]) == 0

    receipt = _receipt(capsys)
    result = receipt["result"]
    diagnostics = receipt["diagnostics"]
    assert isinstance(result, dict) and isinstance(diagnostics, list)
    assert result == {"rows": 1, "truncated": True}
    assert diagnostics[0]["code"] == "budget/truncated"
    assert diagnostics[0]["evidence"] == {"returned": 1, "total": 3}
    assert diagnostics[0]["supported_fixes"] == ["--max-rows 3"]


def test_truncation_is_a_warning_so_a_completed_run_still_exits_zero(
    route: dict[str, Any], synced: Path
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--max-rows", "1", "--quiet"]) == 0


def test_a_result_set_exactly_at_the_ceiling_is_not_truncated(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary. Three rows under a ceiling of three is a complete answer."""
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--max-rows", "3", "--json"]) == 0

    receipt = _receipt(capsys)
    assert receipt["result"] == {"rows": 3, "truncated": False}
    assert receipt["diagnostics"] == []


def test_one_row_past_the_ceiling_is_where_the_diagnostic_fires(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--max-rows", "2", "--json"]) == 0

    receipt = _receipt(capsys)
    diagnostics = receipt["diagnostics"]
    assert isinstance(diagnostics, list)
    assert receipt["result"] == {"rows": 2, "truncated": True}
    assert diagnostics[0]["evidence"] == {"returned": 2, "total": 3}


def test_a_retryable_status_offers_a_retry_and_a_bad_id_does_not(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Telling a caller to wait and retry after a 404 sends them round a loop."""
    del synced
    route[TASKS] = httpx.Response(500, json={"err": "boom"})
    assert main(["tasks", "--json"]) == 1
    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["supported_fixes"] == ["sleep 5"]

    route[TASKS] = httpx.Response(404, json={"err": "List not found"})
    assert main(["tasks", "--json"]) == 1
    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["supported_fixes"] == ["<id>"]


def test_a_response_shaped_unlike_the_contract_is_a_receipt_not_a_traceback(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` promises a receipt on stdout; a bare ValueError delivers nothing at all."""
    del synced
    route[TASKS] = {"tasks": {"synaa0001": "not a list"}}

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "response/unexpected-shape"
    assert diagnostics[0]["evidence"]["found"] == "dict"
    assert diagnostics[0]["evidence"]["path"] == TASKS
    assert TASKS in diagnostics[0]["supported_fixes"][0]


def test_a_result_set_that_never_ends_is_refused_rather_than_paged_forever(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    page = {"tasks": fixture("list_tasks")["tasks"], "last_page": False}
    route[TASKS] = page

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "result/page-ceiling"
    assert diagnostics[0]["evidence"]["pages"] == 50
    assert diagnostics[0]["supported_fixes"] == [
        "--status <status>",
        "--due-before YYYY-MM-DD",
        "--list <id>",
    ]


def test_an_unresolvable_list_name_blocks_before_a_request_is_built(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced

    assert main(["tasks", "--list", "INT - Nowhere", "--json"]) == 1

    receipt = _receipt(capsys)
    diagnostics = receipt["diagnostics"]
    budget = receipt["budget"]
    assert isinstance(diagnostics, list) and isinstance(budget, dict)
    assert diagnostics[0]["code"] == "registry/unresolved"
    assert budget["api_calls_made"] == 0


# ---------------------------------------------------------------------------- task


def test_task_folds_the_whole_comment_tree_into_one_invocation(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route.update(
        {
            "/task/synaa0001": fixture("task"),
            "/task/synaa0001/comment": fixture("task_comments"),
            "/comment/73000000001/reply": fixture("comment_replies"),
        }
    )

    assert main(["task", "synaa0001"]) == 0

    out = capsys.readouterr().out
    assert "Task alpha" in out
    assert "comments 4" in out
    assert "Reply body one." in out


def test_task_fetches_only_the_threads_with_replies(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One replied-to comment, so 2 + 1. A task with none, or with nine, differs."""
    del synced
    route.update(
        {
            "/task/synaa0001": fixture("task"),
            "/task/synaa0001/comment": fixture("task_comments"),
            "/comment/73000000001/reply": fixture("comment_replies"),
        }
    )

    assert main(["task", "synaa0001", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 3


def test_task_without_replies_makes_two_calls(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    comments = fixture("task_comments")["comments"]
    route.update(
        {
            "/task/synaa0001": fixture("task"),
            "/task/synaa0001/comment": {"comments": [comments[1]]},
        }
    )
    assert main(["task", "synaa0001", "--json"]) == 0
    assert _receipt(capsys)["budget"]["api_calls_made"] == 2


def test_no_comments_skips_the_comment_fetches_entirely(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route["/task/synaa0001"] = fixture("task")

    assert main(["task", "synaa0001", "--no-comments", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 1


def test_task_resolves_custom_fields_to_names_in_its_header_block(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route.update(
        {
            "/task/synaa0001": fixture("task"),
            "/task/synaa0001/comment": {"comments": []},
        }
    )

    # Two warnings, and warnings do not abort a run that completed.
    assert main(["task", "synaa0001"]) == 0

    out = capsys.readouterr().out
    assert "Channel=Paid Search" in out
    assert "Budget=1200" in out


def test_a_task_id_that_is_not_one_is_a_usage_error(
    route: dict[str, Any], synced: Path
) -> None:
    del synced, route

    assert main(["task", "not an id", "--quiet"]) == 2


def test_help_announces_the_invocation_the_documentation_tells_callers_to_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`engine` alone is not something a caller can type, and the sibling agrees."""
    with pytest.raises(SystemExit):
        main(["--help"])

    assert capsys.readouterr().out.startswith(
        "usage: python3 -m rexdeck.clickup.engine"
    )


def test_help_describes_what_the_tool_gets_a_caller_not_how_it_is_structured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])

    described = capsys.readouterr().out
    assert "argparse" not in described
    assert "ClickUp" in described


def test_an_unknown_verb_exits_two_through_argparse() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["nope"])

    assert caught.value.code == 2


# -------------------------------------------------------------------------- sprint


def test_sprint_rolls_a_named_list_up_per_status(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["sprint", "Project Gamma"]) == 0

    out = capsys.readouterr().out
    assert "tasks    3" in out
    assert "blocked  2" in out
    assert "unassigned 1" in out


def test_sprint_computes_the_rollup_locally_from_one_fetch(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["sprint", "70000009999", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 1


def test_an_ambiguous_list_name_names_the_ids_rather_than_picking_one(
    route: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del route
    from .conftest import seed_registry, walked_topology

    topology = walked_topology()
    collided = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=(
            *topology.lists,
            ListRecord(
                id="70000000999",
                name="Sample Inbox",
                space_id="700000000001",
                folder_id=None,
            ),
        ),
        members=topology.members,
        custom_fields=topology.custom_fields,
    )
    seeds = seed_registry()
    path = tmp_path / "collided.yaml"
    point_registry(monkeypatch, path)
    registry.rewrite(
        Registry(
            schema_version=1,
            digest=None,
            synced_at=None,
            team_id=seeds.team_id,
            seeds=seeds.seeds,
            topology=collided,
        ),
        now="2026-09-13T09:00:00Z",
        path=path,
    )

    assert main(["sprint", "Sample Inbox", "--quiet"]) == 1


# -------------------------------------------------------------------------- search


def test_search_matches_the_query_against_task_names(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "beta"]) == 0

    out = capsys.readouterr().out
    assert "Beta planning brief" in out
    assert "Task alpha" not in out


def test_search_reports_the_type_it_actually_searched(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "task"]) == 0

    assert capsys.readouterr().out.splitlines()[1].split()[1] == "task"


def test_search_restricted_to_a_type_it_cannot_return_finds_nothing(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "beta", "--type", "doc"]) == 0

    assert "(none)" in capsys.readouterr().out


def test_search_says_in_its_own_receipt_that_it_read_tasks_and_nothing_else(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty result must not read as "the workspace has none of that"."""
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "beta", "--json"]) == 0

    receipt = _receipt(capsys)
    diagnostics = receipt["diagnostics"]
    assert isinstance(diagnostics, list)
    finding = next(one for one in diagnostics if one["code"] == "search/tasks-only")
    assert finding["severity"] == "warning"
    assert finding["evidence"]["searched"] == ["task"]
    assert finding["evidence"]["not_searched"] == [
        "doc",
        "whiteboard",
        "dashboard",
        "attachment",
        "chat",
    ]
    assert finding["supported_fixes"] == ["clickup_search"]


def test_the_tasks_only_finding_rides_on_a_search_that_found_nothing_too(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "nothing matches this", "--json"]) == 0

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert [one["code"] for one in diagnostics] == ["search/tasks-only"]


def test_search_makes_one_round_trip(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TEAM_TASKS] = fixture("team_tasks")

    assert main(["search", "beta", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 1


# ------------------------------------------------------------------- output routing


def test_json_puts_the_receipt_on_stdout_and_the_table_on_stderr(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["verb"] == "tasks"
    assert "Task alpha" in captured.err


def test_quiet_with_json_emits_the_receipt_and_nothing_else_at_all(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--json", "--quiet"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["verb"] == "tasks"
    assert captured.err == ""


# ------------------------------------------------------------------- failure paths


def test_a_missing_token_names_the_export_line_as_its_fix(
    route: dict[str, Any],
    synced: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del synced, route
    monkeypatch.delenv("CLICKUP_API_TOKEN")

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "auth/missing-credential"
    assert diagnostics[0]["supported_fixes"] == ["export CLICKUP_API_TOKEN=<token>"]


def test_a_rate_limit_carries_retry_after_and_is_not_a_usage_error(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = httpx.Response(
        429, headers={"Retry-After": "30"}, json={"err": "rate limited"}
    )

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "transport/rate-limited"
    assert diagnostics[0]["evidence"]["retry_after"] == 30


def test_a_rejected_token_is_an_auth_finding_not_a_transport_one(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = httpx.Response(401, json={"err": "Token invalid"})

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "auth/rejected"


def test_a_server_error_is_reported_as_itself_rather_than_as_a_rate_limit(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = httpx.Response(500, json={"err": "boom"})

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "transport/bad-response"
    assert diagnostics[0]["evidence"]["status"] == 500


def test_an_unwalked_registry_blocks_every_verb_but_sync(
    route: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del route
    path = tmp_path / "never.yaml"
    path.write_text(unsynced_yaml(), encoding="utf-8")
    point_registry(monkeypatch, path)

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "registry/unsynced"


def test_a_hand_edited_registry_is_stale_rather_than_silently_trusted(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del route
    synced.write_text(
        synced.read_text(encoding="utf-8").replace("Project Gamma", "INT - Renamed"),
        encoding="utf-8",
    )

    assert main(["tasks", "--json"]) == 1

    diagnostics = _receipt(capsys)["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["code"] == "registry/stale"


# ------------------------------------------------------------------------ no-cache


def test_no_cache_resolves_against_a_live_walk_instead_of_the_derived_store(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route.update(walk_routes())
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--list", "Project Gamma", "--no-cache", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    # Seven calls for the walk and one for tasks; unused field fetch is removed.
    assert budget["api_calls_made"] == 8


def test_the_default_path_answers_field_definitions_without_a_call(
    route: dict[str, Any], synced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del synced
    route[TASKS] = fixture("list_tasks")

    assert main(["tasks", "--json"]) == 0

    budget = _receipt(capsys)["budget"]
    assert isinstance(budget, dict)
    assert budget["api_calls_made"] == 1


@pytest.mark.parametrize(
    "arguments",
    (
        ["sync"],
        ["tasks"],
        ["task", "synaa0001"],
        ["sprint", "70000009999"],
        ["search", "Synthetic"],
        ["touch", "synaa0001", "--status", "in review", "--dry-run"],
        ["comment", "synaa0001", "--text", "Synthetic", "--dry-run"],
    ),
)
def test_every_cli_verb_requires_explicit_registry_path(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from rexdeck.clickup.engine.commands import main as cli_main

    with pytest.raises(SystemExit) as caught:
        cli_main(arguments)
    assert caught.value.code == 2
    assert "--registry-path" in capsys.readouterr().err


@pytest.mark.parametrize("json_output", (False, True))
def test_task_and_status_labels_cannot_inject_terminal_or_unicode_controls(
    route: dict[str, Any],
    synced: Path,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    del synced
    payload = fixture("list_tasks")
    controls = "\x1b[31m\u202e\u2066\u200b\ufeff"
    payload["tasks"][0]["name"] = f"Visible{controls} task"
    payload["tasks"][0]["status"]["status"] = f"Visible{controls} status"
    route[TASKS] = payload
    arguments = ["tasks", "--json"] if json_output else ["tasks"]
    assert main(arguments) == 0
    captured = capsys.readouterr()
    out = captured.out + captured.err
    if json_output:
        assert isinstance(json.loads(captured.out), dict)
    assert "Visible" in out
    assert "task" in out
    assert "status" in out
    for control in ("\x1b", "\u202e", "\u2066", "\u200b", "\ufeff"):
        assert control not in out + captured.err
    assert "\\u001b" not in out

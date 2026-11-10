"""Recorded Actions responses, read-only argv and recoverable Textual states."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import pytest
from textual.widgets import DataTable, Input, RichLog, Static

from rexdeck.actions import api
from rexdeck.actions.__main__ import main
from rexdeck.actions.api import (
    ActionsError,
    Client,
    Context,
    Job,
    Run,
    RunFilter,
    RunsPage,
    log_tail,
    parse_jobs,
    parse_runs,
)
from rexdeck.actions.app import ActionsApp
from rexdeck.catalogue.actions import INTEGRATION

RUNS = """{"total_count": 31, "workflow_runs": [{"id": 8421,
"name": "CI [not markup]", "status": "completed", "conclusion": "success",
"head_branch": "main", "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
"html_url": "https://github.com/owner/project/actions/runs/8421"}]}"""
JOBS = """{"total_count": 2, "jobs": [{"id": 9321, "name": "lint",
"status": "completed", "conclusion": "success"}, {"id": 9322,
"name": "tests", "status": "in_progress", "conclusion": null}]}"""
CTX = Context("github.com", "owner", "project", "/trusted/bin/gh", "reader")
SELECTED = RunFilter("branch", "main")
RUN = parse_runs(json.loads(RUNS), 1).runs[0]


def env() -> dict[str, str]:
    return {
        "GH_HOST": "github.com",
        "GH_REPO": "github.com/owner/project",
        "GH_PATH": "/trusted/bin/gh",
        "REXDECK_GITHUB_ACCOUNT": "reader",
        "GH_PROMPT_DISABLED": "1",
    }


@pytest.mark.parametrize("missing", list(env()))
def test_context_requires_all_launcher_values(missing: str) -> None:
    values = env()
    assert Context.from_env(values) == CTX
    del values[missing]
    with pytest.raises(ActionsError) as caught:
        Context.from_env(values)
    assert caught.value.state == "context"


@pytest.mark.parametrize(
    "key,value",
    [
        ("GH_HOST", "https://evil.example"),
        ("GH_REPO", "other.example/owner/project"),
        ("GH_REPO", "github.com/../project"),
        ("GH_PATH", "gh"),
        ("GH_PROMPT_DISABLED", "0"),
    ],
)
def test_context_refuses_ambiguous_values(key: str, value: str) -> None:
    values = env()
    values[key] = value
    with pytest.raises(ActionsError):
        Context.from_env(values)


def test_direct_entrypoint_refuses_missing_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in env():
        monkeypatch.delenv(key, raising=False)
    assert main() == 1


def test_recorded_parsing_and_search_limit() -> None:
    page = parse_runs(json.loads(RUNS), 1)
    assert page.runs == (RUN,)
    assert page.total == 31 and page.has_next
    assert not parse_runs(json.loads(RUNS), 2).has_next
    data = json.loads(RUNS)
    data["total_count"] = 1200
    assert not parse_runs(data, 34).has_next
    jobs = parse_jobs(json.loads(JOBS))
    assert jobs == (
        Job(9321, "lint", "completed", "success"),
        Job(9322, "tests", "in_progress", ""),
    )
    lines = [f"2026-11-10T00:00:00Z step {i}" for i in range(205)]
    tail = log_tail("\n".join(lines) + "\n")
    assert tail.splitlines() == lines[-200:]
    assert log_tail("\x1b[31msecret\x1b[0m\x00\u202e\n") == "secret"
    assert log_tail("\x1b]8;;https://example.com\x07link\x1b]8;;\x07") == "link"


def runner(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[subprocess.CompletedProcess[str] | Exception],
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout")
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def result(
    stdout: str, stderr: str = "", code: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_api_filter_paging_jobs_and_plain_log_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_jobs = json.loads(JOBS)
    first_jobs["jobs"] = first_jobs["jobs"][:1]
    second_jobs = json.loads(JOBS)
    second_jobs["jobs"] = second_jobs["jobs"][1:]
    calls = runner(
        monkeypatch,
        [
            result(RUNS),
            result(RUNS),
            result(json.dumps(first_jobs)),
            result(json.dumps(second_jobs)),
            result("\n".join(str(i) for i in range(250))),
        ],
    )
    client = Client(CTX)
    client.runs(RunFilter("branch", "feature/a & unsafe?"), 2)
    client.runs(RunFilter("head_sha", "a" * 40))
    assert len(client.jobs(8421)) == 2
    assert len(client.logs(9321).splitlines()) == 200
    assert all(
        call[:4] == [CTX.gh, "api", "--hostname", "github.com"] for call in calls
    )
    assert (
        calls[0][4]
        == "repos/owner/project/actions/runs?branch=feature%2Fa+%26+unsafe%3F&per_page=30&page=2"
    )
    assert (
        calls[1][4]
        == f"repos/owner/project/actions/runs?head_sha={'a' * 40}&per_page=30&page=1"
    )
    assert calls[2][4].endswith("/runs/8421/jobs?per_page=100&page=1")
    assert calls[3][4].endswith("/runs/8421/jobs?per_page=100&page=2")
    assert calls[4][4].endswith("/jobs/9321/logs")
    assert calls[4][5:] == ["--allow-escape-sequences"]
    assert all("--allow-escape-sequences" not in call for call in calls[:4])
    assert not any(
        flag in call
        for call in calls
        for flag in ("--method", "-X", "--input", "-f", "-F")
    )


@pytest.mark.parametrize(
    "stderr,state",
    [
        ("gh: forbidden (HTTP 403)", "permission"),
        ("gh: Not Found (HTTP 404)", "permission"),
        ("gh: Bad credentials (HTTP 401)", "auth"),
        ("To authenticate, run gh auth login", "auth"),
        ("error connecting to api.github.com", "network"),
        ("gh: HTTP 500", "network"),
    ],
)
def test_error_mapping_never_echoes_gh_output(
    monkeypatch: pytest.MonkeyPatch, stderr: str, state: str
) -> None:
    runner(monkeypatch, [result("sensitive-body", stderr + " sensitive-marker", 1)])
    with pytest.raises(ActionsError) as caught:
        Client(CTX).runs(SELECTED)
    assert caught.value.state == state
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize(
    "error", [OSError("private"), subprocess.TimeoutExpired("gh", 45)]
)
def test_transport_errors(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    runner(monkeypatch, [error])
    with pytest.raises(ActionsError) as caught:
        Client(CTX).logs(1)
    assert caught.value.state == "network"


@pytest.mark.parametrize(
    "payload", ["not json", "[]", "{}", '{"workflow_runs": null, "total_count": 1}']
)
def test_malformed_run_response_is_recoverable(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    runner(monkeypatch, [result(payload)])
    with pytest.raises(ActionsError) as caught:
        Client(CTX).runs(SELECTED)
    assert caught.value.state == "error"


def test_current_worktree_filter_and_detached_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = runner(
        monkeypatch,
        [result("feature/current\n"), result("\n"), result("a" * 40 + "\n")],
    )
    assert api.current_filter() == RunFilter("branch", "feature/current")
    assert api.current_filter() == RunFilter("head_sha", "a" * 40)
    assert calls == [
        ["git", "branch", "--show-current"],
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "HEAD"],
    ]


def test_browser_only_uses_validated_exact_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = runner(monkeypatch, [result("")])
    Client(CTX).open_run(RUN)
    assert calls == [["/usr/bin/open", RUN.url]]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/project/actions/runs/8421",
        "https://github.com.evil/owner/project/actions/runs/8421",
        "https://user@github.com/owner/project/actions/runs/8421",
        "https://github.com/other/project/actions/runs/8421",
        "https://github.com/owner/project/actions/runs/999",
        "https://github.com/owner/project/actions/runs/8421?x=1",
        "https://github.com/owner/project/actions/runs/8421#fragment",
        "https://github.com/owner/project/action\u017f/runs/8421",
    ],
)
def test_browser_refuses_untrusted_links(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    calls = runner(monkeypatch, [])
    run = Run(RUN.id, RUN.name, RUN.status, RUN.conclusion, RUN.branch, RUN.sha, url)
    with pytest.raises(ActionsError):
        Client(CTX).open_run(run)
    assert not calls


class FakeClient(Client):
    def __init__(self) -> None:
        super().__init__(CTX)
        self.failure: str | None = None
        self.empty = False
        self.calls: list[tuple[object, ...]] = []
        self.opened: list[int] = []
        self.account_checks = 0

    def _check(self) -> None:
        if self.failure:
            raise ActionsError(self.failure, "Recoverable recorded error")

    def verify_account(self) -> None:
        self.account_checks += 1

    def runs(self, selected: RunFilter, page: int = 1) -> RunsPage:
        self.calls.append(("runs", selected, page))
        self._check()
        return RunsPage(() if self.empty else (RUN,), page, 31, page == 1)

    def jobs(self, run_id: int) -> tuple[Job, ...]:
        self.calls.append(("jobs", run_id))
        self._check()
        return () if self.empty else parse_jobs(json.loads(JOBS))

    def logs(self, job_id: int) -> str:
        self.calls.append(("logs", job_id))
        self._check()
        return "" if self.empty else "recorded log [not markup]"

    def open_run(self, run: Run) -> None:
        self.opened.append(run.id)


def test_pilot_navigation_filters_logs_browser_and_quit() -> None:
    async def exercise() -> None:
        client = FakeClient()
        app = ActionsApp(client, SELECTED)
        async with app.run_test(size=(110, 32)) as pilot:
            await app.workers.wait_for_complete()
            assert app.state == "ready" and client.opened == []
            assert app.query_one(DataTable).row_count == 1
            await pilot.press("o")
            await app.workers.wait_for_complete()
            assert client.opened == [8421]
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert str(app.view) == "jobs" and app.query_one(DataTable).row_count == 2
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert str(app.view) == "logs"
            assert app.query_one(RichLog).lines
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert str(app.view) == "jobs"
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.press("n")
            await app.workers.wait_for_complete()
            assert app.page == 2
            await pilot.press("p")
            await app.workers.wait_for_complete()
            assert app.page == 1
            await pilot.press("s")
            assert app.query_one("#branch", Input).value == ""
            app.query_one("#sha", Input).value = "b" * 40
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert app.selected == RunFilter("head_sha", "b" * 40)
            await pilot.press("b")
            app.query_one("#branch", Input).value = "release/next"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert client.calls[-1] == ("runs", RunFilter("branch", "release/next"), 1)
            await pilot.press("q")

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["permission", "auth", "network", "error"])
def test_pilot_error_retry_stale_and_empty(failure: str) -> None:
    async def exercise() -> None:
        client = FakeClient()
        client.failure = failure
        app = ActionsApp(client, SELECTED)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert app.state == failure and app.query_one(DataTable).row_count == 0
            client.failure = None
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            before = len(client.calls)
            await pilot.pause()
            assert len(client.calls) == before
            client.failure = failure
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "stale" and app.query_one(DataTable).row_count == 1
            assert "updated" in str(app.query_one("#status", Static).render())
            await pilot.press("o")
            assert client.opened == []
            client.failure = None
            client.empty = True
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "empty" and app.query_one(DataTable).row_count == 0
            client.empty = False
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"

    asyncio.run(exercise())


def test_pilot_loading_and_refresh_failure_preserve_same_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        client = FakeClient()
        gate = asyncio.Event()
        original_thread = asyncio.to_thread

        async def delayed(func: Any, *args: Any, **kwargs: Any) -> Any:
            await gate.wait()
            return await original_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", delayed)
        app = ActionsApp(client, SELECTED)
        async with app.run_test() as pilot:
            assert app.state == "loading"
            assert client.calls == []
            gate.set()
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            client.failure = "permission"
            await pilot.press("b")
            app.query_one("#branch", Input).value = "different"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert app.state == "permission" and app.query_one(DataTable).row_count == 0

    asyncio.run(exercise())


def test_descriptor_is_owned_and_read_only() -> None:
    assert INTEGRATION.context == "github-worktree"
    assert INTEGRATION.owned_module == "rexdeck.actions"
    assert INTEGRATION.args == ("-I", "-m", "rexdeck.actions")
    assert {name for name, _ in INTEGRATION.prerequisites} == {"git", "gh"}


@pytest.mark.parametrize("depth", [1, 2])
def test_pilot_jobs_and_logs_recover_without_restart(depth: int) -> None:
    async def exercise() -> None:
        client = FakeClient()
        app = ActionsApp(client, SELECTED)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            for _ in range(depth):
                await pilot.press("enter")
                await app.workers.wait_for_complete()
            key = app._key()
            saved = app.cache[key].data
            client.failure = "permission"
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "stale" and app.cache[key].data == saved
            client.failure = None
            client.empty = True
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "empty"
            client.empty = False
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"

    asyncio.run(exercise())


def test_pilot_invalid_filter_does_not_issue_requests() -> None:
    async def exercise() -> None:
        client = FakeClient()
        app = ActionsApp(client, SELECTED)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            before = len(client.calls)
            app.query_one("#sha", Input).value = "bad-sha"
            app.query_one("#sha", Input).focus()
            await pilot.press("enter")
            assert len(client.calls) == before and app.selected == SELECTED
            app.query_one("#branch", Input).value = ""
            await pilot.press("enter")
            assert len(client.calls) == before and app.selected == SELECTED

    asyncio.run(exercise())


def test_browser_accepts_canonical_repository_casing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = runner(monkeypatch, [result("")])
    url = "https://github.com/Owner/Project/actions/runs/8421"
    canonical = Run(
        RUN.id, RUN.name, RUN.status, RUN.conclusion, RUN.branch, RUN.sha, url
    )
    Client(CTX).open_run(canonical)
    assert calls == [["/usr/bin/open", url]]


def test_old_gh_log_flag_reports_required_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner(monkeypatch, [result("", "unknown flag: --allow-escape-sequences", 1)])
    with pytest.raises(ActionsError) as caught:
        Client(CTX).logs(9321)
    assert caught.value.state == "error"
    assert "gh" in str(caught.value) and "2.97" in str(caught.value)


@pytest.mark.parametrize(
    "stderr,state",
    [
        ("gh: API rate limit exceeded (HTTP 403)", "rate-limit"),
        ("gh: secondary rate limit (HTTP 403)", "rate-limit"),
        ("gh: Too Many Requests (HTTP 429)", "rate-limit"),
        ("gh: Gone (HTTP 410)", "unavailable"),
    ],
)
def test_rate_limit_and_expired_log_errors(
    monkeypatch: pytest.MonkeyPatch, stderr: str, state: str
) -> None:
    runner(monkeypatch, [result("", stderr, 1)])
    with pytest.raises(ActionsError) as caught:
        Client(CTX).logs(9321)
    assert caught.value.state == state


@pytest.mark.parametrize("login", ["reader\n", "READER\n"])
def test_account_verification_preserves_case_insensitive_login(
    monkeypatch: pytest.MonkeyPatch, login: str
) -> None:
    calls = runner(monkeypatch, [result(login)])
    Client(CTX).verify_account()
    assert calls == [[CTX.gh, "api", "--hostname", CTX.host, "user", "--jq", ".login"]]


def test_pilot_refresh_refuses_account_switch_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = runner(
        monkeypatch,
        [
            result("reader\n"),
            result(RUNS),
            result("different\n"),
            result("reader\n"),
            result(RUNS),
        ],
    )

    async def exercise() -> None:
        app = ActionsApp(Client(CTX), SELECTED)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "stale" and "auth" in app.notice
            assert len(calls) == 3 and calls[-1][4] == "user"
            assert app.query_one(DataTable).row_count == 1
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready" and len(calls) == 5

    asyncio.run(exercise())


def test_pilot_disables_command_palette_and_screenshots() -> None:
    async def exercise() -> None:
        app = ActionsApp(FakeClient(), SELECTED)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert app.ENABLE_COMMAND_PALETTE is False
            screen = app.screen
            await pilot.press("ctrl+p")
            assert app.screen is screen

    asyncio.run(exercise())

"""Personal payloads are synthetic; GET boundaries and browser identity are real."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from textual.widgets import DataTable, Static

from rexdeck.inbox import __main__ as entrypoint
from rexdeck.inbox.api import (
    Client,
    Context,
    InboxError,
    Page,
    clean,
    filter_reason,
    parse_page,
)
from rexdeck.inbox.app import InboxApp
from rexdeck.integrations import get

FIXTURES = Path(__file__).parent / "fixtures" / "inbox"
NOTIFICATIONS = (FIXTURES / "notifications.json").read_text()
ITEMS = parse_page(json.loads(NOTIFICATIONS), 1).items
CTX = Context("github.com", "reader", "/qualified/gh")


def runner(
    monkeypatch: pytest.MonkeyPatch, results: list[subprocess.CompletedProcess[str]]
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["timeout"] == 45
        assert argv[:6] == [
            "/qualified/gh",
            "api",
            "--method",
            "GET",
            "--hostname",
            "github.com",
        ]
        return results.pop(0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def response(
    body: str = "", error: str = "", code: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, body, error)


def test_descriptor_is_owned_independent_and_read_only() -> None:
    spec = get("inbox")
    assert spec.context == "github-account" and spec.owned_module == "rexdeck.inbox"
    assert spec.args == ("-I", "-m", "rexdeck.inbox")
    assert spec.prerequisites == (("gh", "https://cli.github.com/"),)


def test_context_requires_verified_account_and_stripped_env() -> None:
    env = {
        "GH_HOST": "github.com",
        "REXDECK_GITHUB_ACCOUNT": "reader",
        "GH_PATH": "/qualified/gh",
        "GH_PROMPT_DISABLED": "1",
    }
    assert Context.from_env(env) == CTX
    for field in env:
        with pytest.raises(InboxError):
            Context.from_env({k: v for k, v in env.items() if k != field})
    for field in ("GH_REPO", "GH_FORCE_TTY", "CLICOLOR_FORCE"):
        with pytest.raises(InboxError):
            Context.from_env({**env, field: "untrusted"})


def test_pagination_unread_participating_and_verify_each_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = json.dumps([json.loads(NOTIFICATIONS)[0]] * 50)
    calls = runner(
        monkeypatch,
        [response("reader\n"), response(full), response("reader\n"), response("[]")],
    )
    client = Client(CTX)
    first = client.notifications()
    second = client.notifications(2, True)
    assert first.number == 1 and first.has_next and len(first.items) == 50
    assert second.number == 2 and not second.has_next and not second.items
    assert calls[0][-3:] == ["user", "--jq", ".login"]
    assert (
        calls[1][-1] == "notifications?all=false&participating=false&per_page=50&page=1"
    )
    assert calls[2][-3:] == ["user", "--jq", ".login"]
    assert (
        calls[3][-1] == "notifications?all=false&participating=true&per_page=50&page=2"
    )


def test_mismatching_login_never_reads_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = runner(monkeypatch, [response("different\n")])
    with pytest.raises(InboxError, match="account changed"):
        Client(CTX).notifications()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    ["{}", "null", '[{"id":"1"}]', "[null]", '[{"id":1,"subject":{},"repository":{}}]'],
)
def test_invalid_response_is_visible(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    runner(monkeypatch, [response("reader"), response(body)])
    with pytest.raises(InboxError) as failure:
        Client(CTX).notifications()
    assert failure.value.state == "error"


def test_reason_filters_keep_unknown_reasons_in_others() -> None:
    for reason in ("review_requested", "mention", "ci_activity"):
        selected = filter_reason(ITEMS, reason)
        assert len(selected) == 1 and selected[0].reason == reason
    assert filter_reason(ITEMS, "others") == (ITEMS[3],)
    assert filter_reason(ITEMS, "all") == ITEMS
    with pytest.raises(ValueError):
        filter_reason(ITEMS, "invented")


def test_subject_urls_and_suite_resolution_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(CTX)
    assert client.thread_url(ITEMS[0]) == "https://github.com/fixture/project/pull/42"
    assert client.thread_url(ITEMS[1]) == "https://github.com/fixture/project/issues/7"
    assert (
        client.thread_url(ITEMS[3])
        == "https://github.com/fixture/project/commit/" + "a" * 40
    )
    calls = runner(
        monkeypatch,
        [response("reader"), response((FIXTURES / "suite-runs.json").read_text())],
    )
    assert (
        client.thread_url(
            replace(
                ITEMS[2],
                subject_url="https://api.github.com/repos/fixture/project/check-suites/99",
            )
        )
        == "https://github.com/fixture/project/actions/runs/123"
    )
    assert (
        calls[-1][-1]
        == "repos/fixture/project/actions/runs?check_suite_id=99&per_page=100&page=1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/repos/fixture/project/pulls/42",
        "https://api.github.com.evil/repos/fixture/project/pulls/42",
        "https://user@api.github.com/repos/fixture/project/pulls/42",
        "https://api.github.com:443/repos/fixture/project/pulls/42",
        "https://api.github.com/repos/other/project/pulls/42",
        "https://api.github.com/repos/fixture/project/pulls/42?x=1",
        "https://api.github.com/repos/fixture/project/pulls/42#x",
        "https://api.github.com/repos/fixture/project/pulls/42\n",
        "https://api.github.com/repos/fixture/project/pullſ/42",
        "https://api.github.com/repos/fixture/project/pulls/0",
        "https://api.github.com/repos/fixture/project/pulls/042",
        "https://api.github.com/repos/fixture/project/issues/42",
        "https://api.github.com/repos/fixture/project/pulls/%34%32",
        "https://[invalid",
    ],
)
def test_url_refusals_never_make_requests(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    calls = runner(monkeypatch, [])
    with pytest.raises(InboxError):
        Client(CTX).thread_url(replace(ITEMS[0], subject_url=url))
    assert not calls


@pytest.mark.parametrize(
    "changes",
    [
        {"check_suite_id": 98},
        {"id": False},
        {"html_url": "https://github.com/other/project/actions/runs/123"},
        {"html_url": "https://github.com/fixture/project/actions/runs/999"},
        {"html_url": "https://github.com/fixture/project/actions/runs/123?x=1"},
    ],
)
def test_suite_cannot_guess_or_redirect_run(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    data = json.loads((FIXTURES / "suite-runs.json").read_text())
    data["workflow_runs"][0].update(changes)
    runner(monkeypatch, [response("reader"), response(json.dumps(data))])
    with pytest.raises(InboxError, match="Cannot map"):
        Client(CTX).thread_url(
            replace(
                ITEMS[2],
                subject_url="https://api.github.com/repos/fixture/project/check-suites/99",
            )
        )


@pytest.mark.parametrize(
    ("message", "state"),
    [
        ("HTTP 401 token SECRET", "permission"),
        ("HTTP 403 forbidden SECRET", "permission"),
        ("HTTP 404 SECRET", "permission"),
        ("HTTP 429 SECRET", "rate-limit"),
        ("HTTP 403 secondary rate limit SECRET", "rate-limit"),
        ("network unreachable SECRET", "network"),
    ],
)
def test_errors_are_recoverable_and_never_echo_payloads(
    monkeypatch: pytest.MonkeyPatch, message: str, state: str
) -> None:
    runner(monkeypatch, [response(error=message, code=1)])
    with pytest.raises(InboxError) as failure:
        Client(CTX).notifications()
    assert failure.value.state == state
    assert "SECRET" not in str(failure.value)
    if state == "permission":
        assert "existing auth lacks access" in str(failure.value)


def test_timeout_maps_to_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("gh", 45))
    )
    with pytest.raises(InboxError) as failure:
        Client(CTX).notifications()
    assert failure.value.state == "network"


def test_text_sanitization_keeps_markup_literal() -> None:
    assert (
        clean("\x1b[31m[red]literal[/red]\x1b[0m\x00\u202e\n") == "[red]literal[/red]"
    )


class FixtureClient(Client):
    def __init__(self) -> None:
        super().__init__(CTX)
        self.failure: str | None = None
        self.empty = False
        self.calls: list[tuple[int, bool]] = []

    def notifications(self, number: int = 1, participating: bool = False) -> Page:
        self.calls.append((number, participating))
        if self.failure:
            raise InboxError(self.failure, "Synthetic recoverable refusal")
        return Page(() if self.empty else ITEMS, number, number == 1)


def test_pilot_refresh_filters_pagination_browser_narrow_and_quit() -> None:
    async def exercise() -> None:
        client = FixtureClient()
        app = InboxApp(client)
        opened: list[str] = []

        def record_url(url: str, *, new_tab: bool = True) -> None:
            opened.append(url)

        app.open_url = record_url  # type: ignore[method-assign]
        async with app.run_test(size=(100, 30)) as pilot:
            await app.workers.wait_for_complete()
            assert app.state == "ready" and app.query_one(DataTable).row_count == 4
            assert not opened
            await pilot.press("o")
            await app.workers.wait_for_complete()
            assert opened == ["https://github.com/fixture/project/pull/42"]
            await pilot.press("f")
            assert (
                app.reason == "review_requested"
                and app.query_one(DataTable).row_count == 1
            )
            await pilot.press("n")
            await app.workers.wait_for_complete()
            assert app.page == 2 and client.calls[-1] == (2, False)
            await pilot.press("p")
            await app.workers.wait_for_complete()
            await pilot.press("t")
            await app.workers.wait_for_complete()
            assert client.calls[-1] == (1, True)
            count = len(client.calls)
            await pilot.pause()
            assert len(client.calls) == count
            await pilot.resize_terminal(40, 24)
            await pilot.pause()
            assert app.query_one("#selected", Static).region.width <= 40
            assert "refreshed" in str(app.query_one("#status", Static).render())
            await pilot.press("q")

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["network", "permission", "rate-limit"])
def test_pilot_failure_recovery_stale_and_empty(failure: str) -> None:
    async def exercise() -> None:
        client = FixtureClient()
        client.failure = failure
        app = InboxApp(client)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert app.state == failure
            client.failure = None
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"
            client.failure = failure
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "stale" and app.query_one(DataTable).row_count == 4
            client.failure = None
            client.empty = True
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "empty" and app.query_one(DataTable).row_count == 0

    asyncio.run(exercise())


def test_oversized_notification_page_is_rejected() -> None:
    with pytest.raises(ValueError, match="Expected notification page"):
        parse_page([json.loads(NOTIFICATIONS)[0]] * 51, 1)


def test_null_suite_url_opens_only_validated_repository_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = runner(monkeypatch, [])
    assert ITEMS[2].subject_url is None
    assert (
        Client(CTX).thread_url(ITEMS[2]) == "https://github.com/fixture/project/actions"
    )
    for repo in (
        "other/project?x=1",
        "fixture/..",
        "fixture/pröject",
        "fixture/project\n",
    ):
        with pytest.raises(InboxError):
            Client(CTX).thread_url(replace(ITEMS[2], repo=repo))
    with pytest.raises(InboxError):
        Client(CTX).thread_url(replace(ITEMS[0], subject_url=None))
    assert not calls


def test_repeated_refresh_keys_do_not_pile_up_gh_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "user" in argv:
            return response("reader")
        entered.set()
        assert release.wait(10), "test did not release notification fetch"
        return response(NOTIFICATIONS)

    monkeypatch.setattr(subprocess, "run", run)

    async def exercise() -> None:
        app = InboxApp(Client(CTX))
        app.page = 2
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(entered.wait, 5)
                await pilot.press(*(["r"] * 20), "t", "p", "n")
                assert len(calls) == 2
                assert app.page == 2 and not app.participating
                assert app.state == "loading"
                release.set()
                await app.workers.wait_for_complete()
                await pilot.press("r")
                await app.workers.wait_for_complete()
                assert len(calls) == 4
                assert all(argv[2:4] == ["--method", "GET"] for argv in calls)
        finally:
            release.set()

    asyncio.run(exercise())


def test_null_suite_open_notice_never_claims_exact_run() -> None:
    async def exercise() -> None:
        app = InboxApp(FixtureClient())
        opened: list[str] = []

        def record_url(url: str, *, new_tab: bool = True) -> None:
            opened.append(url)

        app.open_url = record_url  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one(DataTable).move_cursor(row=2)
            await pilot.press("o")
            await app.workers.wait_for_complete()
            assert opened == ["https://github.com/fixture/project/actions"]
            assert "repository's Actions page" in app.notice
            assert "no exact run URL" in app.notice

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["fetch", "open"])
def test_unexpected_errors_are_generic_recoverable_and_hide_personal_locals(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    async def exercise() -> None:
        client = FixtureClient()
        app = InboxApp(client)
        if operation == "fetch":
            monkeypatch.setattr(
                client,
                "notifications",
                Mock(side_effect=RuntimeError("PERSONAL_SECRET")),
            )
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            if operation == "open":
                monkeypatch.setattr(
                    client,
                    "thread_url",
                    Mock(side_effect=RuntimeError("PERSONAL_SECRET")),
                )
                await pilot.press("o")
                await app.workers.wait_for_complete()
            assert app.state == "error"
            assert "PERSONAL_SECRET" not in app.notice
            assert "Unexpected Inbox error" in app.notice
            monkeypatch.setattr(
                client, "notifications", lambda *args: Page(ITEMS, 1, False)
            )
            await pilot.press("r")
            await app.workers.wait_for_complete()
            assert app.state == "ready"

    asyncio.run(exercise())


def test_module_entrypoint_preserves_textual_failure_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Context, "from_env", lambda: CTX)
    fake_app = Mock()
    fake_app.return_code = 7
    monkeypatch.setattr(entrypoint, "InboxApp", lambda client: fake_app)
    assert entrypoint.main() == 7

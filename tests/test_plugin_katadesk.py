"""Kata Desk against JSON recorded from kata 0.23.0, with kata replaced by a fake runner."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from rexdeck import integrations
from rexdeck.katadesk.app import KataDesk
from rexdeck.katadesk.kata import Kata, KataError, event_kind, tree

FIXTURES = Path(__file__).parent / "fixtures" / "katadesk"
KATA = "/opt/homebrew/bin/kata"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


PARENT = json.loads(fixture("show.json"))["issue"]
PARENT_REF = f"desk#{PARENT['short_id']}"

READS: dict[tuple[str, ...], tuple[int, str]] = {
    ("daemon", "locate"): (0, fixture("locate.json")),
    ("whoami",): (0, fixture("whoami.json")),
    ("list", "--status", "open", "--limit", "0"): (0, fixture("list.json")),
    ("show", PARENT_REF): (0, fixture("show.json")),
    ("inbox", "--all", "--for", "lab"): (0, fixture("inbox.json")),
}


class Fake:
    """Answers kata argv from recorded output and keeps every call it received."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, argv: Sequence[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        assert argv[0] == KATA and argv[-1] == "--json"
        key = tuple(argv[1:-1])
        self.calls.append(key)
        code, text = self.answers.get(key, (0, fixture("show.json")))
        # Like kata: results on stdout, the JSON error envelope on stderr.
        stdout, stderr = (text, "") if code == 0 else ("", text)
        return subprocess.CompletedProcess(list(argv), code, stdout, stderr)


def kata(
    answers: dict[tuple[str, ...], tuple[int, str]] | None = None,
) -> tuple[Kata, Fake]:
    fake = Fake(dict(READS if answers is None else answers))
    return Kata(KATA, Path("/work"), runner=fake), fake


@pytest.fixture(autouse=True)
def _no_inbox_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KATA_INBOX_USER", raising=False)


def test_listing_nests_children_under_their_parents() -> None:
    client, _ = kata()
    listing = client.issues("open")
    rows = [(depth, issue.title) for depth, issue in tree(listing.issues)]
    assert rows == [(0, "Parent issue"), (1, "Child issue"), (0, "Blocker issue")]
    parent = next(issue for issue in listing.issues if issue.title == "Parent issue")
    assert (parent.priority, parent.blocked, parent.project) == (1, True, "desk")
    assert listing.truncated is False


def test_detail_reads_body_comments_links_and_children() -> None:
    client, _ = kata()
    detail = client.detail(PARENT_REF)
    assert detail.body == "# Heading\n\nParent **body**"
    assert [comment.body for comment in detail.comments] == ["First comment"]
    assert sorted(link.split()[0] for link in detail.links) == ["blocked", "child"]
    assert [child.title for child in detail.children] == ["Child issue"]


def test_attention_reads_the_named_recipient_and_kata_has_no_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake = kata()
    assert client.recipient() == "lab"
    requests = client.inbox(client.recipient())
    assert [(request.ref, request.message) for request in requests] == [
        (PARENT_REF, "Please decide on the parent")
    ]
    monkeypatch.setenv("KATA_INBOX_USER", " reviewer ")
    fake.calls.clear()
    assert client.recipient() == "reviewer"
    assert fake.calls == []


def test_stopped_daemon_is_reported_and_nothing_else_runs() -> None:
    client, fake = kata(
        {("daemon", "locate"): (7, fixture("locate-stopped.stderr.json"))}
    )
    with pytest.raises(KataError) as failure:
        KataDesk(client)._read("open")
    assert failure.value.state == "stopped"
    assert "kata daemon start" in str(failure.value)
    assert fake.calls == [("daemon", "locate")]


def test_unbound_directory_still_reads_attention() -> None:
    answers = dict(READS)
    answers[("list", "--status", "open", "--limit", "0")] = (
        4,
        fixture("list-unbound.stderr.json"),
    )
    client, _ = kata(answers)
    snapshot = KataDesk(client)._read("open")
    assert snapshot.listing is None
    assert snapshot.unbound is not None and ".kata.toml" in snapshot.unbound
    assert len(snapshot.requests) == 1


@pytest.mark.parametrize(
    ("code", "stdout", "state"),
    [
        (
            0,
            fixture("locate.json").replace(
                '"kata_api_version":1', '"kata_api_version":2'
            ),
            "version",
        ),
        (2, "", "failed"),
        (0, "[]", "failed"),
    ],
)
def test_unexpected_kata_output_is_refused(code: int, stdout: str, state: str) -> None:
    client, _ = kata({("daemon", "locate"): (code, stdout)})
    with pytest.raises(KataError) as failure:
        client.locate()
    assert failure.value.state == state


def test_event_lines_are_classified_by_shape() -> None:
    lines = fixture("events.jsonl").splitlines()
    assert [event_kind(line) for line in lines] == ["event"] * len(lines)
    assert event_kind('{"reset_required":true,"reset_after_id":9}') == "reset"
    for noise in (
        "",
        "not json",
        "[]",
        '{"event_id":true,"type":"x"}',
        '{"event_id":1}',
    ):
        assert event_kind(noise) is None


def test_catalogue_runs_kata_from_its_enable_bound_prerequisite() -> None:
    desk = integrations.get("katadesk")
    assert desk.owned_module == "rexdeck.katadesk"
    assert desk.context == "directory"
    assert [name for name, _ in desk.prerequisites] == ["kata"]


class LiveKata(Kata):
    """A recorded daemon whose event stream is a real child process."""

    def __init__(self, fake: Fake) -> None:
        super().__init__(KATA, Path.cwd(), runner=fake)

    def events_argv(self) -> list[str]:
        line = json.dumps({"event_id": 1, "type": "issue.created"})
        script = f"import time; print({line!r}, flush=True); time.sleep(60)"
        return [sys.executable, "-c", script]


def test_live_event_reloads_attention_opens_issue_and_quit_stops_the_stream() -> None:
    fake = Fake(dict(READS))
    app = KataDesk(LiveKata(fake))

    async def scenario() -> asyncio.subprocess.Process:
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(50):
                await pilot.pause(0.1)
                if fake.calls.count(("daemon", "locate")) >= 2 and app._events:
                    break
            # The stream's event caused a second full read after the first one.
            assert fake.calls.count(("daemon", "locate")) >= 2
            process = app._events
            assert process is not None and process.returncode is None
            await pilot.press("2")
            await pilot.pause(0.3)
            assert app.mode == "attention"
            assert ("show", PARENT_REF) in fake.calls
        return process

    process = asyncio.run(scenario())
    assert process.returncode is not None


def test_after_a_stopped_answer_the_desk_runs_nothing_that_could_start_kata() -> None:
    fake = Fake(dict(READS))
    app = KataDesk(Kata(KATA, Path.cwd(), runner=fake))
    app._tailing = True  # no live stream in this scenario

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(30):
                await pilot.pause(0.1)
                if ("show", PARENT_REF) in fake.calls:
                    break
            fake.answers[("daemon", "locate")] = (
                7,
                fixture("locate-stopped.stderr.json"),
            )
            before = len(fake.calls)
            await pilot.press("r")
            await pilot.pause(0.5)
            await pilot.press("down")
            await pilot.pause(0.5)
            assert app.state == "stopped"
            # Only the daemon probe ran: no list, inbox or show could auto-start kata.
            assert fake.calls[before:] == [("daemon", "locate")]

    asyncio.run(scenario())

"""Readable plugin listing edge cases the CLI test does not reach."""

from __future__ import annotations

from rexdeck import report

SERVER = "unix:///Users/me/Library/Application%20Support/rex/server.sock"


def _row(state: str, error: str | None = None) -> dict[str, object]:
    row: dict[str, object] = {"state": state, "server": SERVER}
    if error is not None:
        row["error"] = error
    return row


def test_a_shared_cause_is_listed_once_for_every_affected_plugin() -> None:
    shared = "Rex was not found on PATH"
    text = report.render(
        "RexDeck plugins",
        {
            "state": "fault",
            "plugins": {
                "files": _row("fault", shared),
                "git": _row("fault", shared),
                "viewer": _row("fault", "Viewer receipt is malformed"),
            },
        },
        width=100,
    )

    problems = text.split("Needs attention:", 1)[1]
    assert problems.count(shared) == 1
    assert "  files, git:" in problems
    assert "  viewer:" in problems
    assert (
        "Rex server: unix:///Users/me/Library/Application Support/rex/server.sock"
        in text
    )


def test_remote_or_receipt_text_cannot_inject_terminal_controls() -> None:
    text = report.render(
        "RexDeck status",
        {
            "state": "fault",
            "plugins": {"files": _row("fault", "bad\x1b]0;owned\x07 receipt\nnext")},
        },
        width=100,
    )

    assert "\x1b" not in text and "\x07" not in text
    assert "bad]0;owned receipt next" in text


def test_long_paths_in_a_fix_stay_on_one_line() -> None:
    path = "/Users/me/Library/Application-Support/rexdeck/plugins/files/very-long/setup.json"
    text = report.render(
        "RexDeck status",
        {"state": "fault", "plugins": {"files": _row("fault", f"Cannot read {path}")}},
        width=60,
    )

    assert any(line.strip() == path for line in text.splitlines())


def test_color_is_applied_only_when_requested() -> None:
    document = {"state": "ready", "plugins": {"files": _row("ready")}}

    assert "\x1b[" not in report.render("RexDeck plugins", document, width=100)
    colored = report.render("RexDeck plugins", document, color=True, width=100)
    assert "\x1b[32mready" in colored

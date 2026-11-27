"""Kata Desk: RexDeck's read-only view of a kata project and your attention requests."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="katadesk",
    title="Kata Desk",
    tool="rexdeck-katadesk",
    tool_label="Kata Desk view",
    description="Read the invoking directory's kata issues and your attention requests",
    keywords="kata issues attention requests agents desk",
    context="directory",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "read-only: runs kata daemon locate, whoami, list, show and inbox with --json; "
        "no kata writes",
        "while open, one kata events --tail child in the pane's process group keeps the "
        "list live; it stops when the view quits or the pane closes",
        "never starts, stops or configures kata's daemon: every read locates it first and "
        "a stopped daemon is reported with kata's own message",
        "Attention reads requests addressed to KATA_INBOX_USER, else the actor kata "
        "whoami reports, across every project on the daemon",
        "issue titles, bodies, comments and requests stay in memory; RexDeck stores "
        "nothing",
        "kata may send its default anonymous telemetry; opt out with "
        "KATA_TELEMETRY_ENABLED=0 or DO_NOT_TRACK=1 in the Rex server's environment",
    ),
    default_layout=Layout("right", 50),
    args=("-I", "-m", "rexdeck.katadesk"),
    owned_module="rexdeck.katadesk",
    prerequisites=(("kata", "https://github.com/kenn-io/kata/releases/tag/v0.23.0"),),
    enable_notice=(
        "Kata Desk reads the kata project bound to the invoking directory and needs "
        "kata's daemon running; start it with kata daemon start."
    ),
)

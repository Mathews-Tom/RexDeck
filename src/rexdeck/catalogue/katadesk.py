"""Kata Desk: a kata project's issues, your attention requests and omp agents on them."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="katadesk",
    title="Kata Desk",
    tool="rexdeck-katadesk",
    tool_label="Kata Desk view",
    description="Read the invoking directory's kata issues and run omp agents on them",
    keywords="kata issues attention requests agents omp desk",
    context="directory",
    versions=(),
    version_pattern="",
    release="https://github.com/Mathews-Tom/RexDeck",
    license="MIT",
    capabilities=(
        "the desk itself never writes to kata: it runs kata daemon locate, whoami, "
        "list, show and inbox with --json",
        "starts write-capable omp agents on an open issue (a split, a new window or a "
        "background window) in the invoking directory; agents write to kata as your "
        "actor and run until stopped, also after the desk quits",
        "Stop closes only the chosen agent's block after confirmation; agents are "
        "identified only by the Rex server, session, block and host IDs recorded when "
        "they were placed",
        "records started agents and their prompts under the integration's workspace "
        "(katadesk/state, mode 0600); rexdeck disable katadesk leaves them so running "
        "agents are listed again",
        "while open, one kata events --tail child and one rex events --session child "
        "in the pane's process group keep the list and agent badges live; both stop "
        "when the view quits or the pane closes",
        "never starts, stops or configures kata's daemon: every read locates it first and "
        "a stopped daemon is reported with kata's own message",
        "Attention reads requests addressed to KATA_INBOX_USER, else the actor kata "
        "whoami reports, across every project on the daemon",
        "kata may send its default anonymous telemetry; opt out with "
        "KATA_TELEMETRY_ENABLED=0 or DO_NOT_TRACK=1 in the Rex server's environment",
    ),
    default_layout=Layout("right", 50),
    args=("-I", "-m", "rexdeck.katadesk"),
    owned_module="rexdeck.katadesk",
    prerequisites=(
        ("kata", "https://github.com/kenn-io/kata/releases/tag/v0.23.0"),
        ("omp", "https://omp.sh"),
    ),
    enable_notice=(
        "Kata Desk reads the kata project bound to the invoking directory and needs "
        "kata's daemon running; start it with kata daemon start."
    ),
)

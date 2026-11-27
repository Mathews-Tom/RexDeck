"""Kata: upstream kata tui on the invoking directory's kata project."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="kata",
    title="Kata",
    tool="kata",
    tool_label="kata tui",
    description="Browse and triage the invoking directory's kata issues in kata tui",
    keywords="kata issues tracker tasks agents tui",
    # kata binds a directory to a project through .kata.toml up to the git root.
    context="directory",
    versions=("0.23.0",),
    version_pattern=r"^kata v(\d+\.\d+\.\d+)\s*$",
    release="https://github.com/kenn-io/kata/releases/tag/v0.23.0",
    license="MIT",
    capabilities=(
        "issue writes through kata's daemon (create, edit, comment, label, link, "
        "claim, close, reopen, move, delete) as your kata actor",
        "the Jobs and Workflows tabs create and edit cron jobs, agent-prompt jobs, "
        "workflows and notifications that kata's daemon or worker later runs",
        "Ctrl+e on issue, comment, job and workflow text fields runs $VISUAL, else "
        "$EDITOR, else vi, from the Rex server's environment on a temporary "
        "kata-*.md file in $TMPDIR",
        "kata stores issues in its own database under KATA_HOME (default ~/.kata) "
        "or on a configured remote daemon; RexDeck adds no storage",
        "kata tui and other kata commands auto-start a local daemon under "
        "KATA_HOME on first use (kata 0.23.0 has no setting to turn this off; "
        "KATA_SERVER or a .kata.local.toml remote skips it); that daemon also "
        "serves kata's local web UI on a loopback port",
        "network: kata's daemon sends anonymous PostHog telemetry by default (opt "
        "out with KATA_TELEMETRY_ENABLED=0 or DO_NOT_TRACK=1 in the Rex server's "
        "environment before the daemon starts); the D daemon switcher and F "
        "federation views can contact remote daemons and enroll projects with "
        "catalog credentials",
        "RexDeck passes only the tui subcommand and the invoking directory; quit "
        "asks for confirmation unless kata's [tui] confirm_quit = false",
    ),
    default_layout=Layout("right", 50),
    args=("tui",),
    enable_notice=(
        "Kata shows the project bound to the invoking directory; run kata init "
        "there first. kata starts its local daemon on first use."
    ),
)

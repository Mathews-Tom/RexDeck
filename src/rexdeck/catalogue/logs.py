"""Logs: upstream lnav on an explicitly selected local log or directory."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="logs",
    title="Logs",
    tool="lnav",
    tool_label="lnav",
    description="Explore explicitly selected local logs with lnav filters and SQL",
    keywords="logs lnav filter sql time rotated",
    context="selected-file",
    versions=("0.14.1",),
    version_pattern=r"^lnav (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/tstack/lnav/releases/tag/v0.14.1",
    license="BSD-2-Clause",
    capabilities=(
        "reads and follows the selected log or all files in the selected directory; "
        "logs can contain secrets; directory selection also opens rotated logs",
        "selected paths containing *, ?, [ or \\ are refused before launch "
        "because lnav would expand them as wildcards",
        "writes configuration, history, bookmarks and session state under "
        "~/.config/lnav or legacy ~/.lnav; captures, remote copies and extracted "
        "archives are retained under $TMPDIR/lnav-user-${UID}-work",
        "shell execution and writes remain available through :sh, pipe commands, "
        "| scripts, SQL extensions and export/configuration commands; installed "
        "formats, SQL scripts and configuration are loaded from lnav's own paths",
        "network features include opening URLs, SSH remote logs (copies a tailer "
        "helper to the remote host), and explicitly updating git-installed formats; "
        "RexDeck passes no remote target, stdin capture or execution flags",
        "direct archive suffixes are refused, but lnav detects archives by content "
        "and can extract a disguised archive or an archive inside a selected "
        "directory; this integration is not a sandbox",
    ),
    default_layout=Layout("right", 50),
    file_suffixes=(".log", ".txt", ".out"),
    file_directories=True,
)

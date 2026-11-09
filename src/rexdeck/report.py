"""Human-readable rendering of plugin listings; `--json` keeps the full document."""

from __future__ import annotations

import os
import shutil
import textwrap
import unicodedata
import urllib.parse
from collections.abc import Mapping

_PROBLEMS = {"conflict", "failed", "fault", "missing-dependency"}
_COLORS = {
    "ready": "32",
    "disabled": "2",
    "not-configured": "2",
    "conflict": "33",
    "missing-dependency": "33",
    "fault": "31",
    "failed": "31",
}


def _plain(value: object) -> str:
    """Single-line display text with terminal control characters removed."""
    return "".join(
        " " if character in "\r\n\t" else character
        for character in str(value)
        if character in "\r\n\t" or unicodedata.category(character)[0] != "C"
    )


def _paint(text: str, state: str, color: bool) -> str:
    code = _COLORS.get(state)
    return f"\033[{code}m{text}\033[0m" if color and code else text


def _tool(row: Mapping[str, object]) -> str:
    integration = row.get("integration")
    if not isinstance(integration, Mapping):
        return "-"
    tool = _plain(integration.get("tool", "-"))
    versions = integration.get("versions")
    if isinstance(versions, list) and versions:
        return f"{tool} {'/'.join(_plain(version) for version in versions)}"
    return f"{tool} (built-in)" if integration.get("owned_module") else tool


def _pane(row: Mapping[str, object]) -> str:
    layout = row.get("layout")
    if not isinstance(layout, Mapping):
        return "-"
    return f"{_plain(layout.get('placement', '?'))} {_plain(layout.get('size', '?'))}%"


def _server(rows: Mapping[str, Mapping[str, object]]) -> str | None:
    servers = {str(row["server"]) for row in rows.values() if row.get("server")}
    if len(servers) != 1:
        return None
    (server,) = servers
    return _plain(urllib.parse.unquote(server))


def render(
    title: str,
    result: Mapping[str, object],
    *,
    color: bool = False,
    width: int | None = None,
) -> str:
    """Render a `{state, plugins}` document as a table plus actionable problems."""
    raw = result.get("plugins")
    rows: dict[str, Mapping[str, object]] = (
        {str(name): row for name, row in raw.items() if isinstance(row, Mapping)}
        if isinstance(raw, Mapping)
        else {}
    )
    state = _plain(result.get("state", "unknown"))
    lines = [f"{title}: {_paint(state, state, color)}"]
    server = _server(rows)
    if server:
        lines.append(f"Rex server: {server}")
    lines.append("")

    table = [
        (name, _plain(row.get("state", "unknown")), _tool(row), _pane(row))
        for name, row in rows.items()
    ]
    headers = ("PLUGIN", "STATE", "TOOL", "PANE")
    widths = [
        max(len(headers[index]), *(len(line[index]) for line in table))
        if table
        else len(headers[index])
        for index in range(len(headers))
    ]

    def format_row(cells: tuple[str, str, str, str], row_state: str) -> str:
        padded = [cell.ljust(widths[index]) for index, cell in enumerate(cells)]
        padded[1] = _paint(padded[1], row_state, color)
        return "  ".join(padded).rstrip()

    lines.append(format_row(headers, ""))
    lines.extend(format_row(cells, cells[1]) for cells in table)

    available = width or shutil.get_terminal_size((100, 24)).columns
    wrap = max(40, available - 4)
    # One shared cause (for example Rex missing from PATH) is listed once.
    grouped: dict[str, list[str]] = {}
    for name, row in rows.items():
        if row.get("state") in _PROBLEMS and row.get("error"):
            grouped.setdefault(_plain(row["error"]), []).append(name)
        conflicts = row.get("conflicts")
        if isinstance(conflicts, list) and conflicts:
            message = "conflicts: " + "; ".join(_plain(item) for item in conflicts)
            grouped.setdefault(message, []).append(name)
    problems: list[str] = []
    for message, names in grouped.items():
        problems.append(f"  {', '.join(names)}:")
        problems.extend(
            textwrap.wrap(
                message,
                wrap,
                initial_indent="    ",
                subsequent_indent="    ",
                # Paths and commands in fixes must stay copyable in one piece.
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    if problems:
        lines.extend(["", "Needs attention:", *problems])
    notices: list[str] = []
    for name, row in rows.items():
        if row.get("notice"):
            notices.append(f"  {name}:")
            notices.extend(
                textwrap.wrap(
                    _plain(row["notice"]),
                    wrap,
                    initial_indent="    ",
                    subsequent_indent="    ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    if notices:
        lines.extend(["", "Next steps:", *notices])

    lines.append("")
    if state == "not-configured":
        lines.append("Run `rexdeck setup` to select a Rex server first.")
    else:
        lines.append("Enable or refresh: rexdeck enable <plugin> [<plugin> ...]")
    lines.append("Full details: add --json")
    return "\n".join(lines)


def use_color(stream_is_tty: bool) -> bool:
    """Color only interactive output, honouring the NO_COLOR convention."""
    return (
        stream_is_tty
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    )

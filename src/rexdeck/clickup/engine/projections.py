"""Typed task/comment projections, custom-field resolution and text rendering."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from rexdeck.clickup.text import single_line

from .diagnostics import SEVERITY_ERROR, SEVERITY_WARNING, Diagnostic, EngineError
from .records import CustomField
from .registry import RESYNC as RESYNC

#: A task counts as blocked when its status or any of its tags carries this
#: marker. ClickUp has no blocked flag, and reading dependencies would cost a
#: call per task, so the rule is stated here and documented in `verbs.md`
#: rather than inferred per workspace.
BLOCKED_MARKER = "block"

ABSENT = "-"

#: Where a task payload comes from. The ID is a placeholder because a custom-field
#: blob reaches this module without the task it came off.
TASK_ENDPOINT = "/task/<task-id>"

#: `search` reads one asset type. The MCP tool it stands in for reads six, so
#: the gap is named rather than left for a caller to discover by not finding
#: the doc they were looking for.
SEARCHED_ASSETS: tuple[str, ...] = ("task",)
UNSEARCHED_ASSETS: tuple[str, ...] = (
    "doc",
    "whiteboard",
    "dashboard",
    "attachment",
    "chat",
)


@dataclass(frozen=True, slots=True)
class TaskRow:
    """One line of `tasks`: what a caller scanning a list reads."""

    id: str
    status: str
    name: str
    assignee: str
    due: str


@dataclass(frozen=True, slots=True)
class CommentLine:
    """One comment, flattened in order. `depth` 1 is a threaded reply."""

    id: str
    depth: int
    author: str
    when: str
    text: str


@dataclass(frozen=True, slots=True)
class TaskView:
    """The `task` header block, plus its comment tree already flattened."""

    id: str
    name: str
    status: str
    list_name: str
    assignees: tuple[str, ...]
    due: str
    url: str
    fields: tuple[tuple[str, str], ...]
    comments: tuple[CommentLine, ...]


@dataclass(frozen=True, slots=True)
class SprintView:
    """A list's shape: how many in each status, what is blocked, what is unowned."""

    total: int
    statuses: tuple[tuple[str, int], ...]
    blocked: tuple[TaskRow, ...]
    unassigned: tuple[TaskRow, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    kind: str
    list_name: str
    title: str


# ------------------------------------------------------------------------ projection


def task_row(payload: Mapping[str, object]) -> TaskRow:
    """Project one task down to the five columns `tasks` prints."""
    return TaskRow(
        id=_text(payload, "id"),
        status=_status(payload),
        name=_text(payload, "name"),
        assignee=" ".join(_assignees(payload)) or ABSENT,
        due=epoch_date(payload.get("due_date")),
    )


def task_view(
    payload: Mapping[str, object],
    comments: Sequence[CommentLine],
    fields: Sequence[CustomField],
) -> tuple[TaskView, tuple[Diagnostic, ...]]:
    """Project one task and its comment tree, resolving custom fields to names."""
    resolved, findings = resolve_fields(payload.get("custom_fields"), fields)
    listing = payload.get("list")
    return (
        TaskView(
            id=_text(payload, "id"),
            name=_text(payload, "name"),
            status=_status(payload),
            list_name=_text(listing, "name") if isinstance(listing, dict) else ABSENT,
            assignees=_assignees(payload),
            due=epoch_date(payload.get("due_date")),
            url=str(payload.get("url") or ABSENT),
            fields=resolved,
            comments=tuple(comments),
        ),
        findings,
    )


def comment_line(payload: Mapping[str, object], *, depth: int) -> CommentLine:
    """Project one comment. `comment_text` is the plain-text rendering ClickUp sends."""
    user = payload.get("user")
    return CommentLine(
        id=_text(payload, "id"),
        depth=depth,
        author=_text(user, "username") if isinstance(user, dict) else ABSENT,
        when=epoch_date(payload.get("date")),
        text=str(payload.get("comment_text") or "").strip(),
    )


def reply_count(payload: Mapping[str, object]) -> int:
    """Return how many threaded replies a comment carries.

    `task` fetches a reply thread only where this is non-zero, which is why its
    replaced-call count is computed rather than written down as a constant.
    """
    value = payload.get("reply_count")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 0
    return int(value)


def sprint_view(
    rows: Sequence[TaskRow], payloads: Sequence[Mapping[str, object]]
) -> SprintView:
    """Roll a list up per status, and name what is blocked and what is unowned."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return SprintView(
        total=len(rows),
        statuses=tuple(sorted(counts.items())),
        blocked=tuple(
            row
            for row, payload in zip(rows, payloads, strict=True)
            if _blocked(payload)
        ),
        unassigned=tuple(row for row in rows if row.assignee == ABSENT),
    )


def search_hit(payload: Mapping[str, object]) -> SearchHit:
    """Project one search result.

    `kind` is always `task` here, because tasks are all this verb reads. The
    column keeps the shape the verb surface specifies, and `tasks_only` is what
    stops the narrower reading from being silent.
    """
    listing = payload.get("list")
    return SearchHit(
        id=_text(payload, "id"),
        kind="task",
        list_name=_text(listing, "name") if isinstance(listing, dict) else ABSENT,
        title=_text(payload, "name"),
    )


def tasks_only() -> Diagnostic:
    """Report that a search read tasks and nothing else.

    Every `search` run carries this. The result is correct and the run
    completed, so it is a warning rather than an error — but the verb's name
    invites a caller to read an empty result as "the workspace has none of
    that", and for a doc, whiteboard, dashboard, attachment or chat the honest
    answer is that it was never looked at. A ceiling nobody is told about is
    load-bearing and invisible, which is the same failure `budget/truncated`
    exists to prevent.

    The fix is the MCP tool this engine otherwise displaces: it searches all
    six asset types server-side, and reaching for it is a real move a caller
    can make rather than advice.
    """
    return Diagnostic(
        code="search/tasks-only",
        severity=SEVERITY_WARNING,
        message="only tasks were searched; the workspace's other asset types were not",
        subject="search",
        evidence={
            "searched": list(SEARCHED_ASSETS),
            "not_searched": list(UNSEARCHED_ASSETS),
        },
        supported_fixes=("clickup_search",),
    )


# -------------------------------------------------------------------- custom fields


def resolve_fields(
    values: object, definitions: Sequence[CustomField]
) -> tuple[tuple[tuple[str, str], ...], tuple[Diagnostic, ...]]:
    """Turn a task's UUID-keyed custom-field blobs into named, rendered columns.

    Resolution goes through the cached definitions, never through the name the
    task payload happens to carry alongside the value. The payload's copy is
    whatever the workspace looked like when the task was last written; the
    definitions are what `sync` recorded and what a reviewer saw in the diff.
    """
    if not isinstance(values, list):
        raise EngineError(
            unexpected_shape(
                "custom_fields", expected="list", found=values, where=TASK_ENDPOINT
            )
        )
    by_id = {definition.id: definition for definition in definitions}
    resolved: list[tuple[str, str]] = []
    findings: list[Diagnostic] = []
    seen: set[tuple[str, str]] = set()
    for blob in values:
        # An unset field carries no `value` and is nothing to report. A blob
        # that is not an object at all is a shape violation, and skipping it
        # would be the one place in this engine that swallows one.
        if not isinstance(blob, dict):
            raise EngineError(
                unexpected_shape(
                    "custom_fields", expected="object", found=blob, where=TASK_ENDPOINT
                )
            )
        if "value" not in blob:
            continue
        field_id = _text(blob, "id")
        definition = by_id.get(field_id)
        if definition is None:
            findings.append(_unknown_field(field_id))
            continue
        rendered, finding = render_field_value(definition, blob["value"])
        if finding is not None and (finding.code, finding.subject) not in seen:
            seen.add((finding.code, finding.subject))
            findings.append(finding)
        resolved.append((definition.name, rendered))
    return tuple(resolved), tuple(findings)


def render_field_value(
    definition: CustomField, value: object
) -> tuple[str, Diagnostic | None]:
    """Render one value by its declared type, with whatever it could not resolve.

    Public because `writes.py` renders the same values into a mutation diff. A
    dropdown's raw value is an option UUID or an orderindex, so a write that
    spelled its own diff would be spelling it worse than the read does.

    The finding rides back rather than a flag because there are now two of them
    — an unrendered type, and an option the registry cannot name — and a caller
    that has to reconstruct which from a boolean is one the next case breaks.
    """
    if value is None or value == "":
        return ABSENT, None
    if definition.type == "drop_down":
        named, matched = _option_name(definition, value)
        return named, None if matched else _unknown_option(definition, value)
    if definition.type == "labels":
        chosen = value if isinstance(value, list) else [value]
        resolved = [(one, *_option_name(definition, one)) for one in chosen]
        missed = [one for one, _, matched in resolved if not matched]
        return (
            " | ".join(named for _, named, _ in resolved),
            _unknown_option(definition, missed[0]) if missed else None,
        )
    if definition.type == "users":
        people = value if isinstance(value, list) else [value]
        return " ".join(
            _text(one, "username") if isinstance(one, dict) else str(one)
            for one in people
        ), None
    if definition.type == "checkbox":
        return ("yes" if value in (True, "true") else "no"), None
    if definition.type == "date":
        return epoch_date(value), None
    if definition.type in (
        "number",
        "currency",
        "text",
        "short_text",
        "url",
        "email",
        "phone",
    ):
        return str(value), None
    return json.dumps(value, ensure_ascii=False), _unknown_type(definition)


def _option_name(definition: CustomField, value: object) -> tuple[str, bool]:
    """Name a dropdown or label value, which ClickUp spells as an ID or an orderindex.

    The flag says whether an option was actually matched. An option added since
    the last sync otherwise put a bare UUID in the cell with nothing said about
    it, which is the unreadable output custom-field resolution exists to avoid.
    """
    for option in definition.options:
        if option.id == str(value):
            return option.name, True
    if isinstance(value, int) and not isinstance(value, bool):
        for option in definition.options:
            if option.orderindex == value:
                return option.name, True
    return str(value), False


def _unknown_field(field_id: str) -> Diagnostic:
    return Diagnostic(
        code="projection/unknown-field",
        severity=SEVERITY_WARNING,
        message="a task carries a custom field the registry has no definition for",
        subject=field_id,
        evidence={"field_id": field_id},
        supported_fixes=(RESYNC,),
    )


def _unknown_option(definition: CustomField, value: object) -> Diagnostic:
    """Say a value indexed into no option the registry knows, rather than show a UUID."""
    return Diagnostic(
        code="projection/unknown-option",
        severity=SEVERITY_WARNING,
        message="a custom field holds an option the registry cannot name, shown raw",
        subject=definition.name,
        evidence={"field": definition.name, "option": str(value)},
        supported_fixes=(RESYNC,),
    )


def unexpected_shape(
    subject: str, *, expected: str, found: object, where: str
) -> Diagnostic:
    """Build the finding for a payload ClickUp shaped differently than documented.

    Shared with `runtime.rows`, which reads the same responses. A shape change
    reached the caller as a bare traceback before this — no receipt, and nothing
    at all on stdout under `--json`, which is the one thing `--json` promises.
    """
    return Diagnostic(
        code="response/unexpected-shape",
        severity=SEVERITY_ERROR,
        message=f"ClickUp returned {subject} as something other than {expected}",
        subject=subject,
        evidence={
            "subject": subject,
            "expected": expected,
            "found": type(found).__name__,
            "path": where,
        },
        supported_fixes=(
            'curl -sS -H "Authorization: $CLICKUP_API_TOKEN"'
            f" https://api.clickup.com/api/v2{where}",
        ),
    )


def _unknown_type(definition: CustomField) -> Diagnostic:
    return Diagnostic(
        code="projection/unknown-field-type",
        severity=SEVERITY_WARNING,
        message="a custom field has a type this engine does not render, shown raw",
        subject=definition.name,
        evidence={"field": definition.name, "type": definition.type},
        supported_fixes=(definition.type,),
    )


# --------------------------------------------------------------------------- rendering


def render_tasks(rows: Sequence[TaskRow]) -> str:
    """Render `tasks` as a fixed-column table sized for a model to read."""
    return table(
        ("ID", "STATUS", "NAME", "ASSIGNEE", "DUE"),
        [(row.id, row.status, row.name, row.assignee, row.due) for row in rows],
    )


def render_task(view: TaskView) -> str:
    """Render the `task` header block, then the comment tree flattened in order."""
    header = [
        f"task     {view.id}",
        f"name     {view.name}",
        f"status   {view.status}",
        f"list     {view.list_name}",
        f"assignee {' '.join(view.assignees) or ABSENT}",
        f"due      {view.due}",
        f"url      {view.url}",
    ]
    if view.fields:
        header.append(
            "fields   " + " | ".join(f"{name}={value}" for name, value in view.fields)
        )
    lines = [*header, "", f"comments {len(view.comments)}"]
    for comment in view.comments:
        indent = "  " * (comment.depth + 1)
        lines.append(f"{indent}[{comment.id}] {comment.author} {comment.when}")
        lines.extend(f"{indent}  {line}" for line in comment.text.splitlines() or [""])
    return "\n".join(lines)


def render_sprint(view: SprintView) -> str:
    """Render the per-status rollup, then what is blocked and what is unowned."""
    lines = [f"tasks    {view.total}", ""]
    lines.append(
        table(
            ("STATUS", "COUNT"),
            [(status, str(count)) for status, count in view.statuses],
        )
    )
    lines.append("")
    lines.append(f"blocked  {len(view.blocked)}")
    lines.extend(f"  {row.id} {row.status} {row.name}" for row in view.blocked)
    lines.append(f"unassigned {len(view.unassigned)}")
    lines.extend(f"  {row.id} {row.status} {row.name}" for row in view.unassigned)
    return "\n".join(lines)


def render_search(hits: Sequence[SearchHit]) -> str:
    return table(
        ("ID", "TYPE", "LIST", "TITLE"),
        [(hit.id, hit.kind, hit.list_name, hit.title) for hit in hits],
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render fixed-column text. Values are never clipped — an ID is worth less
    than the name it stands next to, and a truncated name is a silent loss.

    The header is padded and stripped on the same pass as the rows. Padding it
    without stripping it left trailing whitespace on the header of any table
    whose last column holds values shorter than its own name.
    """
    headers = tuple(map(single_line, headers))
    rows = tuple(tuple(map(single_line, row)) for row in rows)
    if not rows:
        return "  ".join(headers) + "\n(none)"
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]
    return "\n".join(
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)).rstrip()
        for line in (headers, *rows)
    )


# --------------------------------------------------------------------------- payload


def _status(payload: Mapping[str, object]) -> str:
    status = payload.get("status")
    return _text(status, "status") if isinstance(status, dict) else ABSENT


def _assignees(payload: Mapping[str, object]) -> tuple[str, ...]:
    people = payload.get("assignees")
    if not isinstance(people, list):
        return ()
    return tuple(
        _text(person, "username") for person in people if isinstance(person, dict)
    )


def _blocked(payload: Mapping[str, object]) -> bool:
    status = _status(payload).casefold()
    tags = payload.get("tags")
    names = (
        [str(tag.get("name", "")).casefold() for tag in tags if isinstance(tag, dict)]
        if isinstance(tags, list)
        else []
    )
    return BLOCKED_MARKER in status or any(BLOCKED_MARKER in name for name in names)


def epoch_date(value: object) -> str:
    """Render a ClickUp millisecond epoch as a UTC date. Absent stays absent."""
    if isinstance(value, bool) or not isinstance(value, (int, str)) or value == "":
        return ABSENT
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).strftime("%Y-%m-%d")


def _text(payload: object, key: str) -> str:
    """Read a required string off a payload, failing where the field is missing."""
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"expected an object carrying {key!r}, got {type(payload).__name__}"
        )
    value = payload.get(key)
    if value is None or isinstance(value, (dict, list)):
        raise ValueError(f"payload is missing field {key!r}")
    return single_line(str(value))

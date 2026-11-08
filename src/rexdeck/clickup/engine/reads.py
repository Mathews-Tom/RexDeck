"""Read verbs, full-history pagination and raw task/comment helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from . import projections, registry, runtime
from .client import Client
from .diagnostics import SEVERITY_ERROR, SEVERITY_WARNING, Diagnostic, EngineError
from .projections import CommentLine
from .records import Registry
from .runtime import Outcome, Session

#: Refuse an incomplete result, rather than treating a page prefix as complete.
PAGE_LIMIT = 50
COMMENT_PAGE_SIZE = 25


def verb_sync(session: Session) -> Outcome:
    """Explicitly walk the selected topology, and write it only when requested."""
    loaded = session.registry or registry.load(session.registry_file())
    session.registry = loaded
    topology = runtime.discover(session, loaded)
    walked = Registry(
        schema_version=loaded.schema_version,
        digest=loaded.digest,
        synced_at=loaded.synced_at,
        team_id=loaded.team_id,
        seeds=loaded.seeds,
        topology=topology,
    )
    digest = registry.compute_digest(walked)
    written = None
    if session.args.write_registry:
        written = registry.rewrite(walked, now=_now(), path=session.registry_file())
        session.registry = written if written is not None else walked
    else:
        session.registry = walked

    counts = (
        ("spaces", len(topology.spaces)),
        ("folders", len(topology.folders)),
        ("lists", len(topology.lists)),
        ("members", len(topology.members)),
        (
            "custom_fields",
            sum(len(fields) for fields in topology.custom_fields.values()),
        ),
    )
    lines = [
        f"digest   {digest}",
        f"changed  {'yes' if digest != loaded.digest else 'no'}",
        f"written  {'yes' if written is not None else 'no'}",
        "",
        projections.table(
            ("COLLECTION", "COUNT"), [(name, str(count)) for name, count in counts]
        ),
    ]
    return Outcome(text="\n".join(lines), rows=sum(count for _, count in counts))


def verb_tasks(session: Session) -> Outcome:
    """One line per task, with the assignee already resolved."""
    loaded = runtime.prepare(session)
    topology = runtime.topology(session, loaded)
    list_id = registry.resolve_list(
        loaded, topology, session.args.list or loaded.seeds.list.id
    )
    params: dict[str, str | int | bool | Sequence[str]] = {}
    if session.args.assignee is not None:
        params["assignees[]"] = [
            registry.resolve_assignee(loaded, topology, session.args.assignee)
        ]
    if session.args.status:
        params["statuses[]"] = list(session.args.status)
    if session.args.due_before is not None:
        params["due_date_lt"] = runtime.epoch_ms(
            session.args.due_before, fix="--due-before YYYY-MM-DD"
        )

    payloads = paged_tasks(session.http(), f"/list/{list_id}/task", params)
    rows = [projections.task_row(payload) for payload in payloads]
    shown, truncated = _clip(rows, session.args.max_rows)
    return Outcome(
        text=projections.render_tasks(shown),
        rows=len(shown),
        truncated=truncated,
        diagnostics=_truncation(len(shown), len(rows), truncated),
    )


def verb_task(session: Session) -> Outcome:
    """A task and its whole conversation, in one invocation."""
    runtime.prepare(session)
    task_id = runtime.task_id(session.args.task_id)
    client = session.http()
    payload = client.get(f"/task/{task_id}")
    comments: list[CommentLine] = []
    if not session.args.no_comments:
        comments = _comment_tree(session, task_id)

    listing = payload.get("list")
    list_id = (
        str(listing["id"]) if isinstance(listing, dict) and "id" in listing else None
    )
    fields = runtime.fields_for(session, list_id) if list_id is not None else ()
    view, findings = projections.task_view(payload, comments, fields)
    return Outcome(
        text=projections.render_task(view),
        rows=1 + len(comments),
        diagnostics=findings,
    )


def verb_sprint(session: Session) -> Outcome:
    """Per-status rollup for one list, computed locally from a single fetch."""
    loaded = runtime.prepare(session)
    topology = runtime.topology(session, loaded)
    list_id = registry.resolve_list(loaded, topology, session.args.list_ref)
    payloads = paged_tasks(
        session.http(), f"/list/{list_id}/task", {"include_closed": "true"}
    )
    rows = [projections.task_row(payload) for payload in payloads]
    view = projections.sprint_view(rows, payloads)
    return Outcome(
        text=projections.render_sprint(view),
        rows=view.total,
    )


def verb_search(session: Session) -> Outcome:
    """Free-text search across the workspace's tasks, and only its tasks.

    ClickUp's public v2 REST surface exposes no free-text endpoint, so this
    fetches the filtered team tasks the registry's spaces scope to and matches
    the query against task names locally. One round trip either way; the match
    is stated here rather than implied to happen server-side.

    Every run carries `search/tasks-only`. Docs, whiteboards, dashboards,
    attachments and chats are not read, and a caller who takes an empty result
    as "the workspace has none of that" would be wrong in a way the verb's own
    name encourages.
    """
    loaded = runtime.prepare(session)
    topology = runtime.topology(session, loaded)
    spaces = [space.id for space in topology.spaces] or [loaded.seeds.space.id]
    params: dict[str, str | int | bool | Sequence[str]] = {"space_ids[]": spaces}
    payloads = paged_tasks(session.http(), f"/team/{loaded.team_id}/task", params)
    needle = session.args.query.casefold()
    kinds = set(session.args.type or [])
    hits = [
        hit
        for hit in (projections.search_hit(payload) for payload in payloads)
        if needle in hit.title.casefold() and (not kinds or hit.kind in kinds)
    ]
    shown, truncated = _clip(hits, session.args.max_rows)
    return Outcome(
        text=projections.render_search(shown),
        rows=len(shown),
        truncated=truncated,
        diagnostics=(
            projections.tasks_only(),
            *_truncation(len(shown), len(hits), truncated),
        ),
    )


# ------------------------------------------------------------------------- plumbing


def _comment_tree(session: Session, task_id: str) -> list[CommentLine]:
    """Project the raw comment tree for the existing text-facing verb."""
    tree = comment_tree(session.http(), task_id)
    return [projections.comment_line(payload, depth=depth) for payload, depth in tree]


def paged_comments(client: Client, task_id: str) -> list[Mapping[str, object]]:
    """Read all top-level comments with ClickUp's paired date/ID cursor.

    A full 25-comment page is never taken as a complete conversation. Replies
    remain available separately through ``comment_tree``.
    """
    path = f"/task/{runtime.task_id(task_id)}/comment"
    collected: list[Mapping[str, object]] = []
    seen_ids: set[str] = set()
    cursors: set[tuple[int, str]] = set()
    params: dict[str, str | int | bool | Sequence[str]] = {}
    for _ in range(PAGE_LIMIT):
        batch = runtime.rows(client.get(path, params), "comments", where=path)
        for comment in batch:
            comment_id = comment.get("id")
            if comment_id is None or isinstance(comment_id, (dict, list, bool)):
                raise EngineError(
                    projections.unexpected_shape(
                        "comment id", expected="a string", found=comment_id, where=path
                    )
                )
            key = str(comment_id)
            if key not in seen_ids:
                seen_ids.add(key)
                collected.append(comment)
        if len(batch) < COMMENT_PAGE_SIZE:
            return collected
        cursor = _comment_cursor(batch, path)
        if cursor in cursors:
            raise EngineError(
                Diagnostic(
                    code="result/repeated-cursor",
                    severity=SEVERITY_ERROR,
                    message="ClickUp repeated a comment-history cursor",
                    subject=path,
                    evidence={"pages": len(cursors) + 1, "rows": len(collected)},
                    supported_fixes=("refresh",),
                )
            )
        cursors.add(cursor)
        params = {"start": cursor[0], "start_id": cursor[1]}
    raise _page_ceiling(path, len(collected))


def _comment_cursor(
    comments: Sequence[Mapping[str, object]], path: str
) -> tuple[int, str]:
    """Use the oldest date and ID together, including timestamp ties."""
    candidates: list[tuple[int, str]] = []
    for comment in comments:
        date = comment.get("date")
        if isinstance(date, bool) or not isinstance(date, (str, int)):
            raise EngineError(
                projections.unexpected_shape(
                    "comment date",
                    expected="a millisecond epoch",
                    found=date,
                    where=path,
                )
            )
        try:
            timestamp = int(date)
        except ValueError:
            raise EngineError(
                projections.unexpected_shape(
                    "comment date",
                    expected="a millisecond epoch",
                    found=date,
                    where=path,
                )
            ) from None
        candidates.append((timestamp, str(comment["id"])))
    return min(
        candidates,
        key=lambda pair: (pair[0], int(pair[1]) if pair[1].isdigit() else 0, pair[1]),
    )


def comment_tree(
    client: Client, task_id: str
) -> list[tuple[Mapping[str, object], int]]:
    """Return raw parents and threaded replies, paired with display depth."""
    flattened: list[tuple[Mapping[str, object], int]] = []
    for parent in paged_comments(client, task_id):
        flattened.append((parent, 0))
        if projections.reply_count(parent) == 0:
            continue
        path = f"/comment/{runtime.task_id(str(parent['id']))}/reply"
        flattened.extend(
            (reply, 1)
            for reply in runtime.rows(client.get(path), "comments", where=path)
        )
    return flattened


def paged_tasks(
    client: Client,
    path: str,
    params: Mapping[str, str | int | bool | Sequence[str]] | None = None,
    *,
    on_page: Callable[[], None] | None = None,
) -> list[Mapping[str, object]]:
    """Read every page of a task endpoint, refusing to loop past the page ceiling."""
    collected: list[Mapping[str, object]] = []
    for page in range(PAGE_LIMIT):
        payload = client.get(path, {**(params or {}), "page": page})
        batch = runtime.rows(payload, "tasks", where=path)
        if on_page is not None:
            on_page()
        collected.extend(batch)
        if payload.get("last_page") is True or not batch:
            return collected
    raise _page_ceiling(path, len(collected))


def _page_ceiling(path: str, rows: int) -> EngineError:
    return EngineError(
        Diagnostic(
            code="result/page-ceiling",
            severity=SEVERITY_ERROR,
            message="the result set did not end within the page ceiling",
            subject=path,
            evidence={"path": path, "pages": PAGE_LIMIT, "rows": rows},
            supported_fixes=(
                ("refresh",)
                if path.endswith("/comment")
                else ("--status <status>", "--due-before YYYY-MM-DD", "--list <id>")
            ),
        )
    )


def _clip[RowT](rows: Sequence[RowT], ceiling: int) -> tuple[list[RowT], bool]:
    return list(rows[:ceiling]), len(rows) > ceiling


def _truncation(returned: int, total: int, truncated: bool) -> tuple[Diagnostic, ...]:
    """Make the row ceiling visible. Silence here means reasoning over a prefix."""
    if not truncated:
        return ()
    return (
        Diagnostic(
            code="budget/truncated",
            severity=SEVERITY_WARNING,
            message="the result set was larger than the row ceiling and is shown in part",
            subject=str(total),
            evidence={"returned": returned, "total": total},
            supported_fixes=(f"--max-rows {total}",),
        ),
    )


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

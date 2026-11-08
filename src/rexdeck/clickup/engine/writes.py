"""The write verbs: `touch` and `comment`.

A remote write cannot be staged atomically, so both verbs use optimistic
concurrency rather than a transaction. `--dry-run` reads the fields the write
would touch, prints their digest and the proposed diff, and sends nothing. The
same invocation with `--expect-digest` re-reads them, recomputes the digest, and
refuses rather than overwriting whatever moved underneath it.

Only the fields being written are digested. ClickUp bumps `date_updated` on
unrelated activity, so a whole-object digest would refuse almost every apply
that mattered.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import projections, reads, registry, runtime
from .diagnostics import SEVERITY_ERROR, SEVERITY_WARNING, Diagnostic, EngineError
from .receipts import Mutation, digest_json
from .records import CustomField
from .runtime import Outcome, Session

#: Custom-field types a caller's string can be converted into without guessing.
#: Anything else is refused: writing the wrong shape into a field ClickUp will
#: accept is a silent corruption, which is worse than the flag not working.
WRITABLE_FIELD_TYPES: tuple[str, ...] = (
    "checkbox",
    "currency",
    "date",
    "drop_down",
    "email",
    "number",
    "phone",
    "short_text",
    "text",
    "url",
)

TRUE_WORDS = ("true", "yes", "1")
FALSE_WORDS = ("false", "no", "0")


@dataclass(frozen=True, slots=True)
class Staged:
    """One field a write touches.

    `current` is the remote value as it reads now and is what gets digested, so
    a pin catches the field moving. The two rendered forms are what the diff
    shows a caller — a dropdown's raw value is an option UUID or an orderindex,
    and neither is readable in a diff.
    """

    name: str
    current: object
    current_text: str
    proposed_text: str


def verb_touch(session: Session) -> Outcome:
    """Set fields on one task, staged against the values they hold right now."""
    _require_digest(session)
    target = runtime.task_id(session.args.task_id)
    wanted = (
        session.args.status,
        session.args.assignee,
        session.args.due,
        session.args.description,
        session.args.points,
        session.args.field,
    )
    if not any(one is not None for one in wanted):
        # Checked before the read, so a misuse costs no call at all.
        raise _nothing_to_write(target)
    # Parsed before the read for the same reason: a date the caller mistyped is
    # a usage error, and a usage error should not cost a call to discover.
    due = (
        None
        if session.args.due is None
        else runtime.epoch_ms(session.args.due, fix="--due YYYY-MM-DD")
    )
    points = None if session.args.points is None else _points(session.args.points)
    loaded = runtime.prepare(session)
    # The Markdown form is what a description write replaces, so it is also the
    # form staged and digested; the plain `description` drops its formatting.
    payload = session.http().get(
        f"/task/{target}", {"include_markdown_description": "true"}
    )

    staged: list[Staged] = []
    body: dict[str, object] = {}
    if session.args.status is not None:
        staged.append(_staged_status(payload, session.args.status))
        body["status"] = session.args.status
    if session.args.assignee is not None:
        topology = runtime.topology(session, loaded)
        who = registry.resolve_assignee(loaded, topology, session.args.assignee)
        current = _current_assignees(payload)
        staged.append(
            Staged(
                name="assignees",
                current=current,
                current_text=" ".join(current) or projections.ABSENT,
                proposed_text=who,
            )
        )
        # ClickUp spells an assignee change as a delta, so setting one assignee
        # means naming everyone it displaces. Passing only `add` would append.
        body["assignees"] = {
            "add": [_member_number(who)],
            "rem": [_member_number(other) for other in current if other != who],
        }
    if due is not None:
        staged.append(_staged_due(payload, session.args.due))
        body["due_date"] = due
    if session.args.description is not None:
        held = payload.get("markdown_description") or payload.get("description")
        current_description = held if isinstance(held, str) and held else None
        staged.append(
            Staged(
                name="description",
                current=current_description,
                current_text=current_description or projections.ABSENT,
                proposed_text=session.args.description,
            )
        )
        body["markdown_content"] = session.args.description
    if points is not None:
        held_points = payload.get("points")
        staged.append(
            Staged(
                name="points",
                current=held_points,
                current_text=projections.ABSENT
                if held_points is None
                else str(held_points),
                proposed_text=str(points),
            )
        )
        body["points"] = points

    custom = _staged_custom(session, payload, session.args.field or [])
    staged.extend(item for item, _, _ in custom)

    mutation = _mutation(target, staged)
    _guard(target, staged, mutation.field_digest, session.args.expect_digest)
    if session.args.dry_run:
        return _outcome(mutation, staged, applied=False, pinned=False)

    client = session.http()
    if body:
        client.put(f"/task/{target}", body)
    for _, definition, value in custom:
        client.post(f"/task/{target}/field/{definition.id}", {"value": value})
    return _outcome(
        mutation, staged, applied=True, pinned=session.args.expect_digest is not None
    )


def verb_comment(session: Session) -> Outcome:
    """Add one comment to a task, staged against the thread it is joining.

    A comment overwrites nothing, so the conflict worth refusing is not a field
    being edited but the conversation having moved on since the caller read it —
    replying into a thread that gained three comments in between is the mistake
    this catches.

    The digest pins every paged top-level comment ID, independent of API
    ordering, and therefore detects additions as well as deletions.
    """
    _require_digest(session)
    target = runtime.task_id(session.args.task_id)
    runtime.prepare(session)
    client = session.http()
    path = f"/task/{target}/comment"
    comments = reads.paged_comments(client, target)
    thread = {"count": len(comments), "ids": _comment_ids(comments, where=path)}
    staged = [
        Staged(
            name="comments",
            current=thread,
            current_text=f"{len(comments)} comment{'' if len(comments) == 1 else 's'}",
            proposed_text=session.args.text,
        )
    ]

    mutation = _mutation(target, staged)
    _guard(target, staged, mutation.field_digest, session.args.expect_digest)
    if session.args.dry_run:
        return _outcome(mutation, staged, applied=False, pinned=False)

    # `notify_all` is required by the endpoint and false here on purpose: a
    # comment left by a CLI should not page everyone watching the task.
    client.post(
        f"/task/{target}/comment",
        {"comment_text": session.args.text, "notify_all": False},
    )
    return _outcome(
        mutation, staged, applied=True, pinned=session.args.expect_digest is not None
    )


def _comment_ids(comments: Sequence[Mapping[str, object]], *, where: str) -> list[str]:
    """Return the thread's comment IDs, sorted, refusing a comment that has none.

    Indexed off `id` rather than position, so the pin does not depend on the
    order ClickUp returns. A comment carrying no `id` would have raised a bare
    `KeyError` here — a traceback with no receipt, in the one verb whose entire
    output is a receipt.
    """
    found: list[str] = []
    for comment in comments:
        value = comment.get("id")
        if value is None:
            raise EngineError(
                projections.unexpected_shape(
                    "comment id", expected="a string", found=value, where=where
                )
            )
        found.append(str(value))
    return sorted(found)


# ------------------------------------------------------------------------------ staging


def _mutation(target: str, staged: Sequence[Staged]) -> Mutation:
    """Digest the fields being written, and state the change proposed to each.

    Each entry carries its own digest as well as its rendered values, computed
    the same way `_guard` computes the ones it hands back on a refusal. That is
    what makes a refused apply attributable: a caller holding the dry run's
    receipt and the refusal diffs `mutation.diff` against `evidence.fields` and
    reads off exactly which field moved, which neither receipt can say alone.
    """
    return Mutation(
        target=target,
        field_digest=digest_json({item.name: item.current for item in staged}),
        diff={
            item.name: {
                "from": item.current_text,
                "to": item.proposed_text,
                "digest": digest_json(item.current),
            }
            for item in staged
        },
    )


def _require_digest(session: Session) -> None:
    """Reject an unpinned apply before reading credentials or remote state."""
    if not session.args.dry_run and not session.args.expect_digest:
        raise EngineError(
            Diagnostic(
                code="usage/missing-expect-digest",
                severity=SEVERITY_ERROR,
                message="writes require --expect-digest unless --dry-run is selected",
                subject="--expect-digest",
                evidence={},
                supported_fixes=("--dry-run", "--expect-digest <sha256>"),
            ),
            exit_code=2,
        )


def _guard(
    target: str, staged: Sequence[Staged], digest: str, expected: str | None
) -> None:
    """Refuse an apply whose pinned fields no longer hold what the dry run read.

    A sha256 is not invertible and this invocation never saw the dry run's
    values, so with more than one field under the pin the engine knows that one
    of them moved but not which. What it hands back closes that gap without
    guessing: every field the pin covered, each with its current value and its
    own digest, against which the dry run's `mutation.diff` carries the same
    per-field digests. A caller holding both receipts can attribute the refusal
    exactly. With a single field under the pin the message names it outright.
    """
    if expected is None or expected == digest:
        return
    names = [item.name for item in staged]
    moved = names[0] if len(names) == 1 else None
    raise EngineError(
        Diagnostic(
            code="state/changed-under-us",
            severity=SEVERITY_ERROR,
            message=(
                f"{moved} no longer holds what the dry run recorded"
                if moved is not None
                else "the fields this write pins no longer hold what the dry run recorded"
            ),
            subject=target,
            evidence={
                "expected": expected,
                "actual": digest,
                "fields": {
                    item.name: {
                        "current": item.current_text,
                        "digest": digest_json(item.current),
                    }
                    for item in staged
                },
            },
            supported_fixes=("mutation.diff", "--dry-run", f"--expect-digest {digest}"),
        )
    )


def _outcome(
    mutation: Mutation, staged: Sequence[Staged], *, applied: bool, pinned: bool
) -> Outcome:
    """Build the receipt-only outcome both write verbs return."""
    findings = () if pinned or not applied else (_unpinned(mutation),)
    return Outcome(
        text=_render(mutation, staged, applied=applied, pinned=pinned),
        rows=len(staged),
        diagnostics=findings,
        mutation=mutation,
    )


def _render(
    mutation: Mutation, staged: Sequence[Staged], *, applied: bool, pinned: bool
) -> str:
    """Render the mutation. A write returns this and no entity listing at all."""
    header = [
        f"target   {mutation.target}",
        f"digest   {mutation.field_digest}",
        f"applied  {'yes' if applied else 'no — dry run'}",
        f"pinned   {'yes' if pinned else 'no'}",
        "",
    ]
    rows = [(item.name, item.current_text, item.proposed_text) for item in staged]
    return "\n".join([*header, projections.table(("FIELD", "FROM", "TO"), rows)])


def _unpinned(mutation: Mutation) -> Diagnostic:
    """Say in the receipt that nothing checked the task had not moved."""
    return Diagnostic(
        code="state/unpinned",
        severity=SEVERITY_WARNING,
        message="the write applied unpinned, so nothing checked the fields had not moved",
        subject=mutation.target,
        evidence={"target": mutation.target, "field_digest": mutation.field_digest},
        supported_fixes=("--dry-run", f"--expect-digest {mutation.field_digest}"),
    )


# ------------------------------------------------------------------------------- fields


def _staged_status(payload: Mapping[str, object], proposed: str) -> Staged:
    status = payload.get("status")
    current = (
        str(status["status"])
        if isinstance(status, dict) and "status" in status
        else None
    )
    return Staged(
        name="status",
        current=current,
        current_text=current or projections.ABSENT,
        proposed_text=proposed,
    )


def _staged_due(payload: Mapping[str, object], spelled: str) -> Staged:
    current = payload.get("due_date")
    return Staged(
        name="due_date",
        current=None if current is None else str(current),
        current_text=projections.epoch_date(current),
        proposed_text=spelled,
    )


def _current_assignees(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return the task's assignee IDs, ordered so an equal set digests equal."""
    people = payload.get("assignees")
    if not isinstance(people, list):
        return ()
    return tuple(
        sorted(str(person["id"]) for person in people if isinstance(person, dict))
    )


def _staged_custom(
    session: Session, payload: Mapping[str, object], specs: Sequence[str]
) -> list[tuple[Staged, CustomField, object]]:
    """Resolve every `--field NAME=VALUE` against the list's own definitions."""
    if not specs:
        return []
    listing = payload.get("list")
    list_id = (
        str(listing["id"]) if isinstance(listing, dict) and "id" in listing else None
    )
    definitions = runtime.fields_for(session, list_id)
    values = payload.get("custom_fields")
    held = {
        str(blob["id"]): blob.get("value")
        for blob in (values if isinstance(values, list) else [])
        if isinstance(blob, dict) and "id" in blob
    }
    resolved: list[tuple[Staged, CustomField, object]] = []
    for spec in specs:
        name, separator, raw = spec.partition("=")
        if not separator or not name:
            raise _unparseable_field(spec, definitions)
        definition = _definition(name, definitions)
        value = _field_value(definition, raw)
        current = held.get(definition.id)
        resolved.append(
            (
                Staged(
                    name=f"field:{definition.name}",
                    current=current,
                    current_text=projections.render_field_value(definition, current)[0],
                    proposed_text=raw,
                ),
                definition,
                value,
            )
        )
    return resolved


def _definition(name: str, definitions: Sequence[CustomField]) -> CustomField:
    """Return the one custom field `name` names, refusing a miss or a tie."""
    matches = [
        one for one in definitions if one.name.casefold() == name.strip().casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise EngineError(
            Diagnostic(
                code="registry/ambiguous",
                severity=SEVERITY_ERROR,
                message="the custom-field name matches more than one field on this list",
                subject=name,
                evidence={"name": name, "matched": [one.id for one in matches]},
                supported_fixes=tuple(one.id for one in matches),
            ),
            exit_code=2,
        )
    raise EngineError(
        Diagnostic(
            code="usage/unknown-field",
            severity=SEVERITY_ERROR,
            message="the list has no custom field by that name",
            subject=name,
            evidence={"name": name, "searched": len(definitions)},
            supported_fixes=_writable_names(definitions),
        ),
        exit_code=2,
    )


def _field_value(definition: CustomField, raw: str) -> object:
    """Convert a caller's string into the shape ClickUp stores for this type."""
    if definition.type in ("text", "short_text", "url", "email", "phone"):
        return raw
    if definition.type in ("number", "currency"):
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError as error:
            raise _bad_field_value(definition, raw, ("<number>",)) from error
    if definition.type == "checkbox":
        folded = raw.strip().casefold()
        if folded in TRUE_WORDS:
            return True
        if folded in FALSE_WORDS:
            return False
        raise _bad_field_value(definition, raw, (*TRUE_WORDS, *FALSE_WORDS))
    if definition.type == "date":
        return runtime.epoch_ms(raw, fix=f"--field {definition.name}=YYYY-MM-DD")
    if definition.type == "drop_down":
        for option in definition.options:
            if option.id == raw or option.name.casefold() == raw.strip().casefold():
                return option.id
        raise _bad_field_value(
            definition, raw, tuple(one.name for one in definition.options)
        )
    raise EngineError(
        Diagnostic(
            code="usage/unsupported-field-type",
            severity=SEVERITY_ERROR,
            message="this engine has no conversion for that custom field's type",
            subject=definition.name,
            evidence={"field": definition.name, "type": definition.type},
            supported_fixes=WRITABLE_FIELD_TYPES,
        ),
        exit_code=2,
    )


def _writable_names(definitions: Sequence[CustomField]) -> tuple[str, ...]:
    """Return the field names `--field` can actually set, which is the fix."""
    names = tuple(one.name for one in definitions if one.type in WRITABLE_FIELD_TYPES)
    return names or ("--field",)


def _bad_field_value(
    definition: CustomField, raw: str, accepted: tuple[str, ...]
) -> EngineError:
    return EngineError(
        Diagnostic(
            code="usage/bad-field-value",
            severity=SEVERITY_ERROR,
            message="the value is not one this custom field's type accepts",
            subject=definition.name,
            evidence={"field": definition.name, "type": definition.type, "given": raw},
            supported_fixes=accepted or ("<value>",),
        ),
        exit_code=2,
    )


def _unparseable_field(spec: str, definitions: Sequence[CustomField]) -> EngineError:
    return EngineError(
        Diagnostic(
            code="usage/bad-field",
            severity=SEVERITY_ERROR,
            message="the field argument is not spelled name=value",
            subject=spec,
            evidence={"given": spec},
            supported_fixes=tuple(
                f"--field {name}=<value>" for name in _writable_names(definitions)
            ),
        ),
        exit_code=2,
    )


def _member_number(member_id: str) -> int:
    """Return a member ID as the integer ClickUp's assignee delta expects."""
    if not member_id.isdigit():
        raise EngineError(
            Diagnostic(
                code="usage/bad-id",
                severity=SEVERITY_ERROR,
                message="the member ID is not a ClickUp user ID",
                subject=member_id,
                evidence={"given": member_id},
                supported_fixes=("--assignee <numeric-id>", registry.RESYNC),
            ),
            exit_code=2,
        )
    return int(member_id)


def _points(raw: str) -> int | float:
    """Return Sprint Points as the JSON number ClickUp stores; whole stays whole."""
    try:
        value = float(raw.strip())
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise EngineError(
            Diagnostic(
                code="usage/bad-points",
                severity=SEVERITY_ERROR,
                message="sprint points must be a finite number of zero or more",
                subject="--points",
                evidence={"given": raw},
                supported_fixes=("--points 3", "--points 0.5"),
            ),
            exit_code=2,
        )
    return int(value) if value.is_integer() else value


def _nothing_to_write(target: str) -> EngineError:
    return EngineError(
        Diagnostic(
            code="usage/nothing-to-write",
            severity=SEVERITY_ERROR,
            message="touch was given no field to set",
            subject=target,
            evidence={"target": target},
            supported_fixes=(
                "--status",
                "--assignee",
                "--due",
                "--description",
                "--points",
                "--field",
            ),
        ),
        exit_code=2,
    )

"""Argparse dispatch, request accounting, receipt assembly and exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from rexdeck.clickup.text import clean

from . import reads, registry, writes
from .diagnostics import EngineError, has_error
from .receipts import Receipt, ResultSummary, digest_json, render
from .runtime import Outcome, Session

DEFAULT_MAX_ROWS = 50


VERBS: Mapping[str, Callable[[Session], Outcome]] = {
    "sync": reads.verb_sync,
    "tasks": reads.verb_tasks,
    "task": reads.verb_task,
    "sprint": reads.verb_sprint,
    "search": reads.verb_search,
    "touch": writes.verb_touch,
    "comment": writes.verb_comment,
}


class _Writer(Protocol):
    def write(self, text: str, /) -> object: ...


class _Parser(argparse.ArgumentParser):
    """Sanitize help and usage diagnostics as well as normal CLI output."""

    def _print_message(self, message: str | None, file: _Writer | None = None) -> None:
        if message:
            super()._print_message(clean(message), file)


def build_parser() -> argparse.ArgumentParser:
    """Build the verb surface. `argparse` only — the runtime deps stay at two."""
    parser = _Parser(
        prog="python3 -m rexdeck.clickup.engine",
        description=(
            "Read and write ClickUp with local topology resolution, projected"
            " task listings and conversations, sprint rollups, task search,"
            " and digest-guarded staged writes."
        ),
    )
    globals_parser = _Parser(add_help=False)
    globals_parser.add_argument(
        "--json",
        action="store_true",
        help="print the receipt on stdout and nothing else",
    )
    globals_parser.add_argument(
        "--quiet", action="store_true", help="suppress stderr entirely"
    )
    globals_parser.add_argument(
        "--registry-path",
        required=True,
        help="use an explicitly selected local registry",
    )
    globals_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="resolve live against the API, not the cache",
    )
    globals_parser.add_argument(
        "--max-rows",
        type=int,
        default=DEFAULT_MAX_ROWS,
        help=(
            "ceiling on emitted rows; exceeding it emits budget/truncated"
            f" (default {DEFAULT_MAX_ROWS})"
        ),
    )
    staging = _Parser(add_help=False)
    staging.add_argument(
        "--dry-run",
        action="store_true",
        help="stage the write, print its field digest and diff, and send nothing",
    )
    staging.add_argument(
        "--expect-digest",
        metavar="sha256",
        help="apply only while the fields still digest to this, from a --dry-run",
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    sync = verbs.add_parser(
        "sync", parents=[globals_parser], help="walk the hierarchy once"
    )
    sync.add_argument(
        "--write-registry",
        action="store_true",
        help="write the walk to the selected local registry",
    )

    tasks = verbs.add_parser(
        "tasks", parents=[globals_parser], help="one line per task"
    )
    tasks.add_argument("--list", help="list ID or name; defaults to the seed list")
    tasks.add_argument("--assignee", help="member ID, username, or me")
    tasks.add_argument("--status", action="append", help="status name, repeatable")
    tasks.add_argument("--due-before", help="only tasks due before this ISO-8601 date")

    task = verbs.add_parser(
        "task", parents=[globals_parser], help="a task and its comments"
    )
    task.add_argument("task_id", metavar="id")
    task.add_argument("--no-comments", action="store_true", help="header block only")

    sprint = verbs.add_parser(
        "sprint", parents=[globals_parser], help="per-status rollup"
    )
    sprint.add_argument("list_ref", metavar="list", help="list ID or name")

    search = verbs.add_parser(
        "search", parents=[globals_parser], help="workspace search"
    )
    search.add_argument("query", metavar="q")
    search.add_argument(
        "--type", action="append", help="restrict to one result type, repeatable"
    )

    touch = verbs.add_parser(
        "touch", parents=[globals_parser, staging], help="set fields on one task"
    )
    touch.add_argument("task_id", metavar="id")
    touch.add_argument("--status", help="set the task status")
    touch.add_argument(
        "--assignee", help="set the assignee: member ID, username, or me"
    )
    touch.add_argument("--due", help="set the due date, ISO-8601 YYYY-MM-DD")
    touch.add_argument(
        "--field",
        action="append",
        metavar="NAME=VALUE",
        help="set a custom field by name, repeatable; names resolve against the list",
    )

    comment = verbs.add_parser(
        "comment", parents=[globals_parser, staging], help="add one comment to a task"
    )
    comment.add_argument("task_id", metavar="id")
    comment.add_argument("--text", required=True, help="the comment body")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one verb and return its exit code: 0 clean, 1 blocked, 2 misused."""
    args = build_parser().parse_args(argv)
    session = Session(args=args)
    try:
        outcome, exit_code = _run(session)
    finally:
        session.close()

    receipt = Receipt(
        verb=str(args.verb),
        args_digest=digest_json(vars(args)),
        registry=registry.stamp(session.registry),
        result=ResultSummary(rows=outcome.rows, truncated=outcome.truncated),
        diagnostics=outcome.diagnostics,
        budget={"api_calls_made": session.client.calls if session.client else 0},
        mutation=outcome.mutation,
    )
    _emit(args, receipt, outcome.text)
    return exit_code


def _run(session: Session) -> tuple[Outcome, int]:
    try:
        outcome = VERBS[str(session.args.verb)](session)
    except EngineError as error:
        return Outcome(
            text="", rows=0, diagnostics=(error.diagnostic,)
        ), error.exit_code
    return outcome, 1 if has_error(outcome.diagnostics) else 0


def _emit(
    args: argparse.Namespace,
    receipt: Receipt,
    text: str,
) -> None:
    """Route output. Under `--json` stdout carries the receipt and nothing else."""
    if args.json:
        print(render(receipt))
    elif text:
        print(clean(text))
    if args.quiet:
        return
    if args.json and text:
        print(clean(text), file=sys.stderr)
    for diagnostic in receipt.diagnostics:
        fixes = ", ".join(diagnostic.supported_fixes)
        print(
            clean(
                f"{diagnostic.severity} {diagnostic.code}: {diagnostic.message}"
                f" [{diagnostic.subject}] — fix: {fixes}"
            ),
            file=sys.stderr,
        )


__all__ = ["build_parser", "main"]

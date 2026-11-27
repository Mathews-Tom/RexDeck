"""The kata CLI as Kata Desk's only transport: JSON reads, no writes, no daemon start."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rexdeck.actions.api import clean

API_VERSION = 1
TIMEOUT = 15.0
# kata codes that mean "not running" or "no project here" rather than a failure.
_STOPPED = ("daemon_unavailable",)
_UNBOUND = ("project_not_initialized",)


class KataError(Exception):
    """A refusal with a stable state: missing, stopped, unbound, version or failed."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


@dataclass(frozen=True)
class Daemon:
    kind: str
    address: str


@dataclass(frozen=True)
class Issue:
    ref: str
    short: str
    title: str
    status: str
    priority: int | None
    owner: str
    updated: str
    parent: str | None
    blocked: bool

    @property
    def project(self) -> str:
        return self.ref.partition("#")[0]


@dataclass(frozen=True)
class Comment:
    author: str
    created: str
    body: str


@dataclass(frozen=True)
class Detail:
    issue: Issue
    body: str
    labels: tuple[str, ...]
    comments: tuple[Comment, ...]
    comments_truncated: bool
    links: tuple[str, ...]
    children: tuple[Issue, ...]


@dataclass(frozen=True)
class Request:
    ref: str
    title: str
    sender: str
    message: str


@dataclass(frozen=True)
class Listing:
    issues: tuple[Issue, ...]
    truncated: bool


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


def _decoded(text: str) -> Mapping[str, Any] | None:
    """One JSON object, or None for empty, non-JSON or non-object output."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def executable() -> str:
    """The kata the pane's PATH resolves first: enable puts its directory there."""
    found = shutil.which("kata", path=os.environ.get("PATH", ""))
    if found is None or not Path(found).is_absolute():
        raise KataError(
            "missing",
            "kata is not on this pane's PATH. Install kata, then run "
            "rexdeck enable katadesk again.",
        )
    return found


def _object(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise KataError("failed", f"kata returned malformed {what}")
    return value


def _text(
    data: Mapping[str, Any], key: str, what: str, *, optional: bool = False
) -> str:
    value = data.get(key)
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise KataError("failed", f"kata returned {what} without a valid {key}")
    return clean(value)


def when(value: str) -> str:
    """kata's UTC timestamps as local minutes; unparseable values are shown as given."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _ref(value: object) -> str | None:
    if value is None:
        return None
    return _text(_object(value, "link"), "qualified_id", "a link")


def _issue(value: object) -> Issue:
    data = _object(value, "issue")
    priority = data.get("priority")
    if priority is not None and (
        isinstance(priority, bool) or not isinstance(priority, int)
    ):
        raise KataError("failed", "kata returned an issue with a malformed priority")
    ref = data.get("qualified_id")
    short = _text(data, "short_id", "an issue")
    return Issue(
        ref=clean(ref) if isinstance(ref, str) else short,
        short=short,
        title=_text(data, "title", "an issue"),
        status=_text(data, "status", "an issue"),
        priority=priority,
        owner=_text(data, "owner", "an issue", optional=True),
        updated=when(_text(data, "updated_at", "an issue")),
        parent=_ref(data.get("parent")),
        blocked=data.get("blocked") is True,
    )


def _link(value: object, ref: str) -> str:
    data = _object(value, "link")
    kind = _text(data, "type", "a link")
    source = _ref(data.get("from")) or "?"
    target = _ref(data.get("to")) or "?"
    if kind == "parent":
        return f"child {source}" if target == ref else f"parent {target}"
    if kind == "blocks":
        return f"blocked by {source}" if target == ref else f"blocks {target}"
    return f"{kind} {target if source == ref else source}"


def event_kind(line: str) -> str | None:
    """Classify one `kata events --tail --json` line by shape: event, reset or noise."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("reset_required") is True:
        return "reset"
    event = data.get("event_id")
    if isinstance(event, int) and not isinstance(event, bool):
        if isinstance(data.get("type"), str):
            return "event"
    return None


def tree(issues: Sequence[Issue]) -> list[tuple[int, Issue]]:
    """Issues nested under their parents in listed order; orphans are roots."""
    present = {issue.ref for issue in issues}
    children: dict[str, list[Issue]] = {}
    roots: list[Issue] = []
    for issue in issues:
        if issue.parent is not None and issue.parent in present:
            children.setdefault(issue.parent, []).append(issue)
        else:
            roots.append(issue)
    rows: list[tuple[int, Issue]] = []
    seen: set[str] = set()

    def visit(issue: Issue, depth: int) -> None:
        if issue.ref in seen:
            return
        seen.add(issue.ref)
        rows.append((depth, issue))
        for child in children.get(issue.ref, ()):
            visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    # A parent cycle has no root; list its members flat rather than drop them.
    for issue in issues:
        visit(issue, 0)
    return rows


class Kata:
    """Read-only kata calls from the invoking directory; every call asks for JSON."""

    def __init__(self, path: str, cwd: Path, runner: Runner = _run) -> None:
        self.path = path
        self.cwd = cwd
        self._runner = runner

    def _json(self, *args: str) -> Mapping[str, Any]:
        argv = [self.path, *args, "--json"]
        try:
            result = self._runner(argv, self.cwd)
        except subprocess.TimeoutExpired as exc:
            raise KataError(
                "failed", f"kata {args[0]} did not answer within {TIMEOUT:g} s"
            ) from exc
        except OSError as exc:
            raise KataError(
                "missing", f"Cannot run kata at {self.path}: {exc}"
            ) from exc
        data = _decoded(result.stdout)
        if data is None:
            # kata writes its JSON error envelope to stderr, not stdout.
            data = _decoded(result.stderr)
        if data is None:
            lines = result.stderr.strip().splitlines()
            detail = clean(lines[-1]) if lines else ""
            raise KataError(
                "failed",
                f"kata {args[0]} exited {result.returncode} without JSON"
                + (f": {detail}" if detail else ""),
            )
        error = data.get("error")
        if isinstance(error, dict):
            code, kind = error.get("code"), error.get("kind")
            message = error.get("message")
            text = (
                clean(message)
                if isinstance(message, str)
                else "kata refused the request"
            )
            if kind in _STOPPED or code in _STOPPED:
                raise KataError("stopped", text)
            if code in _UNBOUND:
                raise KataError("unbound", text)
            raise KataError("failed", text)
        if result.returncode != 0:
            raise KataError("failed", f"kata {args[0]} exited {result.returncode}")
        version = data.get("kata_api_version")
        if version != API_VERSION:
            raise KataError(
                "version",
                f"kata answered with JSON version {version!r}; Kata Desk reads "
                f"version {API_VERSION} (qualified with kata 0.23.0)",
            )
        return data

    def locate(self) -> Daemon:
        """Find the daemon kata would use without starting one."""
        data = self._json("daemon", "locate")
        return Daemon(
            _text(data, "kind", "daemon location"),
            _text(data, "address", "daemon location"),
        )

    def whoami(self) -> str:
        return _text(self._json("whoami"), "actor", "whoami")

    def recipient(self) -> str:
        """KATA_INBOX_USER when set, else the actor kata writes as; kata has no default."""
        value = os.environ.get("KATA_INBOX_USER", "").strip()
        return clean(value) if value else self.whoami()

    def issues(self, status: str) -> Listing:
        if status not in ("open", "closed"):
            raise ValueError(f"Unsupported status filter: {status}")
        data = self._json("list", "--status", status, "--limit", "0")
        rows = data.get("issues")
        if not isinstance(rows, list):
            raise KataError("failed", "kata list returned no issue list")
        return Listing(
            tuple(_issue(row) for row in rows), data.get("truncated") is True
        )

    def detail(self, ref: str) -> Detail:
        data = self._json("show", ref)
        issue_data = _object(data.get("issue"), "issue")
        # show's issue lacks the qualified id; keep the ref it was asked for.
        issue = _issue({**issue_data, "qualified_id": ref})
        comments = tuple(
            Comment(
                _text(_object(row, "comment"), "author", "a comment"),
                when(_text(_object(row, "comment"), "created_at", "a comment")),
                _text(_object(row, "comment"), "body", "a comment"),
            )
            for row in data.get("comments") or ()
        )
        labels = tuple(
            clean(label) for label in data.get("labels") or () if isinstance(label, str)
        )
        return Detail(
            issue=issue,
            body=_text(issue_data, "body", "an issue", optional=True),
            labels=labels,
            comments=comments,
            comments_truncated=data.get("comments_truncated") is True,
            links=tuple(_link(row, ref) for row in data.get("links") or ()),
            children=tuple(_issue(row) for row in data.get("children") or ()),
        )

    def inbox(self, recipient: str) -> tuple[Request, ...]:
        data = self._json("inbox", "--all", "--for", recipient)
        rows = data.get("requests")
        if not isinstance(rows, list):
            raise KataError("failed", "kata inbox returned no request list")
        requests = []
        for row in rows:
            item = _object(row, "request")
            requests.append(
                Request(
                    ref=_text(item, "ref", "a request"),
                    title=_text(item, "title", "a request", optional=True),
                    sender=_text(item, "from", "a request", optional=True),
                    message=_text(item, "message", "a request"),
                )
            )
        return tuple(requests)

    def events_argv(self) -> list[str]:
        """The live stream for the bound project; kata reconnects it by itself."""
        return [self.path, "events", "--tail", "--json"]

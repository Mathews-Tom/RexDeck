"""Identity-bound ClickUp read/write pane backed by its vendored MIT engine."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import secrets
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import TypedDict, Unpack, cast

from rexdeck import settings
from rexdeck.clickup.engine import client as backend
from rexdeck.clickup.engine import (
    credentials,
    diagnostics,
    reads,
    receipts,
    registry,
    runtime,
    writes,
)
from rexdeck.clickup.text import clean, single_line

_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


class ClickUpError(RuntimeError):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ClickUpError("malformed", "Expected a JSON object")
    return cast(dict[str, object], value)


def rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ClickUpError("malformed", "Expected a JSON array")
    return [obj(item) for item in value]


def text(value: object, *, multiline: bool = False) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return clean(str(value)) if multiline else single_line(str(value))
    raise ClickUpError("malformed", "Required text or identifier is absent")


@dataclass(frozen=True)
class Selection:
    account: str
    team: str
    list: str
    credential_env: str = "CLICKUP_API_TOKEN"

    def __post_init__(self) -> None:
        if not all(
            v.isascii() and v.isdecimal() for v in (self.account, self.team, self.list)
        ):
            raise ValueError("Account, team and list must be explicit numeric IDs")
        credentials.validate_env_name(self.credential_env)


def selection_path() -> Path:
    return settings.settings_path().with_name("clickup.json")


def save_selection(selection: Selection) -> None:
    path = selection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("ClickUp selection must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".clickup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(asdict(selection), stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_selection() -> Selection:
    path = selection_path()
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "No explicit ClickUp selection; run rexdeck clickup select --account ID --team ID --list ID"
        )
    if path.stat().st_size > 16384:
        raise ValueError("ClickUp selection is too large")
    document = obj(json.loads(path.read_text()))
    if set(document) != {"account", "team", "list", "credential_env"}:
        raise ValueError("Invalid ClickUp selection fields")
    return Selection(
        *(text(document[k]) for k in ("account", "team", "list", "credential_env"))
    )


def engine_error(error: diagnostics.EngineError) -> ClickUpError:
    finding = error.diagnostic
    status = finding.evidence.get("status")
    state = {
        401: "authentication",
        403: "permission",
        404: "not-found",
        429: "rate-limited",
    }.get(status if isinstance(status, int) else 0)
    if finding.evidence.get("access_restricted") is True:
        state = "permission"
    elif isinstance(status, int) and status >= 500:
        state = "network"
    if state is None:
        state = {
            "auth/missing-credential": "missing-credential",
            "transport/unreachable": "network",
            "transport/bad-response": "malformed",
        }.get(finding.code, finding.code)
    retry = finding.evidence.get("retry_after")
    suffix = f"; Retry-After: {retry}" if isinstance(retry, int) else ""
    return ClickUpError(state, clean(finding.message) + "; no automatic retry" + suffix)


class Client(backend.Client):
    """An engine session; frontend writes additionally require a live approval."""

    def __init__(self, selection: Selection) -> None:
        self.selection = selection
        self.write_guard: Callable[[str], None] | None = None
        super().__init__(os.environ.get(selection.credential_env, ""))

    def get(
        self, path: str, params: backend.Params | None = None
    ) -> Mapping[str, object]:
        if not os.environ.get(self.selection.credential_env):
            raise ClickUpError(
                "missing-credential",
                f"Missing inherited environment reference {self.selection.credential_env}",
            )
        return super().get(path, params)

    def _approved(self, path: str) -> None:
        if self.write_guard is None:
            raise ClickUpError("unconfirmed", "No live selected-task approval")
        self.write_guard(path)

    def put(self, path: str, body: Mapping[str, object]) -> Mapping[str, object]:
        self._approved(path)
        return super().put(path, body)

    def post(self, path: str, body: Mapping[str, object]) -> Mapping[str, object]:
        self._approved(path)
        return super().post(path, body)

    def _before_send(self, method: str, path: str) -> None:
        if method in {"PUT", "POST"}:
            self._approved(path)


@dataclass(frozen=True)
class Status:
    name: str
    kind: str
    color: str

    @classmethod
    def parse(cls, data: Mapping[str, object]) -> Status:
        return cls(
            text(data.get("status")),
            text(data.get("type") or ""),
            text(data.get("color") or ""),
        )

    def label(self) -> str:
        return f"{self.name} (type={self.kind or 'unavailable'}, color={self.color or 'unavailable'})"


@dataclass(frozen=True)
class Task:
    id: str
    custom_id: str
    name: str
    status: str
    list_id: str
    description: str
    updated: str
    url: str
    status_type: str = ""
    status_color: str = ""

    @classmethod
    def parse(cls, data: Mapping[str, object]) -> Task:
        identifier = text(data.get("id"))
        if not _ID.fullmatch(identifier):
            raise ClickUpError("malformed", "Invalid task identifier")
        custom_id = text(data["custom_id"]) if data.get("custom_id") else ""
        if custom_id and not _ID.fullmatch(custom_id):
            raise ClickUpError("malformed", "Invalid custom task identifier")
        status = Status.parse(obj(data.get("status")))
        return cls(
            identifier,
            custom_id,
            text(data.get("name")),
            status.name,
            text(obj(data.get("list")).get("id")),
            text(
                data.get("markdown_description") or data.get("description") or "",
                multiline=True,
            ),
            text(data.get("date_updated") or ""),
            text(data.get("url")),
            status.kind,
            status.color,
        )


def validated_url(task: Task) -> str:
    parsed = urllib.parse.urlsplit(task.url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "app.clickup.com"
        or parsed.path != f"/t/{task.id}"
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in task.url)
    ):
        raise ClickUpError(
            "malformed", "Task link is not a canonical ClickUp HTTPS deep link"
        )
    return task.url


def open_task(task: Task, credential_env: str = "CLICKUP_API_TOKEN") -> None:
    url = validated_url(task)
    opener = os.environ.get("BROWSER")
    if opener:
        if not Path(opener).is_absolute() or not os.access(opener, os.X_OK):
            raise ValueError(
                "BROWSER must name one absolute executable, without arguments"
            )
    else:
        opener = (
            "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
        )
    if not opener:
        raise ValueError("No browser opener is available")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name != credential_env and not name.startswith("CLICKUP_")
    }
    subprocess.run([opener, url], check=True, timeout=15, env=environment)


@dataclass(frozen=True)
class Comment:
    id: str
    date: str
    body: str
    depth: int = 0


@dataclass(frozen=True)
class Detail:
    task: Task
    comments: tuple[Comment, ...]


@dataclass(frozen=True)
class Listing:
    name: str
    statuses: tuple[Status, ...]
    tasks: tuple[Task, ...]
    pages: int


@dataclass(frozen=True)
class Change:
    selection: Selection
    task: str
    action: str
    value: str
    mutation: receipts.Mutation
    text: str
    nonce: str = dataclass_field(default_factory=lambda: secrets.token_hex(16))

    @property
    def approval(self) -> str:
        return receipts.digest_json(
            {
                "selection": asdict(self.selection),
                "task": self.task,
                "action": self.action,
                "value": self.value,
                "diff": self.mutation.diff,
                "field_digest": self.mutation.field_digest,
                "nonce": self.nonce,
            }
        )


class Service:
    def __init__(self, selection: Selection) -> None:
        self.selection = selection
        self.client = Client(selection)

    def authorize(self) -> Mapping[str, object]:
        selected = self.selection
        if (
            text(obj(self.client.get("/user").get("user")).get("id"))
            != selected.account
        ):
            raise ClickUpError(
                "account-mismatch", "Credential account differs from explicit selection"
            )
        teams = rows(self.client.get("/team").get("teams"))
        if selected.team not in {text(team.get("id")) for team in teams}:
            raise ClickUpError(
                "permission", "Selected workspace is not accessible to this account"
            )
        metadata = self.client.get(f"/list/{selected.list}")
        space = text(obj(metadata.get("space")).get("id"))
        spaces = rows(
            self.client.get(f"/team/{selected.team}/space", {"archived": "false"}).get(
                "spaces"
            )
        )
        if space not in {text(item.get("id")) for item in spaces}:
            raise ClickUpError(
                "permission", "Selected list does not belong to the selected workspace"
            )
        return metadata

    def listing(self) -> Listing:
        metadata = self.authorize()
        page_count = 0

        def observed_page() -> None:
            nonlocal page_count
            page_count += 1

        payloads = reads.paged_tasks(
            self.client,
            f"/list/{self.selection.list}/task",
            {"include_closed": "true", "subtasks": "true", "include_timl": "true"},
            on_page=observed_page,
        )
        tasks = {task.id: task for task in map(Task.parse, payloads)}
        return Listing(
            text(metadata.get("name")),
            tuple(Status.parse(item) for item in rows(metadata.get("statuses"))),
            tuple(tasks.values()),
            page_count,
        )

    def detail(self, task: Task, identity: str) -> Detail:
        custom = identity == task.custom_id and bool(task.custom_id)
        query = {"include_markdown_description": "true"}
        if custom:
            query.update(custom_task_ids="true", team_id=self.selection.team)
        result = Task.parse(
            self.client.get(f"/task/{identity if custom else task.id}", query)
        )
        if result.id != task.id:
            raise ClickUpError(
                "account-mismatch", "Task response differs from selected list member"
            )
        comments = tuple(
            Comment(
                text(item.get("id")),
                text(item.get("date")),
                text(item.get("comment_text") or "", multiline=True)
                if item.get("comment_text")
                else "".join(
                    text(part.get("text") or "", multiline=True)
                    for part in rows(item.get("comment") or [])
                ),
                depth,
            )
            for item, depth in reads.comment_tree(self.client, result.id)
        )
        return Detail(result, comments)

    def registry_path(self) -> Path:
        selected = self.selection
        return registry.registry_path(
            account_id=selected.account, team_id=selected.team
        ).with_name(f"list-{selected.list}.yaml")

    def _session(self, **changes: object) -> runtime.Session:
        selected = self.selection
        loaded = registry.load(self.registry_path())
        if (
            loaded.team_id != selected.team
            or loaded.seeds.me_id != selected.account
            or loaded.seeds.list.id != selected.list
        ):
            raise ClickUpError(
                "account-mismatch", "Engine registry differs from explicit selection"
            )
        try:
            cache_root = settings.xdg_path("XDG_CACHE_HOME", Path.home() / ".cache")
        except RuntimeError as error:
            raise ClickUpError("invalid-selection", str(error)) from None
        cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        cache_directory = cache_root
        for component in (
            "rexdeck",
            "clickup",
            selected.account,
            selected.team,
            selected.list,
        ):
            cache_directory /= component
            cache_directory.mkdir(mode=0o700, exist_ok=True)
            cache_directory.chmod(0o700)
        arguments: dict[str, object] = {
            "no_cache": False,
            "registry_path": self.registry_path(),
            "cache_path": cache_directory / "cache.sqlite3",
            "list": selected.list,
            "list_ref": selected.list,
            "assignee": None,
            "status": None,
            "due_before": None,
            "max_rows": sys.maxsize,
            "task_id": "",
            "no_comments": False,
            "query": "",
            "type": None,
            "due": None,
            "field": None,
            "expect_digest": None,
            "dry_run": True,
            "text": "",
        }
        arguments.update(changes)
        return runtime.Session(
            argparse.Namespace(**arguments),
            client=self.client,
            registry=loaded,
            registry_path=self.registry_path(),
            cache_path=cast(Path, arguments["cache_path"]),
            token_env=selected.credential_env,
        )

    @staticmethod
    def _run(
        session: runtime.Session, verb: Callable[[runtime.Session], runtime.Outcome]
    ) -> runtime.Outcome:
        try:
            return verb(session)
        finally:
            if session.connection is not None:
                session.connection.close()

    def sync(self) -> str:
        metadata = self.authorize()
        selected = self.selection
        space = obj(metadata.get("space"))
        seed = registry.seed_registry(
            account_id=selected.account,
            team_id=selected.team,
            space_id=text(space.get("id")),
            space_name=text(space.get("name") or "Selected space"),
            list_id=selected.list,
            list_name=text(metadata.get("name")),
        )
        session = runtime.Session(
            argparse.Namespace(write_registry=True, registry_path=self.registry_path()),
            client=self.client,
            registry=seed,
            registry_path=self.registry_path(),
            token_env=selected.credential_env,
        )
        return clean(self._run(session, reads.verb_sync).text)

    def read(self, command: str, argument: str, task: str) -> str:
        self.authorize()
        verb = {
            "tasks": reads.verb_tasks,
            "task": reads.verb_task,
            "sprint": reads.verb_sprint,
            "search": reads.verb_search,
        }[command]
        result = self._run(self._session(query=argument, task_id=task), verb)
        return clean(result.text) + "".join(
            f"\n{finding.code}: {finding.message}" for finding in result.diagnostics
        )

    def mutate(
        self, task: Task, action: str, value: str, digest: str | None = None
    ) -> runtime.Outcome:
        self.authorize()
        if action == "status":
            metadata = self.client.get(f"/list/{task.list_id}")
            statuses = tuple(
                Status.parse(item) for item in rows(metadata.get("statuses"))
            )
            if value not in {status.name for status in statuses}:
                raise ValueError("Use an exact native status from the task's home list")
        changes: dict[str, object] = {
            "task_id": task.id,
            "expect_digest": digest,
            "dry_run": digest is None,
        }
        if action == "comment":
            changes["text"] = value
            verb = writes.verb_comment
        else:
            changes[action] = [value] if action == "field" else value
            verb = writes.verb_touch
        return self._run(self._session(**changes), verb)


@dataclass(frozen=True)
class View:
    selection: Selection
    state: str = "loading"
    error: str = ""
    error_state: str = ""
    listing: Listing | None = None
    detail: Detail | None = None
    revision: int = 0
    pending: Change | None = None
    output: str = ""


class _ViewChanges(TypedDict, total=False):
    state: str
    error: str
    error_state: str
    listing: Listing | None
    detail: Detail | None
    pending: Change | None
    output: str


class Controller:
    """Each completion carries selection/list/detail generations, never a global cache."""

    def __init__(self, selection: Selection) -> None:
        self.lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.generation = 0
        self.request = 0
        self.view = View(selection)
        self.service = Service(selection)

    def snapshot(self) -> View:
        with self.lock:
            return self.view

    def _set(self, **changes: Unpack[_ViewChanges]) -> None:
        self.view = replace(self.view, **changes, revision=self.view.revision + 1)

    def switch(
        self, selection: Selection
    ) -> concurrent.futures.Future[tuple[Listing, Detail | None]]:
        with self.lock:
            self.generation += 1
            self.request += 1
            self.service = Service(selection)
            self.view = View(selection, revision=self.view.revision + 1)
            return self.refresh()

    def refresh(self) -> concurrent.futures.Future[tuple[Listing, Detail | None]]:
        with self.lock:
            if self.view.state == "writing":
                raise ValueError(
                    "Wait for the write outcome before refreshing; it is not retried"
                )
            self.request += 1
            tag = self.generation, self.request
            service = self.service
            previous = self.view.detail
            self._set(
                state="loading", error="", error_state="", pending=None, output=""
            )

            def load() -> tuple[Listing, Detail | None]:
                listing = service.listing()
                task = next(
                    (
                        item
                        for item in listing.tasks
                        if previous and item.id == previous.task.id
                    ),
                    None,
                )
                return listing, service.detail(task, task.id) if task else None

            future = self.pool.submit(load)

            def complete(
                done: concurrent.futures.Future[tuple[Listing, Detail | None]],
            ) -> None:
                with self.lock:
                    if tag != (self.generation, self.request):
                        return
                    try:
                        listing, detail = done.result()
                        self._set(
                            listing=listing,
                            detail=detail,
                            state="ready" if listing.tasks else "empty",
                        )
                    except (
                        ClickUpError,
                        diagnostics.EngineError,
                        ValueError,
                        OSError,
                    ) as error:
                        self._error(error)

            future.add_done_callback(complete)
            return future

    def _error(
        self,
        error: ClickUpError | diagnostics.EngineError | ValueError | OSError,
        *,
        fresh: bool = False,
    ) -> None:
        if isinstance(error, diagnostics.EngineError):
            error = engine_error(error)
        elif not isinstance(error, ClickUpError):
            error = ClickUpError("invalid", clean(str(error)))
        self._set(pending=None, output="")
        if error.state in {
            "account-mismatch",
            "authentication",
            "permission",
            "missing-credential",
        }:
            self._set(
                listing=None,
                detail=None,
                state=error.state,
                error_state=error.state,
                error=str(error),
            )
        else:
            self._set(
                state=(
                    ("ready" if self.view.listing.tasks else "empty")
                    if fresh and self.view.listing is not None
                    else ("stale" if self.view.listing else error.state)
                ),
                error_state=error.state,
                error=str(error),
            )

    def select(self, identity: str) -> concurrent.futures.Future[Detail]:
        with self.lock:
            if not identity.strip():
                raise ValueError("Select a nonempty internal/custom ID")
            if self.view.state != "ready":
                raise ValueError("Wait for a fresh loaded list before selecting a task")
            listing = self.view.listing
            task = (
                next(
                    (
                        t
                        for t in listing.tasks
                        if identity == t.id
                        or (bool(t.custom_id) and identity == t.custom_id)
                    ),
                    None,
                )
                if listing
                else None
            )
            if task is None:
                raise ValueError("Select an internal/custom ID from the loaded list")
            self.request += 1
            tag = self.generation, self.request
            self._set(
                state="loading",
                detail=None,
                error="",
                error_state="",
                pending=None,
                output="",
            )
            future = self.pool.submit(self.service.detail, task, identity)

            def complete(done: concurrent.futures.Future[Detail]) -> None:
                with self.lock:
                    if tag != (self.generation, self.request):
                        return
                    try:
                        self._set(detail=done.result(), state="ready")
                    except (
                        ClickUpError,
                        diagnostics.EngineError,
                        ValueError,
                        OSError,
                    ) as error:
                        self._error(error)

            future.add_done_callback(complete)
            return future

    def inspect(
        self, command: str, argument: str = ""
    ) -> concurrent.futures.Future[str]:
        with self.lock:
            if self.view.state not in {"ready", "empty"}:
                raise ValueError(
                    "Wait for a fresh list before running engine reads or sync"
                )
            if command == "task" and self.view.detail is None:
                raise ValueError("Select a task first")
            self.request += 1
            tag = self.generation, self.request
            service = self.service
            task_id = self.view.detail.task.id if self.view.detail else ""
            self._set(
                state="loading", pending=None, output="", error="", error_state=""
            )
            future = self.pool.submit(
                service.sync
                if command == "sync"
                else lambda: service.read(command, argument, task_id)
            )

            def complete(done: concurrent.futures.Future[str]) -> None:
                with self.lock:
                    if tag != (self.generation, self.request):
                        return
                    try:
                        self._set(
                            output=done.result(),
                            state="ready"
                            if self.view.listing and self.view.listing.tasks
                            else "empty",
                        )
                    except (
                        ClickUpError,
                        diagnostics.EngineError,
                        ValueError,
                        OSError,
                    ) as error:
                        self._error(error, fresh=True)

            future.add_done_callback(complete)
            return future

    def stage(
        self, action: str, value: str
    ) -> concurrent.futures.Future[runtime.Outcome]:
        with self.lock:
            if action not in {"status", "assignee", "due", "field", "comment"}:
                raise ValueError("Unsupported task change")
            value = clean(value)
            if not value.strip():
                raise ValueError("A proposed value is required")
            if self.view.state != "ready" or self.view.detail is None:
                raise ValueError("Select a fresh task before proposing a change")
            self.request += 1
            tag = self.generation, self.request
            service, task = self.service, self.view.detail.task
            self._set(
                state="loading", pending=None, output="", error="", error_state=""
            )
            future = self.pool.submit(service.mutate, task, action, value)

            def complete(done: concurrent.futures.Future[runtime.Outcome]) -> None:
                with self.lock:
                    if tag != (self.generation, self.request):
                        return
                    try:
                        outcome = done.result()
                        mutation = outcome.mutation
                        if mutation is None or mutation.target != task.id:
                            raise ValueError(
                                "Engine returned an unexpected mutation target"
                            )
                        self._set(
                            state="ready",
                            pending=Change(
                                service.selection,
                                task.id,
                                action,
                                value,
                                mutation,
                                clean(outcome.text),
                            ),
                        )
                    except (
                        ClickUpError,
                        diagnostics.EngineError,
                        ValueError,
                        OSError,
                    ) as error:
                        self._error(error, fresh=True)

            future.add_done_callback(complete)
            return future

    def cancel(self) -> None:
        with self.lock:
            if self.view.state == "writing":
                raise ValueError(
                    "A dispatched write cannot be cancelled; wait for its outcome"
                )
            if self.view.state != "ready" or self.view.pending is None:
                raise ValueError(
                    "No pending proposal; refresh or wait for current work first"
                )
            self.request += 1
            self._set(
                pending=None,
                output="Proposal cancelled",
                state="ready"
                if self.view.listing and self.view.listing.tasks
                else "empty",
            )

    def confirm(
        self, identity: str, approval: str
    ) -> concurrent.futures.Future[tuple[Listing, Detail, str]]:
        with self.lock:
            change, detail = self.view.pending, self.view.detail
            if (
                self.view.state != "ready"
                or change is None
                or detail is None
                or change.selection != self.view.selection
                or change.task != detail.task.id
                or identity != change.task
                or approval != change.approval
            ):
                raise ValueError(
                    "Confirmation must match the current selection, task and exact proposal code"
                )
            self.request += 1
            tag = self.generation, self.request
            service, task = self.service, detail.task
            self._set(
                state="writing",
                pending=None,
                output="Write dispatched once; no automatic retries",
                error="",
                error_state="",
            )

            def guard(path: str) -> None:
                with self.lock:
                    selected = self.view.detail
                    field_path = path.removeprefix(f"/task/{task.id}/field/")
                    allowed = path in {
                        f"/task/{task.id}",
                        f"/task/{task.id}/comment",
                    } or (
                        path.startswith(f"/task/{task.id}/field/")
                        and bool(_ID.fullmatch(field_path))
                    )
                    if (
                        not allowed
                        or tag != (self.generation, self.request)
                        or self.view.selection != change.selection
                        or selected is None
                        or selected.task.id != change.task
                    ):
                        raise ClickUpError(
                            "unconfirmed", "Selected-task approval is no longer current"
                        )

            def apply() -> tuple[Listing, Detail, str]:
                writer = Service(change.selection)
                writer.client.write_guard = guard
                try:
                    outcome = writer.mutate(
                        task, change.action, change.value, change.mutation.field_digest
                    )
                finally:
                    writer.client.close()
                listing = service.listing()
                member = next(
                    (item for item in listing.tasks if item.id == task.id), None
                )
                if member is None:
                    raise ClickUpError(
                        "not-found", "Write sent; task is absent from refreshed list"
                    )
                return listing, service.detail(member, member.id), clean(outcome.text)

            future = self.pool.submit(apply)

            def complete(
                done: concurrent.futures.Future[tuple[Listing, Detail, str]],
            ) -> None:
                with self.lock:
                    if tag != (self.generation, self.request):
                        return
                    try:
                        listing, refreshed, output = done.result()
                        self._set(
                            state="ready",
                            listing=listing,
                            detail=refreshed,
                            output=output
                            + "\nRefreshed task and comments after the write",
                        )
                    except (
                        ClickUpError,
                        diagnostics.EngineError,
                        ValueError,
                        OSError,
                    ) as error:
                        self._error(error)
                        self._set(
                            output="Write may have succeeded; it was NOT retried. Refresh before any new proposal."
                        )
                    except Exception as error:  # noqa: BLE001 - outcome is uncertain
                        # Any other failure after dispatch must still end `writing`.
                        self._error(ClickUpError("write-uncertain", clean(repr(error))))
                        self._set(
                            output="Write may have succeeded; it was NOT retried. Refresh before any new proposal."
                        )

            future.add_done_callback(complete)
            return future

    def close(self) -> None:
        with self.lock:
            self.generation += 1
        self.pool.shutdown(wait=False, cancel_futures=True)


def configure(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Explicit nonsecret ClickUp selection")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    for field in ("account", "team", "list"):
        select.add_argument("--" + field, required=True)
    select.add_argument("--credential-env", default="CLICKUP_API_TOKEN")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        selected = (
            Selection(args.account, args.team, args.list, args.credential_env)
            if args.command == "select"
            else load_selection()
        )
        if args.command == "select":
            save_selection(selected)
        print(json.dumps({"state": "selected", **asdict(selected)}))
        return 0
    except (OSError, ValueError, ClickUpError):
        print(
            json.dumps(
                {
                    "state": "invalid-selection",
                    "error": "Cannot load/save explicit nonsecret ClickUp selection",
                }
            ),
            file=sys.stderr,
        )
        return 1


def render(view: View, query: str, page: int) -> None:
    selected = view.selection
    print(
        f"\nClickUp [{view.state.upper()}] account={selected.account} team={selected.team} list={selected.list}"
    )
    if view.error:
        print(f"{view.error_state.upper()}: {view.error}")
    listing = view.listing
    if listing:
        if view.state == "loading":
            print("Previous data retained while refreshing; opener disabled")
        visible = [
            task
            for task in listing.tasks
            if query.casefold()
            in f"{task.name} {task.status} {task.id} {task.custom_id}".casefold()
        ]
        print(
            f"{listing.name}: {len(listing.tasks)} tasks / {listing.pages} API pages; FULL-LIST FILTER {query!r}: {len(visible)} matches; display page {page + 1}"
        )
        print(
            "Native list statuses: "
            + ", ".join(status.label() for status in listing.statuses)
        )
        for task in visible[page * 20 : (page + 1) * 20]:
            print(f"{task.id} {task.custom_id or '-'} [{task.status}] {task.name}")
        if not visible:
            print("EMPTY: no matching tasks")
    detail = view.detail
    if detail:
        task = detail.task
        print(
            f"\nTASK {task.id} custom={task.custom_id or '-'} native status={task.status} (type={task.status_type or 'unavailable'}, color={task.status_color or 'unavailable'}) home-list={task.list_id} updated={task.updated}"
        )
        print(task.name + "\n" + task.description)
        print(
            f"COMMENTS: {len(detail.comments)}; complete paginated history and replies"
        )
        for comment in detail.comments:
            print(f"{'  ' * comment.depth}[{comment.id} {comment.date}] {comment.body}")
    if view.output:
        print("\nENGINE:\n" + view.output)
    if view.pending is not None:
        change = view.pending
        print(
            f"\nPROPOSED {change.action}: account={change.selection.account} "
            f"team={change.selection.team} list={change.selection.list} task={change.task}"
        )
        print(change.text)
        print("EXACT DIFF: " + json.dumps(change.mutation.diff, sort_keys=True))
        print(f"Field concurrency digest: {change.mutation.field_digest}")
        print(f"To apply ONCE: confirm {change.task} {change.approval}")
        print("Selection, refresh or cancel discards this approval")
    print(
        "Commands: next | prev | filter TEXT | select INTERNAL/CUSTOM-ID | open | refresh | "
        "sync | tasks | task | sprint | search TEXT | status NATIVE-STATUS | assignee ID/NAME | "
        "due YYYY-MM-DD | field NAME=VALUE | comment TEXT | confirm TASK-ID PROPOSAL-CODE | cancel | "
        "switch ACCOUNT TEAM LIST [ENV-NAME] | quit",
        flush=True,
    )


def main() -> int:
    try:
        selected = load_selection()
    except (OSError, ValueError, ClickUpError):
        print(
            "ClickUp [INVALID-SELECTION]: run rexdeck clickup select --account ID --team ID --list ID",
            flush=True,
        )
        return 1
    controller = Controller(selected)
    query, page, revision = "", 0, -1
    controller.refresh()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin, selectors.EVENT_READ)
            while True:
                view = controller.snapshot()
                if view.revision != revision:
                    render(view, query, page)
                    revision = view.revision
                if not selector.select(0.1):
                    continue
                line = sys.stdin.readline()
                if not line:
                    break
                command, _, argument = line.strip().partition(" ")
                try:
                    if command in ("quit", "q"):
                        break
                    if command == "switch":
                        parts = argument.split()
                        if len(parts) not in (3, 4):
                            raise ValueError(
                                "switch requires ACCOUNT TEAM LIST [ENV-NAME]"
                            )
                        selection = Selection(*parts)
                        # This is a pane-local switch, not a silent rewrite of saved selection.
                        controller.switch(selection)
                        query, page = "", 0
                        print(
                            "\x1b[3J\x1b[2J\x1b[HSelection changed: tasks/detail/comments/link cleared",
                            flush=True,
                        )
                    elif command == "refresh":
                        controller.refresh()
                    elif command == "select":
                        controller.select(argument)
                    elif command in {"sync", "tasks", "task", "sprint", "search"}:
                        controller.inspect(command, argument)
                    elif command in {"status", "assignee", "due", "field", "comment"}:
                        controller.stage(command, argument)
                    elif command == "confirm":
                        parts = argument.split()
                        if len(parts) != 2:
                            raise ValueError(
                                "confirm requires the exact TASK-ID and PROPOSAL-CODE shown"
                            )
                        controller.confirm(*parts)
                    elif command == "cancel":
                        controller.cancel()
                    elif command == "open":
                        if view.state != "ready" or view.detail is None:
                            raise ValueError(
                                "Select a fresh task first; no opener target retained"
                            )
                        open_task(view.detail.task, view.selection.credential_env)
                        print("Opened validated task deep link", flush=True)
                    elif command == "filter":
                        query, page = clean(argument), 0
                        render(view, query, page)
                    elif command in ("next", "prev"):
                        page = max(0, page + (1 if command == "next" else -1))
                        render(view, query, page)
                    elif command:
                        raise ValueError("Unknown ClickUp command")
                except (
                    ValueError,
                    ClickUpError,
                    OSError,
                    subprocess.SubprocessError,
                ) as error:
                    print("COMMAND ERROR: " + clean(str(error)), flush=True)
    except KeyboardInterrupt:
        return 0
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Identity-bound ClickUp read/write pane backed by its vendored MIT engine."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import contextvars
import getpass
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
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
WRITE_ACTIONS = frozenset(
    {"status", "assignee", "due", "description", "points", "field", "comment"}
)
_READ_CANCELLED: contextvars.ContextVar[Callable[[], bool] | None] = (
    contextvars.ContextVar("clickup_read_cancelled", default=None)
)


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


def sprint_points(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    raise ClickUpError("malformed", "Sprint Points is not a number")


@dataclass(frozen=True)
class Selection:
    account: str
    team: str
    list: str
    credential_env: str = "CLICKUP_API_TOKEN"
    space: str = ""

    def __post_init__(self) -> None:
        if not all(
            v.isascii() and v.isdecimal() for v in (self.account, self.team, self.list)
        ):
            raise ValueError("Account, team and list must be explicit numeric IDs")
        credentials.validate_env_name(self.credential_env)
        if self.space and not (self.space.isascii() and self.space.isdecimal()):
            raise ValueError("Space must be an explicit numeric ID")


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
            json.dump(
                {
                    key: value
                    for key, value in asdict(selection).items()
                    if key != "space" or value
                },
                stream,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_selection() -> Selection:
    path = selection_path()
    if path.is_symlink() or not path.is_file():
        raise ValueError("No ClickUp selection; run rexdeck clickup select")
    if path.stat().st_size > 16384:
        raise ValueError("ClickUp selection is too large")
    document = obj(json.loads(path.read_text()))
    required = {"account", "team", "list", "credential_env"}
    if not required <= set(document) or set(document) - required - {"space"}:
        raise ValueError("Invalid ClickUp selection fields")
    return Selection(
        account=text(document["account"]),
        team=text(document["team"]),
        list=text(document["list"]),
        credential_env=text(document["credential_env"]),
        space=text(document.get("space", "")),
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
            "request/rejected": "rejected",
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
        cancelled = _READ_CANCELLED.get()
        if method == "GET" and cancelled is not None and cancelled():
            raise concurrent.futures.CancelledError()
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


@dataclass(frozen=True)
class Assignee:
    id: str
    name: str


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
    points: str = "-"
    assignees: tuple[Assignee, ...] = ()
    due: str = ""
    priority: str = ""
    list_name: str = ""

    @classmethod
    def parse(cls, data: Mapping[str, object]) -> Task:
        identifier = text(data.get("id"))
        if not _ID.fullmatch(identifier):
            raise ClickUpError("malformed", "Invalid task identifier")
        custom_id = text(data["custom_id"]) if data.get("custom_id") else ""
        if custom_id and not _ID.fullmatch(custom_id):
            raise ClickUpError("malformed", "Invalid custom task identifier")
        status = Status.parse(obj(data.get("status")))
        list_id = text(obj(data.get("list")).get("id"))
        if not (list_id.isascii() and list_id.isdecimal()):
            raise ClickUpError("malformed", "Invalid task list identifier")
        return cls(
            identifier,
            custom_id,
            text(data.get("name")),
            status.name,
            list_id,
            text(
                data.get("markdown_description") or data.get("description") or "",
                multiline=True,
            ),
            text(data.get("date_updated") or ""),
            text(data.get("url")),
            status.kind,
            status.color,
            sprint_points(data.get("points")),
            tuple(
                Assignee(
                    text(item.get("id")),
                    text(item.get("username") or ""),
                )
                for item in rows(data.get("assignees") or [])
            ),
            text(data["due_date"]) if data.get("due_date") is not None else "",
            text(obj(data["priority"]).get("priority") or "")
            if data.get("priority")
            else "",
            text(obj(data.get("list")).get("name") or ""),
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
    author: str = ""


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
    workspace_name: str = ""
    space_name: str = ""
    filter_label: str = ""


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str


@dataclass(frozen=True)
class ListChoice:
    id: str
    name: str
    space_id: str
    space_name: str
    folder_name: str = ""
    folder_id: str = ""


@dataclass(frozen=True)
class Change:
    selection: Selection
    task: str
    action: str
    value: str
    mutation: receipts.Mutation
    text: str
    task_list: str = ""
    nonce: str = dataclass_field(default_factory=lambda: secrets.token_hex(16))

    @property
    def approval(self) -> str:
        return receipts.digest_json(
            {
                "selection": {
                    key: value
                    for key, value in asdict(self.selection).items()
                    if key != "space" or self.task_list
                },
                "task": self.task,
                "action": self.action,
                "value": self.value,
                "diff": self.mutation.diff,
                "field_digest": self.mutation.field_digest,
                "nonce": self.nonce,
                **({"task_list": self.task_list} if self.task_list else {}),
            }
        )


@dataclass(frozen=True)
class WorkspaceFilters:
    assignee: str = "me"
    lookback: str = "14d"

    def __post_init__(self) -> None:
        if self.assignee not in {"me", "any"} and not (
            self.assignee.isascii() and self.assignee.isdecimal()
        ):
            raise ValueError("Assignee must be me, any or a numeric member ID")
        if self.lookback not in {"1d", "7d", "14d", "30d", "any"}:
            raise ValueError("Lookback must be 1d, 7d, 14d, 30d or any")


class Service:
    def __init__(
        self,
        selection: Selection,
        *,
        workspace: bool | None = None,
        filters: WorkspaceFilters | None = None,
    ) -> None:
        self.selection = selection
        self.workspace = bool(selection.space) if workspace is None else workspace
        if self.workspace and not selection.space:
            raise ValueError("Select a space before entering workspace mode")
        self.filters = filters or WorkspaceFilters()
        self.client = Client(selection)

    def workspaces(self) -> tuple[Workspace, ...]:
        """Enumerate only workspaces visible to the explicitly bound account."""
        account = text(obj(self.client.get("/user").get("user")).get("id"))
        if account != self.selection.account:
            raise ClickUpError(
                "account-mismatch", "Credential account differs from explicit selection"
            )
        return tuple(
            Workspace(text(item.get("id")), text(item.get("name") or ""))
            for item in rows(self.client.get("/team").get("teams"))
        )

    def hierarchy(self, team_id: str | None = None) -> tuple[ListChoice, ...]:
        """Read foldered and folderless lists in an accessible workspace."""
        team = self.selection.team if team_id is None else team_id
        if team not in {workspace.id for workspace in self.workspaces()}:
            raise ClickUpError(
                "permission", "Selected workspace is not accessible to this account"
            )
        unarchived = {"archived": "false"}
        choices: dict[str, ListChoice] = {}
        for space in rows(
            self.client.get(f"/team/{team}/space", unarchived).get("spaces")
        ):
            if space.get("archived"):
                continue
            space_id = text(space.get("id"))
            space_name = text(space.get("name") or "")
            for folder in rows(
                self.client.get(f"/space/{space_id}/folder", unarchived).get("folders")
            ):
                if folder.get("archived"):
                    continue
                for entry in rows(folder.get("lists") or []):
                    if not entry.get("archived"):
                        choice = ListChoice(
                            text(entry.get("id")),
                            text(entry.get("name") or ""),
                            space_id,
                            space_name,
                            text(folder.get("name") or ""),
                            text(folder.get("id")),
                        )
                        choices[choice.id] = choice
            for entry in rows(
                self.client.get(f"/space/{space_id}/list", unarchived).get("lists")
            ):
                if not entry.get("archived"):
                    choice = ListChoice(
                        text(entry.get("id")),
                        text(entry.get("name") or ""),
                        space_id,
                        space_name,
                    )
                    choices[choice.id] = choice
        return tuple(choices.values())

    def task_statuses(self, task: Task) -> tuple[Status, ...]:
        """Read exact native statuses from this task's home list."""
        if self.workspace:
            metadata = self.authorize(task)
        else:
            self.authorize()
            metadata = self.client.get(f"/list/{task.list_id}")
        return tuple(Status.parse(item) for item in rows(metadata.get("statuses")))

    def authorize(self, task: Task | None = None) -> Mapping[str, object]:
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
        metadata = self.client.get(f"/list/{task.list_id if task else selected.list}")
        if self.workspace and task is not None:
            self._check_task_space(metadata)
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
        if self.workspace and selected.space not in {
            text(item.get("id")) for item in spaces
        }:
            raise ClickUpError("permission", "Selected space is outside the workspace")
        return metadata

    def _check_task_space(self, metadata: Mapping[str, object]) -> None:
        if text(obj(metadata.get("space")).get("id")) != self.selection.space:
            raise ClickUpError(
                "task-scope",
                "Task list is outside the selected space; refresh the queue",
            )

    def listing(self) -> Listing:
        metadata = self.authorize()
        workspace_name = next(
            (
                workspace.name
                for workspace in self.workspaces()
                if workspace.id == self.selection.team
            ),
            "",
        )
        page_count = 0

        def observed_page() -> None:
            nonlocal page_count
            page_count += 1

        path = f"/list/{self.selection.list}/task"
        params: backend.Params = {
            "include_closed": "true",
            "subtasks": "true",
            "include_timl": "true",
        }
        if self.workspace:
            path = f"/team/{self.selection.team}/task"
            params = {
                "include_closed": "true",
                "subtasks": "true",
                "space_ids[]": [self.selection.space],
            }
            if self.filters.assignee != "any":
                params["assignees[]"] = [
                    self.selection.account
                    if self.filters.assignee == "me"
                    else self.filters.assignee
                ]
            if self.filters.lookback != "any":
                days = int(self.filters.lookback.removesuffix("d"))
                params["date_updated_gt"] = str(
                    int((time.time() - days * 86400) * 1000)
                )
        payloads = reads.paged_tasks(
            self.client,
            path,
            params,
            on_page=observed_page,
        )
        tasks = {task.id: task for task in map(Task.parse, payloads)}
        return Listing(
            f"Space {self.selection.space}"
            if self.workspace
            else text(metadata.get("name")),
            ()
            if self.workspace
            else tuple(Status.parse(item) for item in rows(metadata.get("statuses"))),
            tuple(tasks.values()),
            page_count,
            workspace_name,
            ""
            if self.workspace
            else text(obj(metadata.get("space")).get("name") or ""),
            f"Space {self.selection.space} · assignee {self.filters.assignee} · updated {self.filters.lookback}"
            if self.workspace
            else "",
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
        if self.workspace:
            self._check_task_space(self.client.get(f"/list/{result.list_id}"))
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
                text(obj(item.get("user") or {}).get("username") or ""),
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
            "description": None,
            "points": None,
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
        metadata = self.authorize(task if self.workspace else None)
        if self.workspace:
            current = Task.parse(self.client.get(f"/task/{task.id}"))
            if current.list_id != task.list_id:
                raise ClickUpError(
                    "task-scope", "Task moved lists; refresh before proposing again"
                )
            scoped = Service(
                replace(self.selection, list=task.list_id), workspace=False
            )
            scoped.client.close()
            scoped.client = self.client
            return scoped._mutate(task, action, value, digest, metadata)
        return self._mutate(task, action, value, digest, metadata)

    def _mutate(
        self,
        task: Task,
        action: str,
        value: str,
        digest: str | None,
        metadata: Mapping[str, object],
    ) -> runtime.Outcome:
        if action == "status":
            if task.list_id != self.selection.list:
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

    def read[T](
        self,
        operation: Callable[[Service], T],
        *,
        current_scope: bool,
        cancel: threading.Event | None = None,
    ) -> concurrent.futures.Future[T]:
        """Run dialog reads without hiding authoritative access failures."""
        with self.lock:
            tag = self.generation, self.request
            service = self.service

            def cancelled() -> bool:
                with self.lock:
                    return (cancel is not None and cancel.is_set()) or (
                        tag[0] != self.generation
                    )

            def load() -> T:
                token = _READ_CANCELLED.set(cancelled)
                try:
                    if cancelled():
                        raise concurrent.futures.CancelledError()
                    result = operation(service)
                    if cancelled():
                        raise concurrent.futures.CancelledError()
                    return result
                except (ClickUpError, diagnostics.EngineError) as error:
                    if cancelled():
                        raise concurrent.futures.CancelledError() from None
                    finding = (
                        engine_error(error)
                        if isinstance(error, diagnostics.EngineError)
                        else error
                    )
                    authoritative = finding.state in {
                        "account-mismatch",
                        "authentication",
                        "missing-credential",
                    } or (
                        finding.state == "permission"
                        and (
                            current_scope
                            or (
                                isinstance(error, diagnostics.EngineError)
                                and error.diagnostic.evidence.get("method") == "GET"
                                and error.diagnostic.evidence.get("path")
                                in {"/user", "/team"}
                            )
                        )
                    )
                    with self.lock:
                        if (
                            authoritative
                            and not cancelled()
                            and tag == (self.generation, self.request)
                        ):
                            self.request += 1
                            self._error(finding)
                    raise
                finally:
                    _READ_CANCELLED.reset(token)

            return self.pool.submit(load)

    def _set(self, **changes: Unpack[_ViewChanges]) -> None:
        self.view = replace(self.view, **changes, revision=self.view.revision + 1)

    def switch(
        self, selection: Selection
    ) -> concurrent.futures.Future[tuple[Listing, Detail | None]]:
        return self.switch_workspace(selection)

    def switch_workspace(
        self,
        selection: Selection,
        *,
        workspace: bool | None = None,
        filters: WorkspaceFilters | None = None,
    ) -> concurrent.futures.Future[tuple[Listing, Detail | None]]:
        with self.lock:
            self.generation += 1
            self.request += 1
            self.service = Service(selection, workspace=workspace, filters=filters)
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
            if service.workspace and self.view.detail is not None:
                service = Service(
                    replace(service.selection, list=self.view.detail.task.list_id),
                    workspace=False,
                )
                service.client.close()
                service.client = self.service.client
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
            if action not in WRITE_ACTIONS:
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
                                task_list=task.list_id if service.workspace else "",
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
                or (change.task_list and change.task_list != detail.task.list_id)
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
                writer = Service(
                    change.selection,
                    workspace=service.workspace,
                    filters=service.filters,
                )
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
                    if service.workspace:
                        refreshed = service.detail(task, task.id)
                        return (
                            listing,
                            refreshed,
                            "Applied; task no longer matches the current filters.\n"
                            + clean(outcome.text),
                        )
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
                        # Only the write itself refusing proves nothing applied;
                        # a 400 on the refresh reads after it proves nothing.
                        refused = (
                            isinstance(error, diagnostics.EngineError)
                            and error.diagnostic.code == "request/rejected"
                            and error.diagnostic.evidence.get("method")
                            in {"PUT", "POST"}
                        )
                        self._set(
                            output="ClickUp refused this write; it was not applied and was NOT retried. Refresh before any new proposal."
                            if refused
                            else "Write may have succeeded; it was NOT retried. Refresh before any new proposal."
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
    parser = argparse.ArgumentParser(description="Nonsecret ClickUp selection")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser(
        "select",
        help="Pick account/workspace/list interactively, or pass all three IDs",
    )
    for field in ("account", "team", "list"):
        select.add_argument("--" + field)
    select.add_argument("--credential-env", default="CLICKUP_API_TOKEN")
    select.add_argument("--space")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "select":
        given = [args.account, args.team, args.list]
        if not any(given) and args.space is None:
            return _guided_select(args.credential_env)
        if any(given) and not all(given):
            print(
                json.dumps(
                    {
                        "state": "invalid-selection",
                        "error": "Pass all of --account, --team and --list, or none "
                        "to choose interactively",
                    }
                ),
                file=sys.stderr,
            )
            return 1
    try:
        selected = (
            (
                Selection(
                    args.account,
                    args.team,
                    args.list,
                    args.credential_env,
                    args.space or "",
                )
                if args.account
                else replace(load_selection(), space=args.space)
            )
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


def _guided_select(credential_env: str) -> int:
    """Interactive `select`; refuses without a terminal rather than guessing."""
    if not sys.stdin.isatty():
        print(
            json.dumps(
                {
                    "state": "invalid-selection",
                    "error": "Interactive selection needs a terminal; otherwise pass "
                    "--account, --team and --list",
                }
            ),
            file=sys.stderr,
        )
        return 1
    from rexdeck.clickup import picker

    try:
        picker.guided(credential_env=credential_env)
    except picker.Cancelled:
        print("Cancelled; nothing was saved.")
        return 1
    except (OSError, ValueError, ClickUpError) as error:
        print(f"ClickUp selection failed: {clean(str(error))}", file=sys.stderr)
        return 1
    return 0


_SHELL_MARK = "__REXDECK_CLICKUP_CREDENTIAL__"


def shell_credential(name: str) -> str:
    """Read one exported variable from the user's interactive login shell.

    The app-started Rex server does not run shell startup files, so a token
    exported from `~/.zshrc` never reaches this pane. The value travels over a
    pipe between markers (startup files may print), never through argv or disk.
    """
    credentials.validate_env_name(name)
    shell = os.environ.get("SHELL", "")
    if not Path(shell).is_absolute() or not os.access(shell, os.X_OK):
        return ""
    script = f"printf '%s' {_SHELL_MARK}; /usr/bin/printenv {name}; printf '%s' {_SHELL_MARK}"
    mark = _SHELL_MARK.encode()
    try:
        # A new session keeps the interactive shell off this pane's terminal.
        child = subprocess.Popen(
            [shell, "-i", "-l", "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return ""
    if child.stdout is None:
        return ""
    output = b""
    deadline = time.monotonic() + 15
    try:
        # Stop at the closing marker: a startup-file background job may keep the
        # pipe open long after the shell printed the value.
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            # Startup files may print a lot; a token never needs more than this.
            while output.count(mark) < 2 and len(output) < 1 << 20:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    break
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    break
                output += chunk
    finally:
        child.stdout.close()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # Only a shell that outlived its answer loses its process group.
            with contextlib.suppress(OSError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    parts = output.decode(errors="replace").split(_SHELL_MARK)
    return parts[1].strip() if len(parts) >= 3 else ""


def ensure_credential(name: str) -> bool:
    """Use the shell's exported token, else ask once for this pane; never save it."""
    if os.environ.get(name, "").strip():
        return True
    shell = Path(os.environ.get("SHELL", "shell")).name
    print(f"Looking for {name} in your {shell} startup files...", flush=True)
    token = shell_credential(name)
    if not token and sys.stdin.isatty():
        print(
            f"\nClickUp needs your personal API token ({name}); your shell does not "
            "export it.\nPaste it to use it in this pane only. Nothing is saved.\n"
            f"To skip this next time, add to your shell profile:  export {name}=<token>\n",
            flush=True,
        )
        try:
            token = getpass.getpass("ClickUp token (hidden; empty cancels): ").strip()
        except (EOFError, KeyboardInterrupt):
            token = ""
    if not token:
        print(
            f"ClickUp [MISSING-CREDENTIAL]: {name} is not set. Add "
            f"`export {name}=<token>` to your shell profile, then reopen the pane.",
            flush=True,
        )
        return False
    os.environ[name] = token
    return True


def main() -> int:
    try:
        selected: Selection | None = load_selection()
    except (OSError, ValueError, ClickUpError):
        path = selection_path()
        # A dangling symlink is not a first run: saving through it is refused.
        if path.is_symlink() or path.exists() or not sys.stdin.isatty():
            print(
                "ClickUp [INVALID-SELECTION]: run rexdeck clickup select",
                flush=True,
            )
            return 1
        selected = None
    try:
        ready = ensure_credential(
            selected.credential_env if selected else credentials.TOKEN_ENV
        )
    except KeyboardInterrupt:
        print("Cancelled; no token was read.", flush=True)
        return 1
    if not ready:
        return 1
    if selected is None:
        # First run: choose from the account's own workspaces instead of typing IDs.
        print("No ClickUp selection yet. Choose one now.\n", flush=True)
        from rexdeck.clickup import picker

        try:
            selected = picker.guided()
        except picker.Cancelled:
            print("Cancelled; nothing was saved.", flush=True)
            return 0
        except (OSError, ValueError, ClickUpError) as error:
            print(
                f"ClickUp [{getattr(error, 'state', 'invalid-selection').upper()}]: "
                f"{clean(str(error))}",
                flush=True,
            )
            return 1
    controller = Controller(selected)
    from rexdeck.clickup import tui

    try:
        return tui.run(controller)
    except KeyboardInterrupt:
        return 0
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())

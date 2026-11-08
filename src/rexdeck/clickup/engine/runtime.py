"""What one verb run holds open, and the plumbing both verb modules stand on.

`commands.py` is the shell and the verbs live beside it; this is what they
share. It carries the session a run holds open, the outcome a verb hands back,
the hierarchy walk, and the few helpers a read and a write both need: opening
the registry, the topology names resolve against, one list's custom-field
definitions, and the parsers that reject a malformed ID or date before a
request exists.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import projections, registry
from .cache import open_cache
from .client import Client
from .credentials import TOKEN_ENV, read_token
from .diagnostics import SEVERITY_ERROR, Diagnostic, EngineError
from .receipts import Mutation
from .records import (
    CustomField,
    FieldOption,
    Folder,
    ListRecord,
    Member,
    Registry,
    Space,
    Topology,
)


@dataclass(slots=True)
class Session:
    """What one verb run holds open: the parsed flags, the client, the cache."""

    args: argparse.Namespace
    client: Client | None = None
    registry: Registry | None = None
    connection: sqlite3.Connection | None = None
    registry_path: Path | None = None
    cache_path: Path | None = None
    token_env: str = TOKEN_ENV
    _injected_registry: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._injected_registry = self.registry is not None

    def registry_file(self) -> Path | None:
        """Prefer an explicit session path, then an optional parsed path."""
        if self.registry_path is not None:
            return self.registry_path
        value = getattr(self.args, "registry_path", None)
        if isinstance(value, (str, Path)):
            return Path(value)
        if self.registry is not None:
            return registry.registry_path(
                account_id=self.registry.seeds.me_id, team_id=self.registry.team_id
            )
        return None

    def http(self) -> Client:
        """Return the client, building it — and reading the token — on first use."""
        if self.client is None:
            self.client = Client(read_token(self.token_env))
        return self.client

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        if self.connection is not None:
            self.connection.close()


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a verb produced: the text a human reads, and what the receipt records."""

    text: str
    rows: int
    truncated: bool = False
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)
    #: Set by a write verb and by nothing else: a read has no mutation to record.
    mutation: Mutation | None = None


# ------------------------------------------------------------------------- the walk


def discover(session: Session, loaded: Registry) -> Topology:
    """Walk only the explicitly selected seed space, never task content."""
    client = session.http()
    space_id = loaded.seeds.space.id
    space = client.get(f"/space/{space_id}")
    folders: list[Folder] = []
    lists: list[ListRecord] = []
    for row in rows(
        client.get(f"/space/{space_id}/folder"),
        "folders",
        where=f"/space/{space_id}/folder",
    ):
        folder_id = str(row["id"])
        folders.append(Folder(id=folder_id, name=str(row["name"]), space_id=space_id))
        lists.extend(
            ListRecord(
                id=str(entry["id"]),
                name=str(entry["name"]),
                space_id=space_id,
                folder_id=folder_id,
            )
            for entry in rows(row, "lists", where=f"/space/{space_id}/folder")
        )
    lists.extend(
        ListRecord(
            id=str(row["id"]), name=str(row["name"]), space_id=space_id, folder_id=None
        )
        for row in rows(
            client.get(f"/space/{space_id}/list"),
            "lists",
            where=f"/space/{space_id}/list",
        )
    )
    return Topology(
        spaces=(
            Space(id=space_id, name=str(space.get("name", loaded.seeds.space.name))),
        ),
        folders=tuple(folders),
        lists=tuple(lists),
        members=_members(client, loaded.team_id),
        custom_fields={record.id: _fetch_fields(client, record.id) for record in lists},
    )


def _members(client: Client, team_id: str) -> tuple[Member, ...]:
    """Return the workspace's members as ID and username, and nothing else.

    No email address is read off the payload, so none can reach the local
    file by way of a later field being added here.
    """
    for team in rows(client.get("/team"), "teams", where="/team"):
        if str(team.get("id")) != team_id:
            continue
        found: list[Member] = []
        for row in rows(team, "members", where="/team"):
            user = row.get("user")
            if not isinstance(user, Mapping):
                continue
            found.append(Member(id=str(user["id"]), username=str(user["username"])))
        return tuple(found)
    raise ValueError(f"the token has no access to team {team_id}")


def _fetch_fields(client: Client, list_id: str) -> tuple[CustomField, ...]:
    """Fetch one list's custom-field definitions from ClickUp."""
    return tuple(
        CustomField(
            id=str(row["id"]),
            name=str(row["name"]),
            type=str(row["type"]),
            options=tuple(
                FieldOption(
                    id=str(option["id"]),
                    name=str(option.get("name", option.get("label", ""))),
                    orderindex=_optional_index(option.get("orderindex")),
                )
                for option in rows(
                    row.get("type_config"), "options", where=f"/list/{list_id}/field"
                )
            ),
        )
        for row in rows(
            client.get(f"/list/{list_id}/field"),
            "fields",
            where=f"/list/{list_id}/field",
        )
    )


def _optional_index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return int(value)


# ------------------------------------------------------------------------- plumbing


def prepare(session: Session) -> Registry:
    """Load the registry, check it is live, and fill the derived cache.

    The liveness check runs before any request is built, so a stale or unwalked
    registry costs nothing and never resolves a name against a file that has
    drifted from the workspace. `--no-cache` leaves the connection unopened,
    which is what makes every later lookup go live.
    """
    loaded = session.registry
    if loaded is None:
        loaded = registry.load(session.registry_file())
    session.registry = loaded
    registry.check_fresh(loaded)
    if not session.args.no_cache and session.connection is None:
        session.connection = open_cache(
            schema_version=registry.CACHE_SCHEMA_VERSION,
            schema=registry.CACHE_SCHEMA,
            path=session.cache_path,
            account_id=loaded.seeds.me_id,
            team_id=loaded.team_id,
        )
        registry.fill_cache(session.connection, loaded.topology)
    return loaded


def topology(session: Session, loaded: Registry) -> Topology:
    """Use an injected selection's topology; CLI no-cache may resolve live."""
    if session.args.no_cache and not session._injected_registry:
        return discover(session, loaded)
    return loaded.topology


def fields_for(session: Session, list_id: str | None) -> tuple[CustomField, ...]:
    """Return one list's custom-field definitions, cached or live under `--no-cache`."""
    if list_id is None:
        return ()
    if session._injected_registry and session.registry is not None:
        fields = session.registry.topology.custom_fields.get(list_id)
        if fields is not None:
            return fields
    if session.connection is None:
        return _fetch_fields(session.http(), list_id)
    return registry.cached_fields(session.connection, list_id)


def rows(payload: object, key: str, *, where: str) -> list[Mapping[str, object]]:
    """Read a list of objects off a response, refusing a shape that is not one.

    `where` is the path the payload came off, so the finding names the endpoint
    whose shape changed rather than leaving the caller to guess which of a
    walk's seven requests it was.
    """
    if not isinstance(payload, Mapping):
        return []
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise EngineError(
            projections.unexpected_shape(
                key, expected="a list", found=value, where=where
            )
        )
    return [row for row in value if isinstance(row, dict)]


def task_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or not all(
        character.isascii() and character.isalnum() for character in cleaned
    ):
        raise EngineError(
            Diagnostic(
                code="usage/bad-id",
                severity=SEVERITY_ERROR,
                message="the task ID is not a ClickUp task ID",
                subject=value,
                evidence={"given": value},
                supported_fixes=("<task-id>",),
            ),
            exit_code=2,
        )
    return cleaned


def epoch_ms(value: str, *, fix: str) -> int:
    """Parse an ISO-8601 calendar date, naming the flag that carried it.

    The fix is passed in because the same parser now serves `--due-before`,
    `--due` and a date custom field, and a diagnostic that named the wrong flag
    would send the caller to edit an argument they did not pass.
    """
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise EngineError(
            Diagnostic(
                code="usage/bad-date",
                severity=SEVERITY_ERROR,
                message="the date is not an ISO-8601 calendar date",
                subject=value,
                evidence={"given": value, "expected": "YYYY-MM-DD"},
                supported_fixes=(fix,),
            ),
            exit_code=2,
        ) from error
    return int(parsed.timestamp() * 1000)

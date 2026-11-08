"""Local topology registry, digest guards, name resolution and derived cache."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

import yaml

from .diagnostics import SEVERITY_ERROR, Diagnostic, EngineError
from .receipts import RegistryStamp, digest_json
from .records import (
    EMPTY_TOPOLOGY,
    CustomField,
    FieldOption,
    Folder,
    ListRecord,
    Member,
    Registry,
    Seeds,
    Space,
    Topology,
)

SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1

RESYNC = (
    "python3 -m rexdeck.clickup.engine sync"
    " --registry-path <registry-path> --write-registry"
)

CACHE_SCHEMA: tuple[str, ...] = (
    "CREATE TABLE spaces (id TEXT PRIMARY KEY, name TEXT NOT NULL)",
    "CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, space_id TEXT NOT NULL)",
    "CREATE TABLE lists ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, space_id TEXT NOT NULL, folder_id TEXT)",
    "CREATE TABLE members (id TEXT PRIMARY KEY, username TEXT NOT NULL)",
    "CREATE TABLE custom_fields ("
    " list_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,"
    " type TEXT NOT NULL, options TEXT NOT NULL, PRIMARY KEY (list_id, id))",
)


def registry_path(*, account_id: str | None = None, team_id: str | None = None) -> Path:
    """Resolve the XDG registry at call time, optionally scoped by account/team."""
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    base = root / "rexdeck" / "clickup"
    if account_id is not None or team_id is not None:
        base /= digest_json({"account": account_id, "team": team_id})[:24]
    return base / "registry.yaml"


def seed_registry(
    *,
    account_id: str,
    team_id: str,
    space_id: str,
    space_name: str,
    list_id: str,
    list_name: str,
) -> Registry:
    """Construct an unsynced topology from explicit selections, without I/O."""
    if not all(
        value.isascii() and value.isdigit()
        for value in (account_id, team_id, space_id, list_id)
    ):
        raise ValueError("account, team, space and list IDs must be ASCII digits")
    if not space_name.strip() or not list_name.strip():
        raise ValueError("selected space and list names must not be empty")
    return Registry(
        schema_version=SCHEMA_VERSION,
        digest=None,
        synced_at=None,
        team_id=team_id,
        seeds=Seeds(
            space=Space(id=space_id, name=space_name),
            list=ListRecord(
                id=list_id, name=list_name, space_id=space_id, folder_id=None
            ),
            me_id=account_id,
        ),
        topology=EMPTY_TOPOLOGY,
    )


def bootstrap(
    *,
    account_id: str,
    team_id: str,
    space_id: str,
    space_name: str,
    list_id: str,
    list_name: str,
    path: Path | None = None,
) -> Registry:
    """Explicit sync setup only: write seed topology, refusing an existing file."""
    loaded = seed_registry(
        account_id=account_id,
        team_id=team_id,
        space_id=space_id,
        space_name=space_name,
        list_id=list_id,
        list_name=list_name,
    )
    target = (
        registry_path(account_id=account_id, team_id=team_id) if path is None else path
    )
    document = normalised(loaded)
    document.update(digest=None, synced_at=None)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.open("x", encoding="utf-8") as stream:
        target.chmod(0o600)
        yaml.safe_dump(document, stream, sort_keys=False, allow_unicode=True)
    return loaded


def stamp(registry: Registry | None) -> RegistryStamp:
    """Return the receipt stamp for `registry`, or the absent stamp for None."""
    if registry is None:
        return RegistryStamp(digest=None, synced_at=None)
    return RegistryStamp(digest=registry.digest, synced_at=registry.synced_at)


# --------------------------------------------------------------------------- load


def load(path: Path | None = None) -> Registry:
    """Parse the local registry, failing loudly on anything it cannot read."""
    target = registry_path() if path is None else path
    if not target.is_file():
        raise EngineError(
            Diagnostic(
                code="registry/unsynced",
                severity=SEVERITY_ERROR,
                message="the local registry is missing",
                subject=str(target),
                evidence={"path": str(target)},
                supported_fixes=(RESYNC,),
            )
        )
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"registry {target} is not a mapping")

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"registry {target} declares schema_version {version!r};"
            f" this engine reads {SCHEMA_VERSION}"
        )

    seeds = _mapping(document, "seeds")
    space = _mapping(seeds, "space")
    seed_list = _mapping(seeds, "list")
    return Registry(
        schema_version=SCHEMA_VERSION,
        digest=_optional_text(document.get("digest")),
        synced_at=_optional_text(document.get("synced_at")),
        team_id=_text(_mapping(document, "workspace"), "team_id", "workspace"),
        seeds=Seeds(
            space=Space(
                id=_text(space, "id", "seeds.space"),
                name=_text(space, "name", "seeds.space"),
            ),
            list=ListRecord(
                id=_text(seed_list, "id", "seeds.list"),
                name=_text(seed_list, "name", "seeds.list"),
                space_id=_text(space, "id", "seeds.space"),
                folder_id=None,
            ),
            me_id=_text(_mapping(seeds, "me"), "id", "seeds.me"),
        ),
        topology=_load_topology(_mapping(document, "discovered")),
    )


def _load_topology(discovered: Mapping[str, object]) -> Topology:
    fields = discovered.get("custom_fields") or {}
    if not isinstance(fields, dict):
        raise ValueError(
            "discovered.custom_fields is not a mapping of list ID to definitions"
        )
    return Topology(
        spaces=tuple(
            Space(id=_text(row, "id", "spaces"), name=_text(row, "name", "spaces"))
            for row in _rows(discovered, "spaces")
        ),
        folders=tuple(
            Folder(
                id=_text(row, "id", "folders"),
                name=_text(row, "name", "folders"),
                space_id=_text(row, "space_id", "folders"),
            )
            for row in _rows(discovered, "folders")
        ),
        lists=tuple(
            ListRecord(
                id=_text(row, "id", "lists"),
                name=_text(row, "name", "lists"),
                space_id=_text(row, "space_id", "lists"),
                folder_id=_optional_text(row.get("folder_id")),
            )
            for row in _rows(discovered, "lists")
        ),
        members=tuple(
            Member(
                id=_text(row, "id", "members"),
                username=_text(row, "username", "members"),
            )
            for row in _rows(discovered, "members")
        ),
        custom_fields={
            str(list_id): tuple(
                _load_field(row) for row in _as_rows(definitions, "custom_fields")
            )
            for list_id, definitions in fields.items()
        },
    )


def _load_field(row: Mapping[str, object]) -> CustomField:
    return CustomField(
        id=_text(row, "id", "custom_fields"),
        name=_text(row, "name", "custom_fields"),
        type=_text(row, "type", "custom_fields"),
        options=tuple(
            FieldOption(
                id=_text(option, "id", "options"),
                name=_text(option, "name", "options"),
                orderindex=_optional_int(option.get("orderindex")),
            )
            for option in _as_rows(row.get("options") or [], "options")
        ),
    )


def _mapping(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"registry section {key!r} is missing or not a mapping")
    return value


def _rows(document: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    return _as_rows(document.get(key) or [], key)


def _as_rows(value: object, key: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"registry collection {key!r} is not a list")
    rows: list[Mapping[str, object]] = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(
                f"registry collection {key!r} holds a record that is not a mapping"
            )
        rows.append(row)
    return rows


def _text(row: Mapping[str, object], key: str, where: str) -> str:
    value = row.get(key)
    if value is None or isinstance(value, (dict, list)):
        raise ValueError(f"registry record in {where} is missing field {key!r}")
    return str(value)


def _optional_text(value: object) -> str | None:
    """Return `value` as text, with null preserved as absence.

    A timestamp written unquoted parses as a `datetime` rather than a string,
    and an ID that is all digits parses as an `int`. Both are the same field to
    every reader, so both normalise here rather than at each use.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _optional_int(value: object) -> int | None:
    """Return `value` as an int, with null preserved as absence."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"expected an integer or null, got {type(value).__name__}")
    return int(value)


# ------------------------------------------------------------------- digest and write


def normalised(registry: Registry) -> dict[str, object]:
    """Return the document the digest covers: everything but `digest` and `synced_at`.

    Ordering lives here, in the one function that takes the digest, so the file
    on disk cannot disagree with the digest stamped into it. Every collection
    sorts on `id`: IDs are stable and unique, where names are neither, and a
    name sort would reshuffle the whole file on one rename and bury the
    one-line change this file is local to surface.
    """
    found = registry.topology
    return {
        "schema_version": registry.schema_version,
        "workspace": {"team_id": registry.team_id},
        "seeds": {
            "space": {"id": registry.seeds.space.id, "name": registry.seeds.space.name},
            "list": {"id": registry.seeds.list.id, "name": registry.seeds.list.name},
            "me": {"id": registry.seeds.me_id},
        },
        "discovered": {
            "spaces": [
                {"id": row.id, "name": row.name}
                for row in _ordered(found.spaces, "spaces")
            ],
            "folders": [
                {"id": row.id, "name": row.name, "space_id": row.space_id}
                for row in _ordered(found.folders, "folders")
            ],
            "lists": [
                {
                    "id": row.id,
                    "name": row.name,
                    "space_id": row.space_id,
                    "folder_id": row.folder_id,
                }
                for row in _ordered(found.lists, "lists")
            ],
            "members": [
                {"id": row.id, "username": row.username}
                for row in _ordered(found.members, "members")
            ],
            "custom_fields": {
                list_id: [
                    _field_payload(field) for field in _ordered(fields, "custom_fields")
                ]
                for list_id, fields in sorted(found.custom_fields.items())
            },
        },
    }


def _field_payload(field: CustomField) -> dict[str, object]:
    return {
        "id": field.id,
        "name": field.name,
        "type": field.type,
        "options": [
            {"id": option.id, "name": option.name, "orderindex": option.orderindex}
            for option in _ordered(field.options, "options")
        ],
    }


class _Identified(Protocol):
    """A record carrying a stable string ID — the only thing `_ordered` needs.

    The bound is doing real work rather than decorating a signature. `_ordered`
    sorts six different record types and reports a duplicate by ID, so it needs
    `record.id` to typecheck and it needs the element type to survive the call:
    a non-generic version taking `Sequence[_Identified]` would hand back
    `list[_Identified]`, and every caller would then be building its YAML
    payload out of a type that has lost `name`, `space_id` and the rest.

    It is a read-only property, not a plain attribute, because every record
    here is a frozen dataclass. A mutable-attribute protocol would demand a
    settable `id` that none of them has.
    """

    @property
    def id(self) -> str: ...


#: The endpoint each walked collection was read off. A duplicate ID is a defect
#: rather than a caller error, so the only move a caller can make is to look at
#: what ClickUp actually returned — which means knowing where it came from.
_COLLECTION_SOURCE: Mapping[str, str] = MappingProxyType(
    {
        "spaces": "/space/<space-id>",
        "folders": "/space/<space-id>/folder",
        "lists": "/space/<space-id>/list",
        "members": "/team",
        "custom_fields": "/list/<list-id>/field",
        "options": "/list/<list-id>/field",
    }
)


def _ordered[RecordT: _Identified](
    records: Iterable[RecordT], collection: str
) -> list[RecordT]:
    """Sort `records` by ID, refusing a collection that holds one ID twice.

    No collection in this file can legitimately hold the same ID twice: a list
    sits in one folder, or folderless in one space, never both, and a member is
    recorded once for the workspace rather than once per space. So `id` alone
    is a total order and needs no tiebreak. A duplicate is a defect in the
    walk, and the writer fails on it rather than ordering around it and
    committing a file whose order depends on the sequence the caller built.
    """
    ordered = sorted(records, key=lambda record: record.id)
    duplicates = sorted(
        {
            ordered[index].id
            for index in range(1, len(ordered))
            if ordered[index].id == ordered[index - 1].id
        }
    )
    if duplicates:
        raise EngineError(
            Diagnostic(
                code="registry/duplicate-id",
                severity=SEVERITY_ERROR,
                message=f"the walk found the same ID twice in {collection}",
                subject=collection,
                evidence={"collection": collection, "ids": duplicates},
                # Not the duplicate IDs themselves: an ID is neither a command
                # nor a field name, and a caller handed one back has been told
                # what is wrong without being told anything to do. Re-syncing is
                # no better — a deterministic duplicate would loop. What settles
                # it is seeing whether ClickUp returned the ID twice.
                supported_fixes=(
                    'curl -sS -H "Authorization: $CLICKUP_API_TOKEN"'
                    f" https://api.clickup.com/api/v2{_COLLECTION_SOURCE[collection]}",
                ),
            )
        )
    return ordered


def compute_digest(registry: Registry) -> str:
    """Return the sha256 of the normalised document — the digest `sync` stamps."""
    return digest_json(normalised(registry))


def check_fresh(registry: Registry) -> None:
    """Raise when the registry has never been walked, or no longer matches its digest.

    `registry/unsynced` keys on `synced_at` and on nothing else. A walk that
    legitimately found nothing and a walk that never ran are identical on disk
    apart from that field, and only one of the two is a missing result.
    """
    if registry.synced_at is None:
        raise EngineError(
            Diagnostic(
                code="registry/unsynced",
                severity=SEVERITY_ERROR,
                message="no authenticated sync has walked the workspace yet",
                subject=registry.team_id,
                evidence={"synced_at": None},
                supported_fixes=(RESYNC,),
            )
        )
    recomputed = compute_digest(registry)
    if recomputed != registry.digest:
        raise EngineError(
            Diagnostic(
                code="registry/stale",
                severity=SEVERITY_ERROR,
                message="the registry no longer digests to the digest stamped in it",
                subject=registry.team_id,
                evidence={"stamped": registry.digest, "recomputed": recomputed},
                supported_fixes=(RESYNC,),
            )
        )


def rewrite(
    registry: Registry, *, now: str, path: Path | None = None
) -> Registry | None:
    """Write the registry when the write rule fires, returning what was written.

    The rule is: the newly computed digest differs, **or** `synced_at` is null.
    Both halves are load-bearing. Without the null clause, a first sync whose
    walk legitimately finds nothing new recomputes the digest it already has,
    declines to write, leaves `synced_at` null, and `registry/unsynced` then
    fires forever offering a fix that provably cannot clear it. Without the
    stamp on both paths, that same null clause rewrites the file every night
    for no change at all.
    """
    target = (
        registry_path(account_id=registry.seeds.me_id, team_id=registry.team_id)
        if path is None
        else path
    )
    digest = compute_digest(registry)
    if digest == registry.digest and registry.synced_at is not None:
        return None

    written = Registry(
        schema_version=registry.schema_version,
        digest=digest,
        synced_at=now,
        team_id=registry.team_id,
        seeds=registry.seeds,
        topology=registry.topology,
    )
    body = normalised(written)
    document = {
        "schema_version": body["schema_version"],
        "digest": digest,
        "synced_at": now,
        "workspace": body["workspace"],
        "seeds": body["seeds"],
        "discovered": body["discovered"],
    }
    rendered = yaml.safe_dump(
        document,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10_000,
    )
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, delete=False
    ) as stream:
        pending = Path(stream.name)
        try:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
    return written


# ------------------------------------------------------------------------ resolution


def resolve_list(registry: Registry, topology: Topology, token: str) -> str:
    """Return the list ID `token` names, resolved locally with no request built."""
    candidates = _list_candidates(registry, topology)
    for record in candidates:
        if record.id == token:
            return token
    matches = [
        record for record in candidates if record.name.casefold() == token.casefold()
    ]
    if len(matches) == 1:
        return matches[0].id
    if matches:
        raise _ambiguous("list", token, [record.id for record in matches], "--list")
    # An all-digit token the registry does not carry passes through as an ID
    # rather than failing, which is the sibling engine's rule too: a list
    # created since the last sync is reachable by ID without forcing a walk
    # first, and a caller who mistypes one gets ClickUp's own 404 rather than a
    # local guess about which list they meant.
    if token.isdigit():
        return token
    raise _unresolved("list", token, "--list", len(candidates))


def resolve_assignee(registry: Registry, topology: Topology, token: str) -> str:
    """Return the member ID `token` names. `me` resolves from the seeds with no call.

    This is the point of the verb. The MCP surface makes a caller run
    `resolve_assignees` before `filter_tasks` and discover the ordering the
    hard way; here the resolution happens against a local file before any
    request exists, so the precondition is structurally unreachable.
    """
    if token == "me":
        return registry.seeds.me_id
    for member in topology.members:
        if member.id == token:
            return token
    matches = [
        member
        for member in topology.members
        if member.username.casefold() == token.casefold()
    ]
    if len(matches) == 1:
        return matches[0].id
    if matches:
        raise _ambiguous(
            "member", token, [member.id for member in matches], "--assignee"
        )
    if token.isdigit():
        return token
    raise _unresolved("member", token, "--assignee", len(topology.members))


def _list_candidates(registry: Registry, topology: Topology) -> list[ListRecord]:
    """Return the lists a name may resolve against.

    The seed list joins the discovered set only when the walk did not already
    find it. Adding it unconditionally would hide a real collision between two
    lists sharing the seed's name behind a silent seed win, and that collision
    is exactly what `registry/ambiguous` exists to report.
    """
    if registry.seeds.list.id in {record.id for record in topology.lists}:
        return list(topology.lists)
    return [*topology.lists, registry.seeds.list]


def _ambiguous(kind: str, token: str, ids: Sequence[str], flag: str) -> EngineError:
    return EngineError(
        Diagnostic(
            code="registry/ambiguous",
            severity=SEVERITY_ERROR,
            message=f"the {kind} name matches more than one entity",
            subject=token,
            evidence={"name": token, "matched": list(ids)},
            supported_fixes=tuple(f"{flag} {entity_id}" for entity_id in ids),
        )
    )


def _unresolved(kind: str, token: str, flag: str, searched: int) -> EngineError:
    return EngineError(
        Diagnostic(
            code="registry/unresolved",
            severity=SEVERITY_ERROR,
            message=f"the {kind} name matches nothing in the registry",
            subject=token,
            evidence={"name": token, "searched": searched},
            supported_fixes=(f"{flag} <id>", RESYNC),
        )
    )


# ----------------------------------------------------------------------- derived cache


def fill_cache(connection: sqlite3.Connection, topology: Topology) -> None:
    """Replace the derived cache with `topology`.

    Replaced wholesale rather than diffed. The cache is never authoritative, so
    a rebuild is always correct, and there is no staleness state left over to
    get wrong later.
    """
    for table in ("spaces", "folders", "lists", "members", "custom_fields"):
        connection.execute(f"DELETE FROM {table}")
    connection.executemany(
        "INSERT INTO spaces (id, name) VALUES (?, ?)",
        [(row.id, row.name) for row in topology.spaces],
    )
    connection.executemany(
        "INSERT INTO folders (id, name, space_id) VALUES (?, ?, ?)",
        [(row.id, row.name, row.space_id) for row in topology.folders],
    )
    connection.executemany(
        "INSERT INTO lists (id, name, space_id, folder_id) VALUES (?, ?, ?, ?)",
        [(row.id, row.name, row.space_id, row.folder_id) for row in topology.lists],
    )
    connection.executemany(
        "INSERT INTO members (id, username) VALUES (?, ?)",
        [(row.id, row.username) for row in topology.members],
    )
    connection.executemany(
        "INSERT INTO custom_fields (list_id, id, name, type, options) VALUES (?, ?, ?, ?, ?)",
        [
            (
                list_id,
                field.id,
                field.name,
                field.type,
                json.dumps(
                    [
                        {
                            "id": option.id,
                            "name": option.name,
                            "orderindex": option.orderindex,
                        }
                        for option in field.options
                    ]
                ),
            )
            for list_id, fields in topology.custom_fields.items()
            for field in fields
        ],
    )
    connection.commit()


def cached_fields(
    connection: sqlite3.Connection, list_id: str
) -> tuple[CustomField, ...]:
    """Return one list's cached custom-field definitions, keyed by list ID.

    The lookup the cache exists for. ClickUp scopes custom-field definitions to
    a list and returns a task's values as UUID-keyed blobs, so without these
    `projections.py` has nothing to turn a UUID into a column name with.
    """
    rows = connection.execute(
        "SELECT id, name, type, options FROM custom_fields WHERE list_id = ? ORDER BY id",
        (list_id,),
    ).fetchall()
    return tuple(
        CustomField(
            id=str(row["id"]),
            name=str(row["name"]),
            type=str(row["type"]),
            options=tuple(
                FieldOption(
                    id=str(option["id"]),
                    name=str(option["name"]),
                    orderindex=_optional_int(option.get("orderindex")),
                )
                for option in json.loads(str(row["options"]))
            ),
        )
        for row in rows
    )

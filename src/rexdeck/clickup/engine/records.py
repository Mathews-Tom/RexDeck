"""The shapes the registry records and every other module passes around.

Data only, no behaviour. `registry.py` parses and writes these, `projections.py`
renders against them, and `commands.py` moves them between the two — so they
live where none of the three owns them.

Dataclasses rather than a validating model: the runtime dependency list is
`httpx` and `pyyaml`, and validation belongs at the parse boundary in
`registry.py`, where the field that is missing can be named in the error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Space:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Folder:
    id: str
    name: str
    space_id: str


@dataclass(frozen=True, slots=True)
class ListRecord:
    id: str
    name: str
    space_id: str
    folder_id: str | None


@dataclass(frozen=True, slots=True)
class Member:
    """A workspace member's ID and username, without email addresses."""

    id: str
    username: str


@dataclass(frozen=True, slots=True)
class FieldOption:
    """One choice on a dropdown or label field.

    `orderindex` is carried because ClickUp spells a chosen dropdown value two
    ways — the option's UUID, or its position — and a registry that records
    only the UUID cannot name a value sent the other way.
    """

    id: str
    name: str
    orderindex: int | None


@dataclass(frozen=True, slots=True)
class CustomField:
    """One list's definition of one custom field.

    ClickUp returns a task's custom fields as UUID-keyed blobs with a type
    discriminator, so `type` decides how `projections.py` renders a value and
    `options` is what a dropdown or label value indexes into.
    """

    id: str
    name: str
    type: str
    options: tuple[FieldOption, ...]


@dataclass(frozen=True, slots=True)
class Seeds:
    """Hand-authored entry points the walk starts from, never rewritten by it."""

    space: Space
    list: ListRecord
    me_id: str


@dataclass(frozen=True, slots=True)
class Topology:
    """Everything a walk found: the local registry's, or a live one's.

    One type with two producers — the local file and `sync`'s walk — so
    `--no-cache` resolving against a fresh walk and the default path resolving
    against the registry run the same resolution code.
    """

    spaces: tuple[Space, ...]
    folders: tuple[Folder, ...]
    lists: tuple[ListRecord, ...]
    members: tuple[Member, ...]
    custom_fields: Mapping[str, tuple[CustomField, ...]]


EMPTY_TOPOLOGY = Topology(spaces=(), folders=(), lists=(), members=(), custom_fields={})


@dataclass(frozen=True, slots=True)
class Registry:
    """The local registry as parsed.

    `digest` and `synced_at` are both nullable and null means absent in both,
    the same convention `RegistryStamp` uses, so a stamp built from this never
    translates one spelling of absence into another.
    """

    schema_version: int
    digest: str | None
    synced_at: str | None
    team_id: str
    seeds: Seeds
    topology: Topology

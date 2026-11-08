"""The synthetic registry: parsing, the digest and its ordering, the write rule, resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rexdeck.clickup.engine import registry
from rexdeck.clickup.engine.diagnostics import EngineError
from rexdeck.clickup.engine.records import (
    CustomField,
    FieldOption,
    Folder,
    ListRecord,
    Member,
    Registry,
    Space,
    Topology,
)

from .conftest import seed_registry, walked_topology


def _with(
    topology: Topology, *, digest: str | None = None, synced_at: str | None = None
) -> Registry:
    seeds = seed_registry()
    return Registry(
        schema_version=1,
        digest=digest,
        synced_at=synced_at,
        team_id=seeds.team_id,
        seeds=seeds.seeds,
        topology=topology,
    )


# ------------------------------------------------------------------------------ parsing


def test_synthetic_synced_registry_loads_with_its_digest(synced: Path) -> None:
    loaded = registry.load(synced)
    assert loaded.digest == registry.compute_digest(loaded)


def test_synthetic_synced_registry_preserves_its_schema_and_seeds(synced: Path) -> None:
    loaded = registry.load(synced)
    assert loaded.schema_version == 1
    assert loaded.seeds.space.id == "700000000001"
    assert loaded.digest == registry.compute_digest(loaded)


def test_load_rejects_a_schema_version_it_does_not_know(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 99}), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        registry.load(path)


def test_load_rejects_a_list_record_missing_a_field(
    tmp_path: Path, synced: Path
) -> None:
    del tmp_path
    document = yaml.safe_load(synced.read_text(encoding="utf-8"))
    del document["discovered"]["lists"][0]["space_id"]
    synced.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="space_id"):
        registry.load(synced)


def test_load_raises_unsynced_rather_than_inventing_a_registry_that_is_not_there(
    tmp_path: Path,
) -> None:
    with pytest.raises(EngineError) as caught:
        registry.load(tmp_path / "absent.yaml")

    assert caught.value.diagnostic.code == "registry/unsynced"


def test_load_accepts_an_unquoted_timestamp_yaml_parses_as_a_datetime(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r.yaml"
    registry.rewrite(_with(walked_topology()), now="2026-09-13T09:00:00Z", path=path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "'2026-09-13T09:00:00Z'", "2026-09-13T09:00:00Z"
        ),
        encoding="utf-8",
    )

    assert registry.load(path).synced_at == "2026-09-13T09:00:00Z"


# ------------------------------------------------------------------------------- digest


def test_the_digest_covers_neither_the_digest_nor_synced_at() -> None:
    keys = registry.normalised(_with(walked_topology())).keys()

    assert "digest" not in keys
    assert "synced_at" not in keys


def test_a_no_op_resync_leaves_the_file_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    registry.rewrite(_with(walked_topology()), now="2026-09-13T09:00:00Z", path=path)
    before = path.read_bytes()

    assert (
        registry.rewrite(registry.load(path), now="2026-09-14T09:00:00Z", path=path)
        is None
    )
    assert path.read_bytes() == before


def test_collections_are_ordered_by_id_whatever_order_the_walk_built_them_in(
    tmp_path: Path,
) -> None:
    topology = walked_topology()
    shuffled = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=tuple(reversed(topology.lists)),
        members=tuple(reversed(topology.members)),
        custom_fields=topology.custom_fields,
    )
    forward = tmp_path / "forward.yaml"
    backward = tmp_path / "backward.yaml"
    registry.rewrite(_with(topology), now="2026-09-13T09:00:00Z", path=forward)
    registry.rewrite(_with(shuffled), now="2026-09-13T09:00:00Z", path=backward)

    assert forward.read_bytes() == backward.read_bytes()


def test_a_rename_moves_one_line_because_the_sort_key_is_the_id_not_the_name(
    tmp_path: Path,
) -> None:
    topology = walked_topology()
    renamed = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=tuple(
            ListRecord(
                id=row.id,
                name="AAA - first",
                space_id=row.space_id,
                folder_id=row.folder_id,
            )
            if row.id == "70000009999"
            else row
            for row in topology.lists
        ),
        members=topology.members,
        custom_fields=topology.custom_fields,
    )
    before = tmp_path / "before.yaml"
    after = tmp_path / "after.yaml"
    registry.rewrite(_with(topology), now="2026-09-13T09:00:00Z", path=before)
    registry.rewrite(_with(renamed), now="2026-09-13T09:00:00Z", path=after)

    changed = [
        (old, new)
        for old, new in zip(
            before.read_text(encoding="utf-8").splitlines(),
            after.read_text(encoding="utf-8").splitlines(),
            strict=True,
        )
        if old != new
    ]

    # The digest line, and the one renamed list. Nothing reshuffled around it.
    assert len(changed) == 2
    assert "Project Gamma" in changed[1][0]


def test_the_writer_refuses_a_collection_holding_one_id_twice() -> None:
    topology = walked_topology()
    doubled = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=topology.lists,
        members=(*topology.members, Member(id="710000001", username="user_one-again")),
        custom_fields=topology.custom_fields,
    )

    with pytest.raises(EngineError) as caught:
        registry.compute_digest(_with(doubled))

    assert caught.value.diagnostic.code == "registry/duplicate-id"
    assert caught.value.diagnostic.evidence["ids"] == ["710000001"]
    # Not the duplicate IDs: an ID is neither a command nor a field name, and a
    # caller handed one back has nothing to do with it.
    fixes = caught.value.diagnostic.supported_fixes
    assert fixes == (
        'curl -sS -H "Authorization: $CLICKUP_API_TOKEN" https://api.clickup.com/api/v2/team',
    )


# --------------------------------------------------------------------------- write rule


def test_a_first_sync_that_discovers_nothing_still_writes_and_stamps(
    tmp_path: Path,
) -> None:
    """The null clause. Without it `registry/unsynced` fires forever.

    A first authenticated sync whose walk legitimately finds nothing recomputes
    the digest the file already carries. Under a digest-only rule it declines
    to write, `synced_at` stays null, and the diagnostic offers a fix that
    provably cannot clear it.
    """
    path = tmp_path / "r.yaml"
    seeds = seed_registry()
    unchanged = _with(
        seeds.topology, digest=registry.compute_digest(seeds), synced_at=None
    )

    written = registry.rewrite(unchanged, now="2026-09-13T09:00:00Z", path=path)

    assert written is not None
    assert written.synced_at == "2026-09-13T09:00:00Z"
    assert registry.load(path).synced_at == "2026-09-13T09:00:00Z"


def test_the_digest_changed_path_stamps_synced_at_too(tmp_path: Path) -> None:
    """The other half. Stamping only on the null path rewrites the file nightly.

    If `synced_at` were stamped only where the digest changed, the null-clause
    write would leave it null, `synced_at is null` would stay true, and every
    subsequent sync would write again for no change at all.
    """
    path = tmp_path / "r.yaml"
    registry.rewrite(seed_registry(), now="2026-09-13T09:00:00Z", path=path)
    grown = _with(
        walked_topology(),
        digest=registry.load(path).digest,
        synced_at="2026-09-13T09:00:00Z",
    )

    written = registry.rewrite(grown, now="2026-09-20T09:00:00Z", path=path)

    assert written is not None
    assert written.synced_at == "2026-09-20T09:00:00Z"
    assert (
        registry.rewrite(registry.load(path), now="2026-09-21T09:00:00Z", path=path)
        is None
    )


# ------------------------------------------------------------------------- freshness


def test_check_fresh_fires_unsynced_on_a_null_stamp() -> None:
    with pytest.raises(EngineError) as caught:
        registry.check_fresh(seed_registry())

    assert caught.value.diagnostic.code == "registry/unsynced"
    assert caught.value.diagnostic.supported_fixes == (registry.RESYNC,)
    assert "--registry-path <registry-path>" in registry.RESYNC


def test_an_empty_discovered_set_is_not_what_unsynced_tests(tmp_path: Path) -> None:
    """A walk that found nothing is a result. A walk that never ran is not."""
    path = tmp_path / "r.yaml"
    written = registry.rewrite(seed_registry(), now="2026-09-13T09:00:00Z", path=path)
    assert written is not None

    registry.check_fresh(written)


def test_check_fresh_fires_stale_when_the_file_no_longer_digests_to_its_stamp() -> None:
    tampered = _with(
        walked_topology(), digest="0" * 64, synced_at="2026-09-13T09:00:00Z"
    )

    with pytest.raises(EngineError) as caught:
        registry.check_fresh(tampered)

    assert caught.value.diagnostic.code == "registry/stale"


# ------------------------------------------------------------------------- resolution


def test_me_resolves_from_the_seeds_with_no_member_record_at_all() -> None:
    seeds = seed_registry()

    assert registry.resolve_assignee(seeds, seeds.topology, "me") == "710000001"


def test_a_username_resolves_to_its_member_id() -> None:
    seeds = seed_registry()

    assert (
        registry.resolve_assignee(seeds, walked_topology(), "user_two") == "710000002"
    )


def test_a_list_name_resolves_to_its_id_case_insensitively() -> None:
    seeds = seed_registry()

    assert (
        registry.resolve_list(seeds, walked_topology(), "project beta") == "70000000101"
    )


def test_an_explicit_list_id_passes_through_untouched() -> None:
    seeds = seed_registry()

    assert (
        registry.resolve_list(seeds, walked_topology(), "70000009999") == "70000009999"
    )


def test_an_ambiguous_list_name_names_every_id_that_matched() -> None:
    topology = walked_topology()
    collided = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=(
            *topology.lists,
            ListRecord(
                id="70000000999",
                name="Project Gamma",
                space_id="700000000001",
                folder_id=None,
            ),
        ),
        members=topology.members,
        custom_fields=topology.custom_fields,
    )

    with pytest.raises(EngineError) as caught:
        registry.resolve_list(seed_registry(), collided, "Project Gamma")

    assert caught.value.diagnostic.code == "registry/ambiguous"
    assert caught.value.diagnostic.supported_fixes == (
        "--list 70000009999",
        "--list 70000000999",
    )


def test_an_unknown_list_name_is_unresolved_rather_than_a_guess() -> None:
    with pytest.raises(EngineError) as caught:
        registry.resolve_list(seed_registry(), walked_topology(), "INT - Nowhere")

    assert caught.value.diagnostic.code == "registry/unresolved"
    assert caught.value.diagnostic.evidence["searched"] == 3


def test_the_seed_list_resolves_before_any_walk_has_found_it() -> None:
    seeds = seed_registry()

    assert (
        registry.resolve_list(seeds, seeds.topology, "Project Gamma") == "70000009999"
    )


def test_the_seed_does_not_mask_a_collision_the_walk_actually_found() -> None:
    """Adding the seed unconditionally would hide the ambiguity behind a seed win."""
    topology = walked_topology()
    collided = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=(
            ListRecord(
                id="70000009999",
                name="Project Gamma",
                space_id="700000000001",
                folder_id=None,
            ),
            ListRecord(
                id="70000000998",
                name="Project Gamma",
                space_id="700000000001",
                folder_id=None,
            ),
        ),
        members=topology.members,
        custom_fields=topology.custom_fields,
    )

    with pytest.raises(EngineError) as caught:
        registry.resolve_list(seed_registry(), collided, "Project Gamma")

    assert caught.value.diagnostic.code == "registry/ambiguous"


# ------------------------------------------------------------------------ derived cache


def test_the_cache_round_trips_the_custom_field_definitions_it_was_filled_with(
    tmp_path: Path,
) -> None:
    del tmp_path
    from rexdeck.clickup.engine.cache import open_cache

    connection = open_cache(
        schema_version=registry.CACHE_SCHEMA_VERSION,
        schema=registry.CACHE_SCHEMA,
    )
    try:
        registry.fill_cache(connection, walked_topology())
        fields = registry.cached_fields(connection, "70000009999")
    finally:
        connection.close()

    assert [field.name for field in fields] == [
        "Channel",
        "Budget",
        "Signed off",
        "Shape",
    ]
    assert fields[0].options == (
        FieldOption(
            id="20000000-0000-4000-8000-000000000001", name="Paid Search", orderindex=0
        ),
        FieldOption(
            id="20000000-0000-4000-8000-000000000002", name="Paid Social", orderindex=1
        ),
    )


def test_filling_the_cache_twice_replaces_rather_than_accumulates() -> None:
    from rexdeck.clickup.engine.cache import open_cache

    connection = open_cache(
        schema_version=registry.CACHE_SCHEMA_VERSION,
        schema=registry.CACHE_SCHEMA,
    )
    try:
        registry.fill_cache(connection, walked_topology())
        registry.fill_cache(connection, walked_topology())
        rows = connection.execute("SELECT COUNT(*) FROM lists").fetchone()
    finally:
        connection.close()

    assert rows[0] == 3


def test_a_list_with_no_cached_definitions_returns_none_rather_than_another_lists() -> (
    None
):
    from rexdeck.clickup.engine.cache import open_cache

    connection = open_cache(
        schema_version=registry.CACHE_SCHEMA_VERSION,
        schema=registry.CACHE_SCHEMA,
    )
    try:
        registry.fill_cache(connection, walked_topology())
        assert registry.cached_fields(connection, "70000099999") == ()
    finally:
        connection.close()


def test_the_cache_carries_no_member_email_because_the_registry_never_holds_one() -> (
    None
):
    assert not hasattr(Member("1", "user_one"), "email")
    assert "email" not in " ".join(registry.CACHE_SCHEMA)


def test_custom_field_definitions_are_keyed_by_list_id() -> None:
    topology = walked_topology()
    one_list = Topology(
        spaces=topology.spaces,
        folders=topology.folders,
        lists=topology.lists,
        members=topology.members,
        custom_fields={
            "70000009999": (
                CustomField(
                    id="10000000-0000-4000-8000-000000000001",
                    name="Channel",
                    type="drop_down",
                    options=(),
                ),
            )
        },
    )
    document = registry.normalised(_with(one_list))
    discovered = document["discovered"]
    assert isinstance(discovered, dict)

    assert list(discovered["custom_fields"]) == ["70000009999"]


def test_a_folder_record_carries_its_space_and_nothing_more() -> None:
    folder = Folder(id="70000000001", name="Sample Folder", space_id="700000000001")
    document = registry.normalised(
        _with(
            Topology(
                spaces=(Space(id="700000000001", name="Sample Space"),),
                folders=(folder,),
                lists=(),
                members=(),
                custom_fields={},
            )
        )
    )
    discovered = document["discovered"]
    assert isinstance(discovered, dict)

    assert discovered["folders"] == [
        {"id": "70000000001", "name": "Sample Folder", "space_id": "700000000001"}
    ]

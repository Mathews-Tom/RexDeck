"""Native evidence remains distinct from identity guesses and focus fallbacks."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from rexdeck import attention, cli, lifecycle, settings


class Native:
    def __init__(self) -> None:
        self.instance = "instance-one"
        self.connected = True
        self.present = True
        self.session_present = True
        self.placement = "placed"
        self.host = "host-one"
        self.unreadable = False
        self.records: list[dict[str, object]] = [
            {"id": "worker", "state": "blocked", "updated_at": "2026-11-08T12:00:00Z"}
        ]
        self.exit: object = None
        self.focused: list[dict[str, object]] = []

    def status(self, setup: lifecycle.Setup, *, selected: bool) -> dict[str, object]:
        assert selected
        if not self.connected:
            raise RuntimeError("Disconnected")
        return {"endpoint": setup.server, "instance_id": self.instance}

    def rex(self, setup: lifecycle.Setup, *args: str) -> dict[str, object]:
        if args == ("ls", "--json"):
            return {
                "sessions": [
                    {"session_id": "session:one"},
                    {"session_id": "session:two"},
                ]
                if self.session_present
                else []
            }
        assert args[:3] == ("api", "call", "-s")
        assert args[3] in ("session:one", "session:two")
        method = args[4]
        payload = json.loads(args[5])
        if method == "session.list_blocks":
            return {
                "blocks": [
                    {
                        "block_id": args[3].replace("session:", "block:"),
                        "placement": self.placement,
                        "host_id": self.host,
                        "window_id": "window:one",
                    }
                ]
                if self.present
                else []
            }
        if method.endswith("program_status"):
            if self.unreadable:
                raise RuntimeError("Status unavailable after connecting")
            return {"records": self.records}
        if method.endswith("process"):
            return {"last_exit": self.exit}
        if method == "session.focus_block":
            self.focused.append(payload)
            return {"revision": 1}
        raise AssertionError(method)


@pytest.fixture
def native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Native, settings.SavedTarget]:
    target = settings.SavedTarget(
        tmp_path / "files",
        tmp_path / "data",
        tmp_path / "init.lua",
        tmp_path / "init.lua",
        lifecycle._canonical_server("unix://" + str(tmp_path / "attention-test.sock")),
        False,
    )
    setup = lifecycle.Setup(
        tmp_path, target.workspace, target.config, target.data_root, target.server
    )
    monkeypatch.setattr(settings.SavedTarget, "setup", lambda self: setup)
    backend = Native()
    monkeypatch.setattr(lifecycle, "_rex", backend.rex)
    monkeypatch.setattr(lifecycle, "_server_status", backend.status)
    return backend, target


def rows(target: settings.SavedTarget, **filters: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], attention.listing(target, **filters)["rows"])


def test_observed_records_filter_and_focus_only_exact_binding(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    observed = attention.observe(target, "session:one", "block:one", "project A")
    row = rows(target, project="project A", session="session:one", block="block:one")[0]
    assert row["agent_identity"] == "unknown"
    assert row["records"] == backend.records
    assert row["binding"] == "bound"
    assert not rows(target, project="project B")
    attention.jump(target, str(observed["id"]))
    assert backend.focused == [{"block_id": "block:one"}]
    with pytest.raises(RuntimeError, match="Unknown attention"):
        attention.jump(target, "worker")
    with pytest.raises(RuntimeError, match="exact session"):
        attention.observe(target, "project A", "block:one")


@pytest.mark.parametrize("closed_session", [False, True])
def test_completed_evidence_survives_closed_binding_without_guessing(
    native: tuple[Native, settings.SavedTarget], closed_session: bool
) -> None:
    backend, target = native
    backend.records = [
        {"id": "worker", "state": "done", "updated_at": "2026-11-08T12:01:00Z"}
    ]
    backend.exit = {"exit_code": 0}
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    backend.present = False
    backend.session_present = not closed_session
    attention.observe(target, "session:one", "block:one")
    row = rows(target)[0]
    assert row["records"] == backend.records
    assert row["last_exit"] == {"exit_code": 0}
    assert row["connection"] == "closed"
    with pytest.raises(RuntimeError, match="closed or unbound"):
        attention.jump(target, identity)
    assert not backend.focused
    backend.connected = False
    attention.observe(target, "session:one", "block:one")
    retained = rows(target)[0]
    assert retained["connection"] == "disconnected"
    assert retained["target_state"] == "closed"
    assert retained["records"] == backend.records


def test_detached_is_unbound_not_missing_or_completed(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    backend.placement = "detached"
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    assert rows(target)[0]["binding"] == "unbound"
    assert rows(target)[0]["connection"] == "connected"
    with pytest.raises(RuntimeError, match="closed or unbound"):
        attention.jump(target, identity)
    assert not backend.focused


def test_disconnected_and_stale_keep_source_timestamp(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, target = native
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    backend.connected = False
    monkeypatch.setattr(time, "time", lambda: 1061.0)
    assert attention.observe(target, "session:one", "block:one")["state"] == "fault"
    row = rows(target)[0]
    assert row["freshness"] == "stale"
    assert row["observed_at"] == 1000
    assert row["records"] == backend.records
    assert row["connection"] == "disconnected"
    with pytest.raises(RuntimeError, match="Disconnected"):
        attention.jump(target, identity)
    assert not backend.focused


def test_replaced_endpoint_cannot_focus_even_same_block_id(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    backend.instance = "instance-two"
    with pytest.raises(RuntimeError, match="replaced"):
        attention.jump(target, identity)
    attention.observe(target, "session:one", "block:one")
    assert len(rows(target)) == 2
    assert rows(target)[0]["connection"] == "replaced"
    assert not backend.focused


def test_no_status_is_unknown_not_inferred_from_process(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    backend.records = []
    attention.observe(target, "session:one", "block:one")
    row = rows(target)[0]
    assert row["records"] == []
    assert row["agent_identity"] == "unknown"
    assert row["last_exit"] is None


def test_storage_rejects_oversized_data_and_symlink(
    native: tuple[Native, settings.SavedTarget], tmp_path: Path
) -> None:
    _, target = native
    path = attention._path(target)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * (attention._MAX_BYTES + 1))
    with pytest.raises(RuntimeError, match="exceeds 2 MiB"):
        attention.listing(target)
    path.unlink()
    path.symlink_to(tmp_path / "foreign")
    with pytest.raises((RuntimeError, OSError)):
        attention.listing(target)


def test_hostile_native_fields_are_bounded_without_blocking_sibling(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    backend.records = [
        {
            "id": str(index),
            "state": "working",
            "msg": "🚀" * 10000,
            "title": "t" * 5000,
            "app": "a" * 5000,
            "kind": "k" * 5000,
            "updated_at": "2026-11-08T12:00:00Z",
        }
        for index in range(128)
    ]
    first = attention.observe(target, "session:one", "block:one")
    row = cast(dict[str, object], first["row"])
    assert row["truncated"] is True
    assert len(json.dumps(row, ensure_ascii=True).encode()) <= attention._MAX_ROW_BYTES
    records = cast(list[dict[str, str]], row["records"])
    assert records
    for record in records:
        for key, limit in attention._FIELD_LIMITS.items():
            assert len(record.get(key, "")) <= limit
    backend.records = [{"state": "working", "updated_at": "2026-11-08T12:00:00Z"}]
    assert attention.observe(target, "session:two", "block:two")["state"] == "observed"
    assert len(rows(target)) == 2


def test_error_text_is_bounded_in_retention_and_fault_output(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target = native
    attention.observe(target, "session:one", "block:one")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("x" * 100000)

    monkeypatch.setattr(lifecycle, "_server_status", unavailable)
    result = attention.observe(target, "session:one", "block:one")
    assert len(str(result["error"])) == attention._MAX_ERROR
    row = rows(target)[0]
    assert len(str(row["error"])) == attention._MAX_ERROR
    assert row["truncated"] is True


@pytest.mark.parametrize("kind", ["storage-fifo", "lock-fifo", "lock-symlink"])
def test_nonregular_storage_and_locks_fail_without_blocking(
    native: tuple[Native, settings.SavedTarget],
    kind: str,
    tmp_path: Path,
) -> None:
    _, target = native
    path = attention._path(target)
    path.parent.mkdir(parents=True)
    selected = path if kind == "storage-fifo" else path.with_suffix(".lock")
    if kind == "lock-symlink":
        selected.symlink_to(tmp_path / "foreign-lock")
    else:
        os.mkfifo(selected)
    with pytest.raises((OSError, RuntimeError)):
        attention.listing(target)


def test_list_validates_saved_workspace_before_opening_storage(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target = native

    def redirected(self: settings.SavedTarget) -> lifecycle.Setup:
        raise RuntimeError("Saved workspace target changed")

    monkeypatch.setattr(settings.SavedTarget, "setup", redirected)
    with pytest.raises(RuntimeError, match="workspace target changed"):
        attention.listing(target)
    assert not attention._path(target).exists()


def test_storage_is_setup_owned_private_and_durably_replaced(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target = native
    calls: list[str] = []
    original_sync, original_replace = os.fsync, os.replace

    def sync(descriptor: int) -> None:
        calls.append(
            "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        original_sync(descriptor)

    def replace(source: str, destination: Path) -> None:
        calls.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", replace)
    attention.observe(target, "session:one", "block:one")
    path = attention._path(target)
    assert path == target.workspace / "attention" / "attention.json"
    assert calls == ["file", "directory", "replace", "directory"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".lock").stat().st_mode) == 0o600


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_or_boolean_observation_timestamp_is_rejected(
    native: tuple[Native, settings.SavedTarget],
    timestamp: object,
) -> None:
    _, target = native
    attention.observe(target, "session:one", "block:one")
    path = attention._path(target)
    document = json.loads(path.read_text())
    next(iter(document["rows"].values()))["observed_at"] = timestamp
    path.write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match="observation timestamp"):
        attention.listing(target)


def test_retained_id_cannot_override_authoritative_lookup_key(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    _, target = native
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    with attention._store(target) as retained:
        retained[identity]["id"] = "misleading-id"
        attention._save(target, retained)
    assert rows(target)[0]["id"] == identity


@pytest.mark.parametrize("absent", [False, True])
def test_restart_replaces_every_endpoint_row_and_clears_old_errors(
    native: tuple[Native, settings.SavedTarget],
    absent: bool,
) -> None:
    backend, target = native
    first = str(
        attention.observe(target, "session:one", "block:one", "project-one")["id"]
    )
    second = str(
        attention.observe(target, "session:two", "block:two", "project-two")["id"]
    )
    backend.connected = False
    attention.refresh(target, first)
    attention.refresh(target, second)
    backend.connected = True
    backend.instance = "instance-two"
    backend.session_present = not absent
    result = attention.refresh(target, first)
    assert result["state"] == ("fault" if absent else "observed")
    retained = {str(row["id"]): row for row in rows(target)}
    for identity in (first, second):
        assert retained[identity]["connection"] == "replaced"
        assert retained[identity]["target_state"] == "replaced"
        assert retained[identity]["binding"] == "bound"
        assert "error" not in retained[identity]
    if not absent:
        assert cast(dict[str, object], result["row"])["project"] == "project-one"


def test_restart_keeps_known_closure_distinct_from_replacement(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    closed = str(attention.observe(target, "session:one", "block:one")["id"])
    backend.present = False
    attention.refresh(target, closed)
    backend.present = True
    backend.instance = "instance-two"
    attention.observe(target, "session:two", "block:two")
    row = {str(item["id"]): item for item in rows(target)}[closed]
    assert (row["connection"], row["target_state"]) == ("replaced", "closed")


def test_tampered_records_fail_as_storage_error(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    _, target = native
    attention.observe(target, "session:one", "block:one")
    path = attention._path(target)
    document = json.loads(path.read_text())
    for row in document["rows"].values():
        row["records"] = None
    path.write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match="records must be a list"):
        attention.listing(target)


def test_unknown_after_connect_keeps_evidence_and_observed_binding(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    backend, target = native
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    previous = rows(target)[0]
    backend.unreadable = True
    assert attention.refresh(target, identity)["state"] == "fault"
    row = rows(target)[0]
    assert row["connection"] == "unknown"
    assert row["records"] == previous["records"]
    assert row["observed_at"] == previous["observed_at"]
    assert row["binding"] == "bound"
    backend.unreadable = False
    backend.present = False
    attention.refresh(target, identity)
    assert rows(target)[0]["connection"] == "closed"
    assert rows(target)[0]["binding"] == "bound"


@pytest.mark.parametrize("change", ["host", "detach"])
def test_jump_revalidates_host_and_placement_after_observation(
    native: tuple[Native, settings.SavedTarget],
    change: str,
) -> None:
    backend, target = native
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    if change == "host":
        backend.host = "different-host"
    else:
        backend.placement = "detached"
    with pytest.raises(RuntimeError, match="host changed|closed or unbound"):
        attention.jump(target, identity)
    assert not backend.focused


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_retention_cap_refusal_preserves_existing_observations(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    _, target = native
    attention.observe(target, "session:one", "block:one")
    before = attention._path(target).read_bytes()
    if limit == "rows":
        monkeypatch.setattr(attention, "_MAX_ROWS", 1)
    else:
        monkeypatch.setattr(attention, "_MAX_BYTES", len(before) + 64)
    with pytest.raises(RuntimeError, match="retention limit"):
        attention.observe(target, "session:two", "block:two")
    assert attention._path(target).read_bytes() == before


def test_oversized_explicit_metadata_cannot_poison_sibling_retention(
    native: tuple[Native, settings.SavedTarget],
) -> None:
    _, target = native
    with pytest.raises(RuntimeError, match="metadata exceeds"):
        attention.observe(target, "session:one", "block:one", "p" * 20000)
    assert (
        attention.observe(target, "session:two", "block:two", "normal")["state"]
        == "observed"
    )


def test_project_label_carries_from_newest_prior_incarnation(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, target = native
    for clock, instance, label in (
        (1, "instance-one", "old"),
        (2, "instance-two", "new"),
    ):
        monkeypatch.setattr(time, "time", lambda: float(clock))
        backend.instance = instance
        attention.observe(target, "session:one", "block:one", label)
    backend.instance = "instance-three"
    assert (
        cast(
            dict[str, object],
            attention.observe(target, "session:one", "block:one")["row"],
        )["project"]
        == "new"
    )


def test_cli_refresh_exit_codes_streams_and_recursive_json_failure(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend, target = native
    monkeypatch.setattr(settings, "load", lambda path: target)
    identity = str(attention.observe(target, "session:one", "block:one")["id"])
    monkeypatch.setattr(sys, "argv", ["rexdeck", "attention", "observe", identity])
    assert cli.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["state"] == "observed"
    assert not captured.err
    backend.connected = False
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["state"] == "fault"
    assert not captured.err
    monkeypatch.setattr(sys, "argv", ["rexdeck", "attention", "observe", "bad-id"])
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["state"] == "failed"
    path = attention._path(target)
    path.write_text('{"version":1,"rows":' + "[" * 2000 + "0" + "]" * 2000 + "}")
    monkeypatch.setattr(sys, "argv", ["rexdeck", "attention", "list"])
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["state"] == "failed"
    assert "Traceback" not in captured.err

"""Native evidence remains distinct from identity guesses and focus fallbacks."""

from __future__ import annotations

import fcntl
import io
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


def hook_payload(event: str = "SubagentStart") -> dict[str, object]:
    return {
        "hook_event_name": event,
        "session_id": "parent-provider-id",
        "agent_id": "child-provider-id",
        "agent_type": "Explore",
        "cwd": "/project/worktree",
        "turn_id": "must-not-retain",
        "transcript_path": "/private/parent-transcript",
        "agent_transcript_path": "/private/child-transcript",
        "last_assistant_message": "private response must not be retained",
    }


def hook_environment(
    monkeypatch: pytest.MonkeyPatch, target: settings.SavedTarget, *, bound: bool = True
) -> None:
    for variable, value in (
        ("REX_SERVER", target.server),
        ("REX_SESSION", "session:one"),
        ("REX_BLOCK", "block:one"),
    ):
        if bound:
            monkeypatch.setenv(variable, value)
        else:
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(settings, "load", lambda path: target)


def invoke_hook(
    monkeypatch: pytest.MonkeyPatch, raw: bytes, provider: str = "claude-code"
) -> int:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    monkeypatch.setattr(sys, "argv", ["rexdeck", "attention", "hook", provider])
    return cli.main()


@pytest.mark.parametrize("provider", attention.PROVIDERS)
def test_provider_lifecycle_parentage_privacy_and_exact_navigation(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode(), provider) == 0
    row = rows(target, provider=provider, parent="parent-provider-id")[0]
    assert row["state"] == "started" and row["outcome"] == "unknown"
    assert row["agent_id"] == "child-provider-id"
    assert row["rex_server"] == target.server
    assert row["session_id"] == "session:one" and row["block_id"] == "block:one"
    assert row["instance_id"] == "instance-one" and row["binding"] == "bound"
    assert row["last_message_present"] and row["agent_transcript_present"]
    assert row["transcript_present"]
    assert not rows(target, provider=provider, agent="another-child")
    document = attention._path(target, provider_store=True).read_text()
    for secret in (
        "private response",
        "/private/parent-transcript",
        "/private/child-transcript",
        "must-not-retain",
        "turn_id",
    ):
        assert secret not in document
    listing = attention.listing(target, provider=provider)
    parents = cast(list[dict[str, object]], listing["parents"])
    assert parents[0]["parent_session_id"] == "parent-provider-id"
    assert parents[0]["children"] == [row["id"]]
    attention.jump(target, str(row["id"]))
    assert backend.focused == [{"block_id": "block:one"}]
    with pytest.raises(RuntimeError, match="Unknown attention"):
        attention.refresh(target, str(row["id"]))
    assert (
        invoke_hook(
            monkeypatch, json.dumps(hook_payload("SubagentStop")).encode(), provider
        )
        == 0
    )
    stopped = rows(target, provider=provider)[0]
    assert stopped["id"] == row["id"] and stopped["state"] == "stopped"
    assert stopped["outcome"] == "unknown"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "failure", ["unbound", "foreign", "closed", "detached", "offline"]
)
def test_provider_unfocusable_binding_preserves_metadata(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target, bound=failure != "unbound")
    if failure == "foreign":
        monkeypatch.setenv("REX_SERVER", "unix:///different.sock")
    elif failure == "closed":
        backend.present = False
    elif failure == "detached":
        backend.placement = "detached"
    elif failure == "offline":
        backend.connected = False
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    row = rows(target, provider="claude-code")[0]
    assert row["binding"] == "unbound" and row["agent_id"] == "child-provider-id"
    with pytest.raises(RuntimeError, match="unbound"):
        attention.jump(target, str(row["id"]))
    assert not backend.focused


@pytest.mark.parametrize("change", ["instance", "host", "closure", "placement"])
def test_provider_jump_revalidates_captured_binding(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target)
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    row = rows(target, provider="claude-code")[0]
    if change == "instance":
        backend.instance = "replacement-instance"
    elif change == "host":
        backend.host = "replacement-host"
    elif change == "closure":
        backend.present = False
    else:
        backend.placement = "detached"
    with pytest.raises(RuntimeError, match="no focus attempted"):
        attention.jump(target, str(row["id"]))
    assert not backend.focused


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON",
        b"[]",
        b"\xff",
        b"[" * 2000,
        b"x" * (attention._MAX_HOOK_BYTES + 1),
        b'{"hook_event_name":"SubagentStart"}',
        b'{"hook_event_name":"Stop"}',
    ],
)
def test_hook_malformed_input_is_silent_and_does_not_corrupt(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: bytes,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    attention.observe(target, "session:one", "block:one")
    before = attention._path(target).read_bytes()
    assert invoke_hook(monkeypatch, raw) == 0
    assert attention._path(target).read_bytes() == before
    assert capsys.readouterr() == ("", "")


def test_hook_timeout_lock_and_native_deadline_are_fail_open(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    attention.observe(target, "session:one", "block:one")
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    path = attention._path(target, provider_store=True)
    before = path.read_bytes()
    with path.with_suffix(".lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(attention, "_HOOK_SAVE_MARGIN", 0.98)
        assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    assert path.read_bytes() == before
    monkeypatch.setattr(attention, "_HOOK_SAVE_MARGIN", 0.05)
    monkeypatch.setattr(attention, "_BINDING_SECONDS", 0.02)

    def blocked(setup: lifecycle.Setup) -> str:
        time.sleep(1)
        raise AssertionError("Hook deadline did not interrupt native lookup")

    monkeypatch.setattr(attention, "_status", blocked)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    timed_out = rows(target, provider="claude-code")[0]
    assert timed_out["binding"] == "unbound"
    assert timed_out["binding_reason"] == "verification-timeout"
    assert len(rows(target)) == 2
    assert capsys.readouterr() == ("", "")


def test_hook_stdin_deadline_and_missing_setup_are_silent(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    monkeypatch.setattr(settings, "load", lambda path: None)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    assert not attention._path(target).exists()
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "r") as source:
            monkeypatch.setattr(sys, "stdin", source)
            monkeypatch.setattr(attention, "_HOOK_SECONDS", 0.02)
            assert attention.hook("codex") == 0
    finally:
        os.close(write_fd)
    assert capsys.readouterr() == ("", "")


def test_native_v1_unchanged_and_provider_rollback_preserves_native_and_sibling(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    native_id = attention.observe(target, "session:one", "block:one")["id"]
    path = attention._path(target)
    before = path.read_bytes()
    document = json.loads(before)
    assert document["version"] == 1
    assert all("row_type" not in row for row in document["rows"].values())
    for provider in attention.PROVIDERS:
        invoke_hook(monkeypatch, json.dumps(hook_payload()).encode(), provider)
    assert path.read_bytes() == before
    provider_document = json.loads(
        attention._path(target, provider_store=True).read_text()
    )
    assert provider_document["version"] == 1
    assert provider_document["kind"] == "provider-subagents"
    result = attention.clear_provider(target, "claude-code")
    assert result["removed"] == 1
    assert not rows(target, provider="claude-code")
    assert len(rows(target, provider="codex")) == 1
    assert any(row["id"] == native_id for row in rows(target))
    assert path.read_bytes() == before
    with attention._store(target, provider_store=True) as retained:
        for row in retained.values():
            row["observed_at"] = time.time() - 61
        attention._save(target, retained, provider_store=True)
    assert rows(target, provider="codex")[0]["freshness"] == "stale"


def test_provider_nullable_fields_empty_agent_type_and_namespaced_ids(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    payload = hook_payload("SubagentStop")
    payload.update(
        agent_type="",
        transcript_path=None,
        agent_transcript_path=None,
        last_assistant_message=None,
    )
    for provider in attention.PROVIDERS:
        invoke_hook(monkeypatch, json.dumps(payload).encode(), provider)
    retained = rows(target)
    assert len(retained) == 2 and retained[0]["id"] != retained[1]["id"]
    assert all(row["agent_type"] == "" for row in retained)
    assert all(not row["last_message_present"] for row in retained)
    assert all(not row["transcript_present"] for row in retained)


def test_hook_exact_saved_endpoint_verification_without_rex_server(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target)
    monkeypatch.delenv("REX_SERVER")
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    row = rows(target, provider="claude-code")[0]
    assert row["rex_server"] is None
    assert row["endpoint"] == target.server
    assert row["binding"] == "bound" and row["binding_reason"] == "verified"
    attention.jump(target, str(row["id"]))
    assert backend.focused == [{"block_id": "block:one"}]


@pytest.mark.parametrize("endpoint", ["unix:///foreign.sock", "not-a-unix-endpoint"])
def test_hook_conflicting_endpoint_retains_unbound_reason(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    monkeypatch.setenv("REX_SERVER", endpoint)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    row = rows(target, provider="claude-code")[0]
    assert row["binding"] == "unbound" and row["binding_reason"] == "endpoint-mismatch"
    assert row["rex_server"] == endpoint


@pytest.mark.parametrize("failure", ["id-size", "cwd-size", "row-limit", "provider"])
def test_hook_invalid_fields_and_retention_limits_cannot_poison_native_evidence(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    attention.observe(target, "session:one", "block:one")
    before = attention._path(target).read_bytes()
    payload = hook_payload()
    if failure == "id-size":
        payload["agent_id"] = "a" * 1025
    elif failure == "cwd-size":
        payload["cwd"] = "a" * 4097
    elif failure == "row-limit":
        monkeypatch.setattr(attention, "_MAX_PROVIDER_ROWS", 0)
    provider = "omp" if failure == "provider" else "claude-code"
    assert invoke_hook(monkeypatch, json.dumps(payload).encode(), provider) == 0
    assert attention._path(target).read_bytes() == before
    assert capsys.readouterr() == ("", "")


def test_delayed_lookup_cannot_overwrite_newer_receipt_after_clock_rollback(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    original_block = attention._block
    after_newer: list[bytes] = []
    monkeypatch.setattr(time, "time", lambda: 100.0)

    def interleave(
        setup: lifecycle.Setup, session: str, block: str
    ) -> dict[str, object] | None:
        found = original_block(setup, session, block)
        with monkeypatch.context() as newer:
            newer.setattr(time, "time", lambda: 90.0)
            newer.setenv("REX_SERVER", "unix:///other.sock")
            attention._receive_hook(target, "claude-code", hook_payload("SubagentStop"))
        after_newer.append(attention._path(target, provider_store=True).read_bytes())
        return found

    monkeypatch.setattr(attention, "_block", interleave)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    assert attention._path(target, provider_store=True).read_bytes() == after_newer[0]
    row = rows(target)[0]
    assert row["state"] == "stopped" and row["observed_at"] == 90.0
    assert row["receipt_order"] == 2


def test_full_provider_quota_never_consumes_native_capacity(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    for index in range(attention._MAX_PROVIDER_ROWS):
        payload = hook_payload()
        payload["agent_id"] = f"child-{index}"
        attention._receive_hook(target, "codex", payload)
    provider_path = attention._path(target, provider_store=True)
    before = provider_path.read_bytes()
    overflow = hook_payload()
    overflow["agent_id"] = "one-too-many"
    attention._receive_hook(target, "codex", overflow)
    assert provider_path.read_bytes() == before
    result = attention.observe(target, "session:one", "block:one")
    assert result["state"] == "observed"
    assert len(rows(target, provider="codex")) == attention._MAX_PROVIDER_ROWS
    assert attention.clear_provider(target, "codex")["removed"] == 64
    attention._receive_hook(target, "codex", overflow)
    assert len(rows(target, provider="codex")) == 1


def test_hook_large_stop_message_keeps_metadata_and_drains_oversize_input(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    payload = hook_payload("SubagentStop")
    payload["last_assistant_message"] = "private-message-" * 10000
    assert invoke_hook(monkeypatch, json.dumps(payload).encode()) == 0
    assert rows(target)[0]["state"] == "stopped"
    assert rows(target)[0]["last_message_present"]
    path = attention._path(target, provider_store=True)
    assert "private-message-" not in path.read_text()
    before = path.read_bytes()
    source = io.TextIOWrapper(io.BytesIO(b"x" * (attention._MAX_HOOK_BYTES + 65536)))
    monkeypatch.setattr(sys, "stdin", source)
    assert attention.hook("claude-code") == 0
    assert source.buffer.read() == b""
    assert path.read_bytes() == before


def test_native_near_16k_v1_row_is_not_changed_by_provider_receipts(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    attention.observe(target, "session:one", "block:one")
    path = attention._path(target)
    document = json.loads(path.read_text())
    row = next(iter(document["rows"].values()))
    row["project"] = ""
    size = len(json.dumps(row, ensure_ascii=True).encode())
    row["project"] = "x" * (attention._MAX_ROW_BYTES - size)
    assert len(json.dumps(row, ensure_ascii=True).encode()) == attention._MAX_ROW_BYTES
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    assert rows(target)[0]["row_type"] == "native"
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    assert path.read_bytes() == before


@pytest.mark.parametrize("variable", ["REX_SERVER", "REX_SESSION", "REX_BLOCK"])
def test_empty_inherited_rex_value_retains_unbound_receipt(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    monkeypatch.setenv(variable, "")
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    row = rows(target)[0]
    assert row["binding"] == "unbound"
    expected = (
        "endpoint-mismatch" if variable == "REX_SERVER" else "missing-identifiers"
    )
    assert row["binding_reason"] == expected


def test_parallel_hook_receivers_wait_for_remaining_deadline_and_both_retain(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    attention._receive_hook(target, "codex", hook_payload())
    path = attention._path(target, provider_store=True)
    code = (
        "from pathlib import Path; from rexdeck import attention,lifecycle,settings; "
        f"target=settings.SavedTarget(Path({str(target.workspace)!r}),"
        f"Path({str(target.data_root)!r}),Path({str(target.config)!r}),"
        f"Path({str(target.config_target)!r}),{target.server!r},False); "
        "settings.SavedTarget.setup=lambda self: lifecycle.Setup("
        "Path('/tmp'),target.workspace,target.config,target.data_root,target.server); "
        "settings.load=lambda path: target; raise SystemExit(attention.hook('codex'))"
    )
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("REX_")
    }
    env["PYTHONPATH"] = str(Path(attention.__file__).parent.parent)
    children: list[subprocess.Popen[bytes]] = []
    try:
        with path.with_suffix(".lock").open("r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for index in range(2):
                payload = hook_payload()
                payload["agent_id"] = f"parallel-child-{index}"
                child = subprocess.Popen(
                    [sys.executable, "-c", code],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                children.append(child)
                assert child.stdin is not None
                child.stdin.write(json.dumps(payload).encode())
                child.stdin.close()
                child.stdin = None
            time.sleep(0.3)
        for child in children:
            stdout, stderr = child.communicate(timeout=3)
            assert child.returncode == 0 and not stdout and not stderr
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    assert len(rows(target, provider="codex")) == 3


def test_provider_byte_limit_does_not_reduce_native_byte_budget(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    monkeypatch.setattr(attention, "_MAX_PROVIDER_BYTES", 4096)
    for index in range(10):
        payload = hook_payload()
        payload["agent_id"] = f"byte-cap-child-{index}"
        invoke_hook(monkeypatch, json.dumps(payload).encode())
    provider_path = attention._path(target, provider_store=True)
    assert provider_path.stat().st_size <= 4096
    assert 0 < len(rows(target, provider="claude-code")) < 10
    assert attention._MAX_BYTES == 2 * 1024 * 1024
    assert attention.observe(target, "session:one", "block:one")["state"] == "observed"


@pytest.mark.parametrize(
    "kind", ["storage-symlink", "storage-fifo", "lock-symlink", "lock-fifo"]
)
def test_provider_store_rejects_redirected_files_without_blocking_native_observe(
    native: tuple[Native, settings.SavedTarget], kind: str
) -> None:
    _, target = native
    path = attention._path(target, provider_store=True)
    path.parent.mkdir(parents=True)
    selected = path.with_suffix(".lock") if kind.startswith("lock") else path
    if kind.endswith("fifo"):
        os.mkfifo(selected)
    else:
        foreign = path.with_name("foreign-file")
        foreign.write_text("{}")
        selected.symlink_to(foreign)
    with (
        pytest.raises((RuntimeError, OSError)),
        attention._store(target, provider_store=True),
    ):
        raise AssertionError("Provider store accepted a redirected file")
    assert attention.observe(target, "session:one", "block:one")["state"] == "observed"
    listing = attention.listing(target)
    assert listing["state"] == "fault" and listing["provider_state"] == "fault"
    assert listing["provider_error"]
    assert len(cast(list[dict[str, object]], listing["rows"])) == 1
    cleared = attention.clear_provider(target, "codex")
    assert cleared["state"] == "fault" and cleared["provider_state"] == "fault"
    assert cleared["provider_error"] and "removed" not in cleared


@pytest.mark.parametrize(
    "fault",
    ["malformed-json", "invalid-kind", "bad-row", "empty-key", "nul-key", "size"],
)
def test_provider_store_faults_preserve_native_cli_output_and_allow_explicit_recovery(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    native_id = attention.observe(target, "session:one", "block:one")["id"]
    attention._receive_hook(target, "codex", hook_payload())
    path = attention._path(target, provider_store=True)
    if fault == "malformed-json":
        path.write_text("{")
    elif fault == "invalid-kind":
        path.write_text('{"version":1,"kind":"foreign","rows":{}}')
    elif fault == "bad-row":
        document = json.loads(path.read_text())
        next(iter(document["rows"].values()))["binding_reason"] = []
        path.write_text(json.dumps(document))
    elif fault in ("empty-key", "nul-key"):
        document = json.loads(path.read_text())
        key, row = next(iter(document["rows"].items()))
        del document["rows"][key]
        document["rows"]["" if fault == "empty-key" else "bad\x00key"] = row
        path.write_text(json.dumps(document))
    else:
        path.write_bytes(b"x" * (attention._MAX_PROVIDER_BYTES + 1))
    provider_before = path.read_bytes()
    native_before = attention._path(target).read_bytes()
    monkeypatch.setattr(sys, "argv", ["rexdeck", "attention", "list"])
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert result["provider_state"] == "fault" and result["provider_error"]
    assert result["rows"][0]["id"] == native_id
    monkeypatch.setattr(
        sys, "argv", ["rexdeck", "attention", "clear", "--provider", "codex"]
    )
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert not captured.err and json.loads(captured.out)["provider_state"] == "fault"
    assert path.read_bytes() == provider_before
    assert attention._path(target).read_bytes() == native_before
    path.unlink()  # Explicit operator recovery, with no hooks/writers running.
    assert attention.clear_provider(target, "codex")["provider_state"] == "ready"
    assert attention.listing(target)["provider_state"] == "ready"
    assert attention._path(target).read_bytes() == native_before


def test_provider_jump_releases_store_lock_before_all_native_ipc(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target)
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    identity = str(rows(target, provider="claude-code")[0]["id"])
    path = attention._path(target, provider_store=True).with_suffix(".lock")
    checked: list[str] = []

    def assert_unlocked(stage: str) -> None:
        with path.open("r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checked.append(stage)

    def status(setup: lifecycle.Setup, *, selected: bool) -> dict[str, object]:
        assert_unlocked("status")
        return backend.status(setup, selected=selected)

    def rex(setup: lifecycle.Setup, *args: str) -> dict[str, object]:
        assert_unlocked("rex")
        return backend.rex(setup, *args)

    monkeypatch.setattr(lifecycle, "_server_status", status)
    monkeypatch.setattr(lifecycle, "_rex", rex)
    attention.jump(target, identity)
    assert checked == ["status", "rex", "rex", "rex"]
    assert backend.focused == [{"block_id": "block:one"}]


def test_stuck_provider_writer_returns_native_rows_with_bounded_fault(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    attention.observe(target, "session:one", "block:one")
    attention._receive_hook(target, "codex", hook_payload())
    lock_path = attention._path(target, provider_store=True).with_suffix(".lock")
    monkeypatch.setattr(attention, "_PROVIDER_READ_LOCK_SECONDS", 0.02)
    with lock_path.open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        listing = attention.listing(target)
        assert time.monotonic() - started < 0.3
        assert listing["provider_state"] == "fault"
        assert "timed out" in str(listing["provider_error"])
        assert len(cast(list[object], listing["rows"])) == 1
    assert attention.listing(target)["provider_state"] == "ready"


def test_receipt_reserved_before_store_reset_is_dropped_not_corrupting(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    for _ in range(3):
        attention._receive_hook(target, "codex", hook_payload())
    reserve = attention._reserve_receipt

    def reserve_then_reset(
        store_target: settings.SavedTarget, identity: str
    ) -> int | None:
        sequence = reserve(store_target, identity)
        attention._path(store_target, provider_store=True).unlink()
        return sequence

    monkeypatch.setattr(attention, "_reserve_receipt", reserve_then_reset)
    attention._receive_hook(target, "codex", hook_payload())
    listing = attention.listing(target)
    assert listing["provider_state"] == "ready"


def test_hook_deadline_during_endpoint_validation_is_not_swallowed(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    budgets: list[float] = []
    original_budget = attention._hook_lock_budget

    def budget() -> float:
        result = original_budget()
        budgets.append(result)
        return result

    def expired(server: str) -> str:
        raise attention._HookDeadline("Endpoint deadline")

    monkeypatch.setattr(attention, "_hook_lock_budget", budget)
    monkeypatch.setattr(lifecycle, "_canonical_server", expired)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    assert len(budgets) == 1  # Reservation only; no post-expiry lookup/store wait.
    assert not rows(target, provider="claude-code")
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    assert capsys.readouterr() == ("", "")


def test_binding_timeout_after_capture_clears_focusable_fields(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, target = native
    hook_environment(monkeypatch, target)

    @contextmanager
    def expire_after_capture() -> Iterator[None]:
        yield
        raise attention._BindingDeadline("Expired after row capture")

    monkeypatch.setattr(attention, "_binding_budget", expire_after_capture)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    row = rows(target)[0]
    assert (
        row["binding"] == "unbound" and row["binding_reason"] == "verification-timeout"
    )
    assert row["instance_id"] is None and row["host_id"] is None
    with pytest.raises(RuntimeError, match="unbound"):
        attention.jump(target, str(row["id"]))
    assert not backend.focused


@pytest.mark.parametrize("late_binding", [False, True])
def test_overall_hook_deadline_interrupts_native_subprocess_cleanup(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    late_binding: bool,
) -> None:
    _, target = native
    hook_environment(monkeypatch, target)
    read_fd, write_fd = os.pipe()
    release_wait = threading.Event()
    reaped = threading.Event()

    class SlowCleanup:
        def __init__(self) -> None:
            self.stdout = os.fdopen(read_fd, "rb")
            self.killed = False

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            release_wait.wait(2)
            reaped.set()
            return 0

    child = SlowCleanup()

    def popen(*args: object, **kwargs: object) -> SlowCleanup:
        return child

    def blocked(setup: lifecycle.Setup) -> str:
        lifecycle._run(["fake-native"], {})
        raise AssertionError("Native cleanup exceeded the receiver deadline")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(attention, "_status", blocked)
    monkeypatch.setattr(attention, "_HOOK_SECONDS", 0.18)
    monkeypatch.setattr(attention, "_BINDING_SECONDS", 0.03)
    if late_binding:
        original_timer = signal.setitimer

        def delayed_timer(
            which: int, seconds: float, interval: float = 0
        ) -> tuple[float, float]:
            if which == signal.ITIMER_REAL and seconds == 0.03:
                seconds = 0.25  # Deliver the binding alarm after the overall deadline.
            return original_timer(which, seconds, interval)

        monkeypatch.setattr(signal, "setitimer", delayed_timer)
    try:
        started = time.monotonic()
        assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
        assert time.monotonic() - started < 0.8
        assert child.killed
        assert not reaped.is_set()
    finally:
        release_wait.set()
        assert reaped.wait(1)
        child.stdout.close()
        os.close(write_fd)
    assert capsys.readouterr() == ("", "")


def test_hook_deadline_in_cleanup_is_silent_and_cannot_escape(
    native: tuple[Native, settings.SavedTarget],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    original = signal.setitimer
    previous_handler = signal.getsignal(signal.SIGALRM)
    injected: list[bool] = []

    def alarm(which: int, seconds: float, interval: float = 0) -> tuple[float, float]:
        result = original(which, seconds, interval)
        if seconds == 0 and not injected:
            injected.append(True)
            raise attention._HookDeadline("Pending deadline during cleanup")
        return result

    monkeypatch.setattr(signal, "setitimer", alarm)
    assert invoke_hook(monkeypatch, json.dumps(hook_payload()).encode()) == 0
    assert injected and signal.getsignal(signal.SIGALRM) == previous_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    assert capsys.readouterr() == ("", "")


def test_newer_receipt_wins_when_wall_clock_moves_backwards(
    native: tuple[Native, settings.SavedTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = native
    hook_environment(monkeypatch, target, bound=False)
    monkeypatch.setattr(time, "time", lambda: 100.0)
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    monkeypatch.setattr(time, "time", lambda: 90.0)
    invoke_hook(monkeypatch, json.dumps(hook_payload("SubagentStop")).encode())
    row = rows(target)[0]
    assert row["state"] == "stopped" and row["observed_at"] == 90.0
    assert row["receipt_order"] == 2
    attention.clear_provider(target, "claude-code")
    invoke_hook(monkeypatch, json.dumps(hook_payload()).encode())
    assert rows(target)[0]["receipt_order"] == 3

"""Bounded native terminal evidence and opt-in provider-reported parentage."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import signal
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from rexdeck import lifecycle, settings

_MAX_BYTES = 2 * 1024 * 1024
_MAX_ROWS = 256
_MAX_PROVIDER_ROWS = 64
_MAX_PROVIDER_BYTES = 256 * 1024
_MAX_ROW_BYTES = 16 * 1024
_MAX_ERROR = 1024
_FIELD_LIMITS = {
    "id": 256,
    "app": 128,
    "effective_app": 128,
    "kind": 64,
    "msg": 1024,
    "title": 256,
    "state": 32,
    "updated_at": 64,
}
FRESH_SECONDS = 60
PROVIDERS = ("claude-code", "codex")
_MAX_HOOK_BYTES = 2 * 1024 * 1024
_HOOK_SECONDS = 1.0
_HOOK_SAVE_MARGIN = 0.05
_BINDING_SECONDS = 0.35
_PROVIDER_READ_LOCK_SECONDS = 0.1
_BINDING_REASONS = {
    "verified",
    "missing-identifiers",
    "endpoint-mismatch",
    "verification-failed",
    "verification-timeout",
    "exact-block-absent",
    "detached",
}


class _HookDeadline(lifecycle.CommandDeadline):
    """Interrupt native subprocess waits while retaining their cleanup path."""


class _BindingDeadline(lifecycle.CommandDeadline):
    """Bound receipt-time lookup without spending the receiver's storage budget."""


class _ProviderRows(dict[str, dict[str, object]]):
    def __init__(self) -> None:
        super().__init__()
        self.sequence = 0


def _receipt_number(value: object) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeError("Invalid provider receipt sequence")
    return value


@contextmanager
def _binding_budget() -> Iterator[None]:
    previous = signal.getsignal(signal.SIGALRM)
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(signum: int, frame: object) -> None:
        # The binding alarm is one-shot; preserve the overall deadline for
        # the receiver work that remains after binding cancellation.
        if remaining:
            left = remaining - (time.monotonic() - started)
            if left <= 0:
                raise _HookDeadline("Provider hook deadline")
            signal.signal(signal.SIGALRM, previous)
            signal.setitimer(signal.ITIMER_REAL, left, interval)
        raise _BindingDeadline("Provider binding deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(
        signal.ITIMER_REAL,
        min(remaining, _BINDING_SECONDS) if remaining else _BINDING_SECONDS,
    )
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if remaining:
            left = remaining - (time.monotonic() - started)
            if left <= 0:
                raise _HookDeadline("Provider hook deadline")
            signal.setitimer(signal.ITIMER_REAL, left, interval)


def _hook_text(value: object, limit: int = 1024, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not value and not empty)
        or "\x00" in value
        or len(value) > limit
    ):
        raise RuntimeError("Invalid or oversized provider hook field")
    return value


def _validate_provider(row: dict[str, object]) -> None:
    """Validate the separate provider-store schema without adopting native IDs."""
    if row.get("provider") not in PROVIDERS:
        raise RuntimeError("Invalid attention provider")
    if row.get("event") not in ("SubagentStart", "SubagentStop"):
        raise RuntimeError("Invalid provider event")
    expected = "started" if row["event"] == "SubagentStart" else "stopped"
    if row.get("state") != expected or row.get("outcome") != "unknown":
        raise RuntimeError("Invalid provider lifecycle state")
    for field in ("parent_session_id", "agent_id"):
        _hook_text(row.get(field))
    _hook_text(row.get("agent_type"), empty=True)
    _hook_text(row.get("cwd"), 4096)
    _text(row.get("endpoint"))
    _receipt_number(row.get("receipt_order", 0))
    for field in (
        "last_message_present",
        "transcript_present",
        "agent_transcript_present",
    ):
        if type(row.get(field)) is not bool:
            raise RuntimeError("Invalid provider presence flag")
    for field in ("rex_server", "instance_id", "session_id", "block_id", "host_id"):
        if row.get(field) is not None:
            _hook_text(row[field], 4096, empty=True)
    if row.get("binding") not in ("bound", "unbound"):
        raise RuntimeError("Invalid provider binding")
    if row.get("binding_reason") not in _BINDING_REASONS:
        raise RuntimeError("Invalid provider binding reason")
    if row["binding"] == "bound":
        for field in ("instance_id", "session_id", "block_id", "host_id"):
            _text(row.get(field))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise RuntimeError("Invalid attention object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("Attention requires nonempty string identities")
    return value


def _path(target: settings.SavedTarget, *, provider_store: bool = False) -> Path:
    name = "providers.json" if provider_store else "attention.json"
    return target.workspace / "attention" / name


def _timestamp(value: object) -> float:
    try:
        if type(value) not in (int, float):
            raise ValueError
        result = float(cast(float, value))
        if not math.isfinite(result):
            raise ValueError
        return result
    except (ValueError, OverflowError) as error:
        raise RuntimeError("Invalid attention observation timestamp") from error


@contextmanager
def _store(
    target: settings.SavedTarget,
    *,
    provider_store: bool = False,
    lock_timeout: float | None = None,
) -> Iterator[dict[str, dict[str, object]]]:
    """Validate frozen workspace and read only bounded regular non-symlink files."""
    target.setup()
    path = _path(target, provider_store=provider_store)
    max_bytes = _MAX_PROVIDER_BYTES if provider_store else _MAX_BYTES
    max_rows = _MAX_PROVIDER_ROWS if provider_store else _MAX_ROWS
    size_label = "256 KiB (providers)" if provider_store else "2 MiB"
    if provider_store and lock_timeout is None:
        lock_timeout = _PROVIDER_READ_LOCK_SECONDS
    if path.parent.resolve() != path.parent:
        raise RuntimeError("Attention storage directory must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(
        path.with_suffix(".lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    with os.fdopen(lock_descriptor, "r+b", buffering=0) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise RuntimeError("Attention lock must be a regular non-symlink file")
        os.fchmod(handle.fileno(), 0o600)
        if lock_timeout is None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + lock_timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Attention lock timed out") from None
                    time.sleep(0.005)
        rows: dict[str, dict[str, object]] = _ProviderRows() if provider_store else {}
        descriptor: int | None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            descriptor = None
        if descriptor is not None:
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeError(
                        "Attention storage must be a regular non-symlink file"
                    )
                if info.st_size > max_bytes:
                    raise RuntimeError(f"Attention storage exceeds {size_label}")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    raw = source.read(max_bytes + 1)
            finally:
                os.close(descriptor)
            if len(raw) > max_bytes:
                raise RuntimeError(f"Attention storage exceeds {size_label}")
            document = _object(json.loads(raw))
            version = document.get("version")
            if type(version) is not int or version != 1:
                raise RuntimeError("Unsupported attention storage version")
            if provider_store and document.get("kind") != "provider-subagents":
                raise RuntimeError("Invalid provider attention storage kind")
            if provider_store:
                cast(_ProviderRows, rows).sequence = _receipt_number(
                    document.get("receipt_sequence", 0)
                )
            for key, value in _object(document.get("rows")).items():
                row = _object(value)
                if provider_store:
                    _text(key)
                    if row.get("row_type") != "provider":
                        raise RuntimeError("Invalid provider attention row type")
                    _validate_provider(row)
                    if (
                        _receipt_number(row.get("receipt_order", 0))
                        > cast(_ProviderRows, rows).sequence
                    ):
                        raise RuntimeError("Provider receipt exceeds store sequence")
                else:
                    for field in ("endpoint", "instance_id", "session_id", "block_id"):
                        _text(row.get(field))
                    if not isinstance(row.get("records", []), list):
                        raise RuntimeError("Attention row records must be a list")
                _timestamp(row.get("observed_at"))
                if len(json.dumps(row, ensure_ascii=True).encode()) > _MAX_ROW_BYTES:
                    raise RuntimeError("Attention row exceeds 16 KiB")
                rows[key] = row
        if len(rows) > max_rows:
            raise RuntimeError(f"Attention storage exceeds {max_rows} retained rows")
        yield rows


def _bounded_row(row: dict[str, object]) -> dict[str, object]:
    """Bound untrusted terminal reports independently of other terminal rows."""
    if row.get("row_type") == "provider":
        _validate_provider(row)
        if len(json.dumps(row, ensure_ascii=True).encode()) > _MAX_ROW_BYTES:
            raise RuntimeError("Provider metadata exceeds the 16 KiB row limit")
        return row
    truncated = False
    records: list[dict[str, object]] = []
    for value in cast(list[object], row.get("records", [])):
        native = _object(value)
        record: dict[str, object] = {}
        for field, limit in _FIELD_LIMITS.items():
            item = native.get(field)
            if isinstance(item, str):
                record[field] = item[:limit]
                truncated |= len(item) > limit
        for field in ("progress",):
            item = native.get(field)
            if item is None or type(item) is int:
                if field in native:
                    record[field] = item
        owner = native.get("owner")
        if isinstance(owner, dict):
            name = owner.get("name")
            if isinstance(name, str) and type(owner.get("pid")) is int:
                record["owner"] = {"pid": owner["pid"], "name": name[:128]}
                truncated |= len(name) > 128
        elif owner is None and "owner" in native:
            record["owner"] = None
        truncated |= bool(set(native) - set(_FIELD_LIMITS) - {"progress", "owner"})
        records.append(record)
    row["records"] = records
    last_exit = row.get("last_exit")
    if isinstance(last_exit, dict):
        safe_exit: dict[str, object] = {"exit_code": last_exit.get("exit_code")}
        if isinstance(last_exit.get("error"), str):
            detail = cast(str, last_exit["error"])
            safe_exit["error"] = detail[:_MAX_ERROR]
            truncated |= len(detail) > _MAX_ERROR
        row["last_exit"] = safe_exit
    if isinstance(row.get("error"), str):
        detail = cast(str, row["error"])
        row["error"] = detail[:_MAX_ERROR]
        truncated |= len(detail) > _MAX_ERROR
    row["truncated"] = bool(row.get("truncated")) or truncated
    while len(json.dumps(row, ensure_ascii=True).encode()) > _MAX_ROW_BYTES:
        if not records:
            raise RuntimeError("Attention metadata exceeds the 16 KiB row limit")
        records.pop()
        row["truncated"] = True
    return row


def _save(
    target: settings.SavedTarget,
    rows: dict[str, dict[str, object]],
    *,
    provider_store: bool = False,
) -> None:
    for key, row in rows.items():
        rows[key] = _bounded_row(row)
    document: dict[str, object] = {"version": 1, "rows": rows}
    if provider_store:
        document["kind"] = "provider-subagents"
        document["receipt_sequence"] = cast(_ProviderRows, rows).sequence
    data = json.dumps(document, ensure_ascii=True, allow_nan=False).encode()
    max_bytes = _MAX_PROVIDER_BYTES if provider_store else _MAX_BYTES
    max_rows = _MAX_PROVIDER_ROWS if provider_store else _MAX_ROWS
    if len(data) > max_bytes or len(rows) > max_rows:
        raise RuntimeError("Attention retention limit reached")
    path = _path(target, provider_store=provider_store)
    fd, name = tempfile.mkstemp(prefix=".attention-", dir=path.parent)
    directory: int | None = None
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(directory)
        os.replace(name, path)
        os.fsync(directory)
    finally:
        if directory is None:
            os.close(fd)
        else:
            os.close(directory)
        if os.path.exists(name):
            os.unlink(name)


def _status(setup: lifecycle.Setup) -> str:
    current = lifecycle._server_status(setup, selected=True)
    if (
        current is None
        or lifecycle._canonical_server(_text(current.get("endpoint"))) != setup.server
    ):
        raise RuntimeError("Selected attention endpoint is not available")
    return _text(current.get("instance_id"))


def _call(
    setup: lifecycle.Setup, session: str, method: str, args: dict[str, object]
) -> dict[str, object]:
    return lifecycle._rex(setup, "api", "call", "-s", session, method, json.dumps(args))


def _block(
    setup: lifecycle.Setup, session: str, block: str
) -> dict[str, object] | None:
    sessions = lifecycle._rex(setup, "ls", "--json").get("sessions")
    if not isinstance(sessions, list):
        raise RuntimeError("Invalid Rex session list")
    if not any(_object(item).get("session_id") == session for item in sessions):
        return None
    value = _call(setup, session, "session.list_blocks", {})
    blocks = value.get("blocks") or []
    if not isinstance(blocks, list):
        raise RuntimeError("Invalid Rex block list")
    for item in blocks:
        candidate = _object(item)
        if candidate.get("block_id") == block:
            return candidate
    return None


def observe(
    target: settings.SavedTarget, session: str, block: str, project: str | None = None
) -> dict[str, object]:
    """Read one explicitly identified terminal; retain prior evidence on disconnect."""
    if not session.startswith("session:") or not block.startswith("block:"):
        raise RuntimeError(
            "Use exact session: and block: IDs, not labels or similarity"
        )
    if project is not None:
        _text(project)
    setup = target.setup()
    with _store(target) as rows:
        prior = [
            row
            for row in rows.values()
            if row.get("endpoint") == setup.server
            and row.get("session_id") == session
            and row.get("block_id") == block
        ]
        try:
            instance = _status(setup)
        except (OSError, RuntimeError) as error:
            for previous in prior:
                previous.update(connection="disconnected", error=str(error))
            _save(target, rows)
            return {"state": "fault", "error": str(error)[:_MAX_ERROR], "rows": prior}
        key = hashlib.sha256(
            json.dumps([setup.server, instance, session, block]).encode()
        ).hexdigest()
        row = rows.get(key)
        newest = max(
            prior, key=lambda item: _timestamp(item["observed_at"]), default={}
        )
        for old in rows.values():
            if (
                old.get("endpoint") == setup.server
                and old.get("instance_id") != instance
            ):
                # A closure seen before the restart stays a closure.
                old.update(
                    connection="replaced",
                    target_state="closed"
                    if old.get("target_state") == "closed"
                    else "replaced",
                )
                old.pop("error", None)
        try:
            found = _block(setup, session, block)
            if found is None:
                if row is None:
                    raise RuntimeError(
                        "Exact block is absent; no observation to retain"
                    )
                row.update(connection="closed", target_state="closed")
                row.pop("error", None)
            else:
                status = _call(
                    setup,
                    session,
                    "com.superlogical.terminal.program_status",
                    {"block_id": block, "args": {}},
                )
                records = status.get("records")
                if not isinstance(records, list) or len(records) > 128:
                    raise RuntimeError("Invalid or oversized native status records")
                process = _call(
                    setup,
                    session,
                    "com.superlogical.terminal.process",
                    {"block_id": block, "args": {}},
                )
                row = {
                    "endpoint": setup.server,
                    "instance_id": instance,
                    "session_id": session,
                    "block_id": block,
                    "host_id": found.get("host_id"),
                    "project": project
                    if project is not None
                    else newest.get("project"),
                    "agent_identity": "unknown",
                    "identity_scope": "Rex terminal, not provider session",
                    "source": "com.superlogical.terminal.program_status (OSC 7501)",
                    "records": records,
                    "last_exit": process.get("last_exit"),
                    "observed_at": time.time(),
                    "connection": "connected",
                    "target_state": "present",
                    "binding": "bound"
                    if found.get("placement") == "placed"
                    else "unbound",
                    "window_id": found.get("window_id"),
                }
                rows[key] = row
        except (OSError, RuntimeError) as error:
            if row is not None:
                row.update(connection="unknown", error=str(error))
            _save(target, rows)
            return {
                "state": "fault",
                "error": str(error)[:_MAX_ERROR],
                "rows": [row] if row else [],
            }
        _save(target, rows)
        return {"state": "observed", "id": key, "row": row}


def refresh(
    target: settings.SavedTarget, identity: str, project: str | None = None
) -> dict[str, object]:
    """Recheck the exact retained row without requiring copied session/block IDs."""
    with _store(target) as rows:
        row = rows.get(identity)
        if row is None or row.get("endpoint") != target.server:
            raise RuntimeError("Unknown attention observation for the saved endpoint")
        session, block = _text(row.get("session_id")), _text(row.get("block_id"))
    return observe(target, session, block, project)


def _provider_fault(error: Exception) -> dict[str, object]:
    """Expose provider evidence failures without hiding healthy native rows."""
    return {
        "state": "fault",
        "provider_state": "fault",
        "provider_error": str(error)[:_MAX_ERROR],
    }


def listing(
    target: settings.SavedTarget,
    project: str | None = None,
    session: str | None = None,
    block: str | None = None,
    provider: str | None = None,
    parent: str | None = None,
    agent: str | None = None,
) -> dict[str, object]:
    """Local retained evidence only. Freshness never means the worker is alive."""
    result: list[dict[str, object]] = []
    provider_status: dict[str, object] = {"provider_state": "ready"}
    # Never hold both locks: native and provider stores have independent quotas.
    for provider_store in (False, True):
        try:
            with _store(target, provider_store=provider_store) as rows:
                for key, row in rows.items():
                    if row.get("endpoint") != target.server or any(
                        wanted is not None and row.get(field) != wanted
                        for field, wanted in (
                            ("project", project),
                            ("session_id", session),
                            ("block_id", block),
                            ("provider", provider),
                            ("parent_session_id", parent),
                            ("agent_id", agent),
                        )
                    ):
                        continue
                    age = max(0.0, time.time() - _timestamp(row["observed_at"]))
                    result.append(
                        {
                            **row,
                            "row_type": "provider" if provider_store else "native",
                            "id": key,
                            "age_seconds": round(age, 1),
                            "freshness": "stale" if age > FRESH_SECONDS else "recent",
                        }
                    )
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            if not provider_store:
                raise
            provider_status = _provider_fault(error)
    parents: dict[tuple[str, str], dict[str, object]] = {}
    for row in result:
        if row.get("row_type") == "provider":
            parent_key = (_text(row["provider"]), _text(row["parent_session_id"]))
            group = parents.setdefault(
                parent_key,
                {
                    "provider": parent_key[0],
                    "parent_session_id": parent_key[1],
                    "children": [],
                    "identity_scope": "provider-reported parent, not observed lifecycle",
                },
            )
            cast(list[str], group["children"]).append(_text(row["id"]))
    return {
        "state": "retained",
        "rows": result,
        "parents": list(parents.values()),
        **provider_status,
    }


def jump(target: settings.SavedTarget, identity: str) -> dict[str, object]:
    """Revalidate endpoint incarnation and exact placed block before native focus."""
    setup = target.setup()
    with _store(target, provider_store=identity.startswith("hook:")) as rows:
        row = rows.get(identity)
        if row is None or row.get("endpoint") != setup.server:
            raise RuntimeError("Unknown attention observation for the saved endpoint")
        if row.get("row_type") == "provider" and row.get("binding") != "bound":
            raise RuntimeError("Provider target is unbound; no focus attempted")
    # The retained row is a private in-memory snapshot. Never hold providers.lock
    # across Rex IPC: independent hook receipts must be able to update immediately.
    if _status(setup) != row.get("instance_id"):
        raise RuntimeError("Captured Rex server was replaced; no focus attempted")
    session, block = _text(row.get("session_id")), _text(row.get("block_id"))
    found = _block(setup, session, block)
    if found is None or found.get("placement") != "placed":
        raise RuntimeError("Exact target is closed or unbound; no focus attempted")
    if found.get("host_id") != row.get("host_id"):
        raise RuntimeError("Captured block host changed; no focus attempted")
    _call(setup, session, "session.focus_block", {"block_id": block})
    return {"state": "focused", "session_id": session, "block_id": block}


def _hook_lock_budget() -> float:
    remaining, _ = signal.getitimer(signal.ITIMER_REAL)
    return max(0.0, (remaining or _HOOK_SECONDS) - _HOOK_SAVE_MARGIN)


def _reserve_receipt(target: settings.SavedTarget, identity: str) -> int | None:
    """Persist an ordering token before lookup, independent of wall-clock changes."""
    with _store(target, provider_store=True, lock_timeout=_hook_lock_budget()) as rows:
        if identity not in rows and len(rows) >= _MAX_PROVIDER_ROWS:
            return None
        provider_rows = cast(_ProviderRows, rows)
        provider_rows.sequence += 1
        _save(target, rows, provider_store=True)
        return provider_rows.sequence


def _receive_hook(target: settings.SavedTarget, provider: str, payload: object) -> None:
    """Accept only documented metadata; neither content nor transcript paths escape."""
    if provider not in PROVIDERS:
        return
    value = _object(payload)
    event = value.get("hook_event_name")
    if event not in ("SubagentStart", "SubagentStop"):
        return
    parent = _hook_text(value.get("session_id"))
    agent = _hook_text(value.get("agent_id"))
    row: dict[str, object] = {
        "row_type": "provider",
        "provider": provider,
        "event": event,
        "parent_session_id": parent,
        "agent_id": agent,
        "agent_type": _hook_text(value.get("agent_type"), empty=True),
        "cwd": _hook_text(value.get("cwd"), 4096),
        "state": "started" if event == "SubagentStart" else "stopped",
        "outcome": "unknown",
        "observed_at": time.time(),
        "last_message_present": isinstance(value.get("last_assistant_message"), str),
        "transcript_present": isinstance(value.get("transcript_path"), str),
        "agent_transcript_present": isinstance(value.get("agent_transcript_path"), str),
        "endpoint": target.server,
        "rex_server": None,
        "instance_id": None,
        "session_id": None,
        "block_id": None,
        "host_id": None,
        "binding": "unbound",
        "binding_reason": "missing-identifiers",
    }
    for variable, field in (
        ("REX_SERVER", "rex_server"),
        ("REX_SESSION", "session_id"),
        ("REX_BLOCK", "block_id"),
    ):
        if variable in os.environ:
            row[field] = _hook_text(os.environ[variable], 4096, empty=True)
    identity = (
        "hook:"
        + hashlib.sha256(json.dumps([provider, parent, agent]).encode()).hexdigest()
    )
    sequence = _reserve_receipt(target, identity)
    if sequence is None:
        return
    row["receipt_order"] = sequence
    server, session, block = row["rex_server"], row["session_id"], row["block_id"]
    endpoint_matches = server is None
    if isinstance(server, str):
        try:
            endpoint_matches = lifecycle._canonical_server(server) == target.server
        except _HookDeadline:
            raise
        except (OSError, RuntimeError, ValueError):
            endpoint_matches = False
    if not endpoint_matches:
        row["binding_reason"] = "endpoint-mismatch"
    elif (
        isinstance(session, str)
        and session.startswith("session:")
        and isinstance(block, str)
        and block.startswith("block:")
    ):
        try:
            with _binding_budget():
                setup = target.setup()
                instance = _status(setup)
                found = _block(setup, session, block)
                if found is None:
                    row["binding_reason"] = "exact-block-absent"
                elif found.get("placement") != "placed":
                    row["binding_reason"] = "detached"
                else:
                    row.update(
                        instance_id=instance,
                        host_id=_text(found.get("host_id")),
                        binding="bound",
                        binding_reason="verified",
                    )
        except _BindingDeadline:
            row.update(
                binding="unbound",
                binding_reason="verification-timeout",
                instance_id=None,
                host_id=None,
            )
        except _HookDeadline:
            raise
        except (OSError, RuntimeError):
            row.update(
                binding="unbound",
                binding_reason="verification-failed",
                instance_id=None,
                host_id=None,
            )
    with _store(target, provider_store=True, lock_timeout=_hook_lock_budget()) as rows:
        if identity not in rows and len(rows) >= _MAX_PROVIDER_ROWS:
            return
        if sequence > cast(_ProviderRows, rows).sequence:
            return  # The store was reset after this receipt reserved its order.
        previous = rows.get(identity)
        if (
            previous is not None
            and _receipt_number(previous.get("receipt_order", 0)) > sequence
        ):
            return
        rows[identity] = row
        _save(target, rows, provider_store=True)


def hook(provider: str) -> int:
    """Silent fail-open boundary, including deadline restoration and cleanup."""
    try:
        previous = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)

        def expired(signum: int, frame: object) -> None:
            raise _HookDeadline("Provider hook deadline")

        try:
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, _HOOK_SECONDS)
            if provider not in PROVIDERS:
                return 0
            raw = sys.stdin.buffer.read(_MAX_HOOK_BYTES + 1)
            if len(raw) > _MAX_HOOK_BYTES:
                while sys.stdin.buffer.read(64 * 1024):
                    pass
                return 0
            payload: object = json.loads(raw)
            target = settings.load(settings.settings_path())
            if target is not None:
                _receive_hook(target, provider, payload)
        finally:
            # Keep restoration inside the outer fail-open handler: a pending
            # deadline delivered at this boundary must not escape the hook.
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
            finally:
                signal.signal(signal.SIGALRM, previous)
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    except (Exception, KeyboardInterrupt):
        # Provider control semantics must never be affected by telemetry failure.
        pass
    return 0


def clear_provider(target: settings.SavedTarget, provider: str) -> dict[str, object]:
    """Explicit rollback removes only the requested provider's retained rows."""
    if provider not in PROVIDERS:
        raise RuntimeError("Unknown attention provider")
    try:
        with _store(target, provider_store=True) as rows:
            removed = [
                key
                for key, row in rows.items()
                if row.get("row_type") == "provider" and row.get("provider") == provider
            ]
            for key in removed:
                del rows[key]
            _save(target, rows, provider_store=True)
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        return {"provider": provider, **_provider_fault(error)}
    return {
        "state": "cleared",
        "provider": provider,
        "provider_state": "ready",
        "removed": len(removed),
    }

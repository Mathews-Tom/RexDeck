"""On-demand native terminal status, never inferred provider session identity."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from rexdeck import lifecycle, settings

_MAX_BYTES = 2 * 1024 * 1024
_MAX_ROWS = 256
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


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise RuntimeError("Invalid attention object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("Attention requires nonempty string identities")
    return value


def _path(target: settings.SavedTarget) -> Path:
    return target.workspace / "attention" / "attention.json"


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
def _store(target: settings.SavedTarget) -> Iterator[dict[str, dict[str, object]]]:
    """Validate frozen workspace and read only bounded regular non-symlink files."""
    target.setup()
    path = _path(target)
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
        fcntl.flock(handle, fcntl.LOCK_EX)
        rows: dict[str, dict[str, object]] = {}
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
                if info.st_size > _MAX_BYTES:
                    raise RuntimeError("Attention storage exceeds 2 MiB")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    raw = source.read(_MAX_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > _MAX_BYTES:
                raise RuntimeError("Attention storage exceeds 2 MiB")
            document = _object(json.loads(raw))
            if document.get("version") != 1:
                raise RuntimeError("Unsupported attention storage version")
            for key, value in _object(document.get("rows")).items():
                row = _object(value)
                for field in ("endpoint", "instance_id", "session_id", "block_id"):
                    _text(row.get(field))
                _timestamp(row.get("observed_at"))
                if not isinstance(row.get("records", []), list):
                    raise RuntimeError("Attention row records must be a list")
                if len(json.dumps(row, ensure_ascii=True).encode()) > _MAX_ROW_BYTES:
                    raise RuntimeError("Attention row exceeds 16 KiB")
                rows[key] = row
        if len(rows) > _MAX_ROWS:
            raise RuntimeError("Attention storage exceeds 256 observed terminals")
        yield rows


def _bounded_row(row: dict[str, object]) -> dict[str, object]:
    """Bound untrusted terminal reports independently of other terminal rows."""
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


def _save(target: settings.SavedTarget, rows: dict[str, dict[str, object]]) -> None:
    for key, row in rows.items():
        rows[key] = _bounded_row(row)
    data = json.dumps(
        {"version": 1, "rows": rows}, ensure_ascii=True, allow_nan=False
    ).encode()
    if len(data) > _MAX_BYTES or len(rows) > _MAX_ROWS:
        raise RuntimeError("Attention retention limit reached (256 terminals / 2 MiB)")
    path = _path(target)
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


def listing(
    target: settings.SavedTarget,
    project: str | None = None,
    session: str | None = None,
    block: str | None = None,
) -> dict[str, object]:
    """Local retained evidence only. Freshness never means the worker is alive."""
    result: list[dict[str, object]] = []
    with _store(target) as rows:
        for key, row in rows.items():
            if row.get("endpoint") != target.server or any(
                wanted is not None and row.get(field) != wanted
                for field, wanted in (
                    ("project", project),
                    ("session_id", session),
                    ("block_id", block),
                )
            ):
                continue
            age = max(0.0, time.time() - _timestamp(row["observed_at"]))
            result.append(
                {
                    **row,
                    "id": key,
                    "age_seconds": round(age, 1),
                    "freshness": "stale" if age > FRESH_SECONDS else "recent",
                }
            )
    return {"state": "retained", "rows": result}


def jump(target: settings.SavedTarget, identity: str) -> dict[str, object]:
    """Revalidate endpoint incarnation and exact placed block before native focus."""
    setup = target.setup()
    with _store(target) as rows:
        row = rows.get(identity)
        if row is None or row.get("endpoint") != setup.server:
            raise RuntimeError("Unknown attention observation for the saved endpoint")
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

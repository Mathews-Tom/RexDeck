"""Kata Desk's record of the agents it started: exact Rex identities, nothing more.

The store lives in the integration's workspace under `katadesk/state/`. It never
touches setup.json or lifecycle.lock, and `rexdeck disable katadesk` leaves it,
so agents still running are listed again when the desk reopens. Files are 0600,
read only as bounded regular non-symlink files, and replaced atomically.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

MAX_AGENTS = 32
MAX_BYTES = 256 * 1024
MAX_PROMPT_BYTES = 64 * 1024
LOCK_SECONDS = 2.0
PLACEMENTS = ("split", "window", "background")
_FIELDS = (
    "block_id",
    "session_id",
    "host_id",
    "instance_id",
    "endpoint",
    "ref",
    "title",
    "placement",
    "window_id",
    "prompt",
    "started",
)


@dataclass(frozen=True)
class Agent:
    """One started agent, identified only by what Rex reported when it was placed."""

    block_id: str
    session_id: str
    host_id: str
    instance_id: str
    endpoint: str
    ref: str
    title: str
    placement: str
    window_id: str | None
    prompt: str
    started: float


def prompt_text(ref: str, title: str) -> str:
    """The agent's first message: kata's agent loop for exactly this Issue."""
    return f"""You are working on kata Issue {ref}. Its title, as kata reports it:

> {title}

KATA_REF={ref} is set in your environment. Use kata for every Issue change:

1. Claim the Issue before any work: `kata claim {ref} --ttl 2h --agent`. Run the \
same command again to renew the claim while you are still working.
2. Read it with `kata show {ref}`, then do the work in this directory.
3. Comment your findings and how you verified them: \
`kata comment {ref} -m "<findings and verification>" --agent`.
4. Close it only once the work is verified: \
`kata close {ref} --done -m "<what changed and how it was verified>" --agent`.
5. If you cannot finish, do not close it. Run \
`kata meta set {ref} work.attention needs-human --agent` and \
`kata meta set {ref} work.attention_msg "<why a person is needed>" --agent`, \
then stop.

Ask the person in this terminal whenever a decision is needed.
"""


def _text(value: object, field: str, limit: int = 1024) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > limit:
        raise RuntimeError(f"Kata Desk agent store has an invalid {field}")
    return value


def _agent(raw: object) -> Agent:
    if not isinstance(raw, dict) or set(raw) != set(_FIELDS):
        raise RuntimeError("Kata Desk agent store has a malformed agent")
    value = cast(dict[str, object], raw)
    placement = _text(value["placement"], "placement")
    if placement not in PLACEMENTS:
        raise RuntimeError("Kata Desk agent store has an unknown placement")
    window = value["window_id"]
    started = value["started"]
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        raise RuntimeError("Kata Desk agent store has an invalid start time")
    return Agent(
        block_id=_text(value["block_id"], "block_id"),
        session_id=_text(value["session_id"], "session_id"),
        host_id=_text(value["host_id"], "host_id"),
        instance_id=_text(value["instance_id"], "instance_id"),
        endpoint=_text(value["endpoint"], "endpoint", 4096),
        ref=_text(value["ref"], "ref", 128),
        title=_text(value["title"], "title", 1024),
        placement=placement,
        window_id=None if window is None else _text(window, "window_id"),
        prompt=_text(value["prompt"], "prompt", 4096),
        started=float(started),
    )


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "agents.json"
        self.prompts = root / "prompts"

    def _directory(self, path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f"Kata Desk storage must be a real directory: {path}")

    @contextmanager
    def _locked(self) -> Iterator[list[Agent]]:
        self._directory(self.root.parent)
        self._directory(self.root)
        descriptor = os.open(
            self.root / "agents.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        with os.fdopen(descriptor, "r+b", buffering=0) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise RuntimeError("Kata Desk lock must be a regular non-symlink file")
            deadline = time.monotonic() + LOCK_SECONDS
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Kata Desk agent store is busy") from None
                    time.sleep(0.01)
            yield self._read()

    def _read(self) -> list[Agent]:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Kata Desk agent store must be a regular file")
            if info.st_size > MAX_BYTES:
                raise RuntimeError("Kata Desk agent store exceeds 256 KiB")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        try:
            document = json.loads(raw)
        except ValueError as error:
            raise RuntimeError("Kata Desk agent store is not valid JSON") from error
        if not isinstance(document, dict) or document.get("version") != 1:
            raise RuntimeError("Unsupported Kata Desk agent store version")
        rows = document.get("agents")
        if not isinstance(rows, list) or len(rows) > MAX_AGENTS:
            raise RuntimeError("Kata Desk agent store has a malformed agent list")
        return [_agent(row) for row in rows]

    def _write(self, agents: list[Agent]) -> None:
        if len(agents) > MAX_AGENTS:
            raise RuntimeError(
                f"Kata Desk tracks at most {MAX_AGENTS} agents; stop one first"
            )
        data = json.dumps(
            {"version": 1, "agents": [asdict(agent) for agent in agents]},
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
        if len(data) > MAX_BYTES:
            raise RuntimeError("Kata Desk agent store would exceed 256 KiB")
        _replace(self.path, data)

    def load(self) -> list[Agent]:
        with self._locked() as agents:
            return agents

    def update(self, change: Callable[[list[Agent]], list[Agent]]) -> list[Agent]:
        """Apply one change under the lock; the file is replaced only if it changed."""
        with self._locked() as agents:
            changed = change(list(agents))
            if changed != agents:
                self._write(changed)
            return changed

    def prompt(self, ref: str, text: str) -> Path:
        """Write one agent's prompt file and return its absolute path."""
        data = text.encode()
        if len(data) > MAX_PROMPT_BYTES:
            raise RuntimeError("The agent prompt exceeds 64 KiB")
        self._directory(self.root.parent)
        self._directory(self.root)
        self._directory(self.prompts)
        digest = hashlib.sha256(f"{ref}\0{time.time_ns()}".encode()).hexdigest()[:16]
        path = self.prompts / f"{digest}.md"
        _replace(path, data)
        return path

    def forget_prompt(self, agent: Agent) -> None:
        path = Path(agent.prompt)
        if path.parent == self.prompts:
            path.unlink(missing_ok=True)


def _replace(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".katadesk-", dir=path.parent)
    directory: int | None = None
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        os.fsync(directory)
    finally:
        if directory is None:
            os.close(fd)
        else:
            os.close(directory)
        if os.path.exists(name):
            os.unlink(name)

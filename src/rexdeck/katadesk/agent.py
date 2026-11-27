"""Start one omp agent on one kata Issue: exact ref, clean environment, no shell.

Rex terminal blocks take a command and a cwd but no environment, so Kata Desk
starts `python -I -m rexdeck.katadesk.agent <omp> <ref> <prompt>` and this
wrapper replaces itself with omp. Inherited kata identity overrides are dropped
so the agent writes to kata as the owner's own actor, about this Issue only.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}#[A-Za-z0-9]{1,32}")
DROPPED = ("KATA_REF", "KATA_INBOX_USER", "KATA_TEAMMATE")


def environment(ref: str, inherited: dict[str, str]) -> dict[str, str]:
    """The agent's environment: inherited, minus kata overrides, plus KATA_REF."""
    if not REF.fullmatch(ref):
        raise ValueError(f"Not a kata issue reference: {ref!r}")
    kept = {key: value for key, value in inherited.items() if key not in DROPPED}
    kept["KATA_REF"] = ref
    return kept


def argv(omp: str, prompt: str) -> list[str]:
    """omp's argv: the prompt file attached as the first message, nothing else."""
    if not Path(omp).is_absolute() or not os.access(omp, os.X_OK):
        raise ValueError(f"omp must be an absolute executable path: {omp!r}")
    path = Path(prompt)
    if not path.is_absolute():
        raise ValueError(f"The prompt file must be an absolute path: {prompt!r}")
    mode = os.lstat(path).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"The prompt file is not a regular file: {prompt!r}")
    return [omp, f"@{path}"]


def main(arguments: list[str]) -> int:
    if len(arguments) != 3:
        print(
            "usage: python -I -m rexdeck.katadesk.agent OMP REF PROMPT", file=sys.stderr
        )
        return 2
    omp, ref, prompt = arguments
    try:
        command = argv(omp, prompt)
        env = environment(ref, dict(os.environ))
    except (OSError, ValueError) as error:
        print(f"RexDeck Kata Desk agent: {error}", file=sys.stderr)
        return 2
    os.execve(omp, command, env)
    return 0  # pragma: no cover - execve does not return


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Run Kata Desk in the invoking directory with kata and omp put on PATH by enable."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from rexdeck import settings
from rexdeck.katadesk.agents import Store
from rexdeck.katadesk.app import AgentSetup, KataDesk
from rexdeck.katadesk.kata import Kata, KataError, executable
from rexdeck.katadesk.rex import AgentRex


def agents() -> AgentSetup | str:
    """Agent support on RexDeck's saved endpoint, or why the desk runs without it."""
    try:
        target = settings.load(settings.settings_path())
        if target is None:
            return "agents need rexdeck setup"
        setup = target.setup("katadesk")
    except (OSError, RuntimeError, ValueError) as error:
        return f"agents unavailable: {error}"
    omp = shutil.which("omp", path=os.environ.get("PATH", ""))
    if omp is None or not Path(omp).is_absolute():
        return (
            "omp is not on this pane's PATH; install omp, then rexdeck enable katadesk"
        )
    return AgentSetup(
        rex=AgentRex(setup, dict(os.environ)),
        store=Store(target.workspace / "katadesk" / "state"),
        omp=omp,
        endpoint=setup.server,
    )


def main() -> int:
    try:
        path = executable()
    except KataError as error:
        print(f"RexDeck Kata Desk: {error}", file=sys.stderr)
        return 1
    app = KataDesk(Kata(path, Path.cwd()), agents())
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())

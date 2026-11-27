"""Run Kata Desk in the invoking directory with the kata enable put on PATH."""

from __future__ import annotations

import sys
from pathlib import Path

from rexdeck.katadesk.app import KataDesk
from rexdeck.katadesk.kata import Kata, KataError, executable


def main() -> int:
    try:
        path = executable()
    except KataError as error:
        print(f"RexDeck Kata Desk: {error}", file=sys.stderr)
        return 1
    app = KataDesk(Kata(path, Path.cwd()))
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())

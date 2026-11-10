"""Only the verified RexDeck launcher supplies the required GitHub context."""

from __future__ import annotations

import sys

from rexdeck.actions.api import ActionsError, Client, Context, current_filter
from rexdeck.actions.app import ActionsApp


def main() -> int:
    try:
        context = Context.from_env()
        selected = current_filter()
    except ActionsError as error:
        print(str(error), file=sys.stderr)
        return 1
    ActionsApp(Client(context), selected).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

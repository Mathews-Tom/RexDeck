"""The verified account launcher is the only source of account selection."""

from __future__ import annotations

import sys

from rexdeck.inbox.api import Client, Context, InboxError
from rexdeck.inbox.app import InboxApp


def main() -> int:
    try:
        context = Context.from_env()
    except InboxError as error:
        print(str(error), file=sys.stderr)
        return 1
    app = InboxApp(Client(context))
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())

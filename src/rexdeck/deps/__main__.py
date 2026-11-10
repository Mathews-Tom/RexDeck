"""Launch the packaged Dependencies view without inspecting the working directory."""

from __future__ import annotations

from rexdeck.deps.app import DepsApp


def main() -> int:
    DepsApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

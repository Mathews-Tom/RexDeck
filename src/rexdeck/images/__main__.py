"""Launch the packaged Images view without inspecting the working directory."""

from __future__ import annotations

from rexdeck.images.app import ImagesApp


def main() -> int:
    ImagesApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

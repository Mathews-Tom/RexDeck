"""Render the RexDeck launch banner, video and GIF from the HTML sources.

Install Chromium and run from the repository root:

    uv run --no-project --with playwright playwright install chromium

    uv run --no-project --with playwright python assets/source/render.py

Requires ffmpeg on PATH.
Frames are captured deterministically by seeking `window.setTime(t)`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

SOURCE = Path(__file__).resolve().parent
ASSETS = SOURCE.parent
FPS = 30


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> int:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required on PATH")
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1280, "height": 640}, device_scale_factor=2
        )
        page.goto((SOURCE / "banner.html").as_uri())
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(ASSETS / "rexdeck-banner.png"))
        page.close()

        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((SOURCE / "launch.html").as_uri())
        page.evaluate("document.fonts.ready")
        duration = float(page.evaluate("window.DURATION"))
        frames = Path(tmp)
        for i in range(round(duration * FPS)):
            page.evaluate("t => window.setTime(t)", i / FPS)
            page.screenshot(path=str(frames / f"{i:05d}.png"))
        browser.close()

        pattern = str(frames / "%05d.png")
        run(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(FPS),
            "-i",
            pattern,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(ASSETS / "rexdeck-launch.mp4"),
        )
        gif_filter = (
            "fps=15,scale=960:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
        )
        run(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(FPS),
            "-i",
            pattern,
            "-filter_complex",
            gif_filter,
            "-loop",
            "0",
            str(ASSETS / "rexdeck-launch.gif"),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

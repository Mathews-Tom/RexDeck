from __future__ import annotations

import subprocess
from pathlib import Path


def test_uncertain_replaced_and_other_origin_previews_are_preserved() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "luajit",
            str(root / "tests/lua/preview_ownership.lua"),
            str(root / "plugins/files"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rexdeck import integrations

ORIGINAL = ("files", "git", "markdown", "usage", "github", "viewer", "clickup")
SOURCE = Path(integrations.__file__).parent


def test_original_plugins_keep_published_order_before_sorted_additions() -> None:
    assert integrations.PLUGINS[: len(ORIGINAL)] == ORIGINAL
    added = integrations.PLUGINS[len(ORIGINAL) :]
    assert added == tuple(sorted(added))


def _catalogue(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    shutil.copytree(
        SOURCE, root / "rexdeck", ignore=shutil.ignore_patterns("__pycache__")
    )
    return root / "rexdeck" / "catalogue"


def _plugins(catalogue: Path) -> subprocess.CompletedProcess[str]:
    site = catalogue.parent.parent
    code = (
        f"import sys; sys.path.insert(0, {str(site)!r}); "
        "import rexdeck.integrations as i; print(','.join(i.PLUGINS))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def _derived(catalogue: Path, name: str, source: str = "files") -> None:
    text = (catalogue / f"{source}.py").read_text(encoding="utf-8")
    text = text.replace('id="files"', f'id="{name}"').replace(
        'title="Files"', f'title="{name.capitalize()}"'
    )
    (catalogue / f"{name}.py").write_text(text, encoding="utf-8")


def test_new_modules_follow_the_original_seven_sorted_by_id(tmp_path: Path) -> None:
    catalogue = _catalogue(tmp_path)
    for name in ("zeta", "alpha"):
        _derived(catalogue, name)
    result = _plugins(catalogue)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(",") == [*ORIGINAL, "alpha", "zeta"]


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("missing", "Missing catalogue modules: git"),
        ("mismatch", "Catalogue module extra must describe integration id 'extra'"),
        ("unexported", "Catalogue module extra must define INTEGRATION"),
    ],
)
def test_discovery_fails_loudly_on_a_broken_catalogue(
    tmp_path: Path, damage: str, message: str
) -> None:
    catalogue = _catalogue(tmp_path)
    if damage == "missing":
        (catalogue / "git.py").unlink()
    elif damage == "mismatch":
        _derived(catalogue, "other")
        (catalogue / "other.py").rename(catalogue / "extra.py")
    else:
        (catalogue / "extra.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = _plugins(catalogue)
    assert result.returncode != 0
    assert message in result.stderr

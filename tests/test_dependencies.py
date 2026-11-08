"""Preinstalled-tool qualification and manual recovery without package ownership."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from rexdeck import dependencies, lifecycle


class _UnusedInput:
    """An interactive input that dependency checks must never consume."""

    def isatty(self) -> bool:
        """Expose the interactive path without authorizing an installation prompt."""
        return True

    def readline(self, size: int | None = -1, /) -> str:
        """Fail if tool validation attempts to ask for installation consent."""
        raise AssertionError("Dependency validation must not read installation consent")


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        """Isolate tool discovery and observable package-manager side effects."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.manager_marker = self.root / "package-manager-executed"
        boundary = patch.dict(
            os.environ, {"HOME": str(self.root), "PATH": str(self.bin)}, clear=True
        )
        boundary.start()
        self.addCleanup(boundary.stop)
        for manager in ("brew", "uv", "pip"):
            self.script(
                manager,
                "from pathlib import Path\n"
                f"Path({str(self.manager_marker)!r}).write_text('executed')\n"
                "raise SystemExit(99)\n",
            )
        self.tool("git", "git version 2.50.0")

    def script(self, name: str, body: str) -> Path:
        """Write executable fixtures only under the disposable tool directory."""
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return path

    def tool(self, name: str, output: str, code: int = 0) -> Path:
        """Provide a real version subprocess with controlled output and exit status."""
        return self.script(
            name,
            "import sys\n"
            "if sys.argv[1:] != ['--version']: raise SystemExit(97)\n"
            f"print({output!r})\n"
            f"raise SystemExit({code})\n",
        )

    def test_reuses_only_qualified_selected_tool(self) -> None:
        """Reuse neither requires the sibling tool nor executes a package manager."""
        for plugin, name, output in (
            ("files", "yazi", "Yazi 26.9.1 (c2c32ac 2026-09-13)"),
            (
                "files",
                "yazi",
                "Yazi\n    Version: 26.9.1 (Homebrew 2026-09-01)\n    Debug  : false",
            ),
            ("git", "lazygit", "commit=abc, version=0.66.2, os=darwin, arch=arm64"),
            ("git", "lazygit", "commit=abc, version=0.66.3, os=darwin, arch=arm64"),
        ):
            with self.subTest(plugin=plugin, output=output):
                executable = self.tool(name, output)
                before = executable.read_bytes()
                qualified = dependencies.ensure(plugin)
                self.assertEqual(qualified.path, str(executable))
                qualified.require_current()
                self.assertEqual(executable.read_bytes(), before)
                self.assertFalse(self.manager_marker.exists())
                executable.unlink()

    def test_missing_tools_never_read_consent_or_execute_available_managers(
        self,
    ) -> None:
        """Missing tools always take the manual route, even with interactive yes input."""
        for plugin, tool in (("files", "yazi"), ("git", "lazygit")):
            for stdin in (io.StringIO("yes\n"), _UnusedInput()):
                with self.subTest(plugin=plugin, interactive=stdin.isatty()):
                    with (
                        patch.object(sys, "stdin", stdin),
                        self.assertRaises(RuntimeError) as failure,
                    ):
                        dependencies.ensure(plugin)
                    message = str(failure.exception)
                    self.assertIn(dependencies._RELEASES[tool], message)
                    self.assertIn(f"{tool} --version", message)
                    self.assertIn(f"rexdeck enable {plugin}", message)
                    self.assertFalse(self.manager_marker.exists())
                    self.assertFalse((self.bin / tool).exists())

    def test_manual_install_then_rerun_qualifies_the_same_selected_tool(self) -> None:
        """An owner-installed executable is usable without remembering failed attempts."""
        for plugin, name, output in (
            ("files", "yazi", "Yazi 26.9.1"),
            ("git", "lazygit", "version=0.66.3, os=darwin, arch=arm64"),
        ):
            with self.subTest(plugin=plugin):
                with self.assertRaises(RuntimeError):
                    dependencies.ensure(plugin)
                executable = self.tool(name, output)
                qualified = dependencies.ensure(plugin)
                self.assertEqual(qualified.path, str(executable))
                qualified.require_current()
                self.assertFalse(self.manager_marker.exists())
                executable.unlink()

    def test_unqualified_malformed_and_failed_versions_are_not_replaced(self) -> None:
        """Invalid installed tools remain owner-managed and cannot trigger upgrades."""
        for output, code in (
            ("Yazi 26.9.2", 0),
            ("Yazi 26.9.1-beta", 0),
            ("not a version", 0),
            ("Yazi 26.9.1", 7),
        ):
            with self.subTest(output=output, code=code):
                executable = self.tool("yazi", output, code)
                before = executable.read_bytes()
                with (
                    patch.object(sys, "stdin", _UnusedInput()),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertEqual(executable.read_bytes(), before)
                self.assertFalse(self.manager_marker.exists())

    def test_executable_changed_during_its_version_probe_is_rejected(self) -> None:
        """A valid version report cannot qualify a replacement executable."""
        replacement = self.tool("replacement", "Yazi 99.0.0")
        replacement_bytes = replacement.read_bytes()
        executable = self.script(
            "yazi",
            "import os\n"
            "print('Yazi 26.9.1')\n"
            f"os.replace({str(replacement)!r}, {str(self.bin / 'yazi')!r})\n",
        )
        with self.assertRaisesRegex(RuntimeError, "changed after qualification"):
            dependencies.ensure("files")
        self.assertEqual(executable.read_bytes(), replacement_bytes)
        self.assertFalse(self.manager_marker.exists())

    def test_missing_git_blocks_only_the_git_integration(self) -> None:
        """Git is a prerequisite, while independently installed Files stays usable."""
        (self.bin / "git").unlink()
        with self.assertRaisesRegex(RuntimeError, "Git.*PATH.*rexdeck enable git"):
            dependencies.ensure("git")
        executable = self.tool("yazi", "Yazi 26.9.1")
        self.assertEqual(dependencies.ensure("files").path, str(executable))
        self.assertFalse(self.manager_marker.exists())

    def test_relative_path_cannot_execute_checkout_tools_or_bind_lifecycle(
        self,
    ) -> None:
        """Relative PATH entries cannot execute checkout-local prerequisite impostors."""
        repository = self.root / "untrusted"
        relative = repository / "tools"
        relative.mkdir(parents=True)
        marker = self.root / "checkout-executed"
        for folder in (repository, relative):
            for name in ("yazi", "lazygit", "git", "rex"):
                executable = folder / name
                executable.write_text(
                    f"#!{sys.executable}\n"
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).touch()\n"
                )
                executable.chmod(0o755)
        with (
            chdir(repository),
            patch.dict(os.environ, {"PATH": f":tools:{self.bin}"}),
        ):
            for plugin in ("files", "git"):
                with self.subTest(plugin=plugin), self.assertRaises(RuntimeError):
                    dependencies.ensure(plugin)
                self.assertFalse(marker.exists())
            for name in ("rex", "yazi", "lazygit"):
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    lifecycle._executable(name)
            self.assertEqual(lifecycle._executable("git"), str(self.bin / "git"))
        self.assertFalse(marker.exists())
        self.assertFalse(self.manager_marker.exists())

    def test_missing_rex_has_external_prerequisite_guidance(self) -> None:
        """Rex remains externally installed and cannot trigger a bootstrap command."""
        with self.assertRaisesRegex(
            RuntimeError, "externally installed prerequisite.*rexdeck setup"
        ):
            lifecycle._executable("rex")
        self.assertFalse(self.manager_marker.exists())


if __name__ == "__main__":
    unittest.main()

"""Dependency consent and real subprocess boundaries, not package-install proof."""

from __future__ import annotations

import io
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rexdeck import dependencies, lifecycle


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # An eight-character directory below /tmp fits Homebrew's short-prefix rule.
        temporary = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        self.bin = self.prefix / "bin"
        self.bin.mkdir()
        self.record = self.prefix / "installer.json"
        environment = {"HOME": str(self.prefix), "PATH": str(self.bin)}
        for boundary in (
            patch.dict(os.environ, environment, clear=True),
            patch.object(platform, "system", return_value="Darwin"),
            patch.object(platform, "machine", return_value="arm64"),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)
        self.tool("git", "git version 2.50.0")

    def script(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return path

    def tool_source(self, name: str, output: str, code: int = 0) -> str:
        return (
            "import pathlib, sys\n"
            "if sys.argv[1:] != ['--version']: sys.exit(97)\n"
            f"with pathlib.Path({str(self.prefix / (name + '.probes'))!r}).open('a') as stream:\n"
            "    stream.write('version\\n')\n"
            f"print({output!r})\n"
            f"sys.exit({code})\n"
        )

    def tool(self, name: str, output: str, code: int = 0) -> Path:
        return self.script(name, self.tool_source(name, output, code))

    def brew(
        self,
        *,
        version: str = "7.0.12",
        prefix: str | None = None,
        cellar: str | None = None,
        outcome: str = "success",
        yazi_version: str = "26.9.1",
    ) -> Path:
        prefix = str(self.prefix) if prefix is None else prefix
        cellar = str(Path(prefix) / "Cellar") if cellar is None else cellar
        sources = {
            "yazi": self.tool_source("yazi", f"Yazi {yazi_version} (qualified build)"),
            "lazygit": self.tool_source(
                "lazygit",
                "commit=abc, build source=test, version=0.66.3, os=darwin, arch=arm64",
            ),
        }
        return self.script(
            "brew",
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"if args == ['--version']: print('Homebrew {version}')\n"
            f"elif args == ['--prefix']: print({prefix!r})\n"
            f"elif args == ['--cellar']: print({cellar!r})\n"
            "elif len(args) == 2 and args[0] == 'install' and args[1] in ('yazi', 'lazygit'):\n"
            f"    record = pathlib.Path({str(self.record)!r})\n"
            "    keys = ['HOMEBREW_NO_AUTO_UPDATE', 'HOMEBREW_NO_INSTALL_UPGRADE', "
            "'HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK', 'HOMEBREW_NO_INSTALL_CLEANUP']\n"
            "    record.write_text(json.dumps({'argv': args, 'env': {key: os.environ.get(key) for key in keys}}))\n"
            "    print('download progress', flush=True)\n"
            "    print('installer diagnostic', file=sys.stderr, flush=True)\n"
            f"    if {outcome!r} == 'nonzero': sys.exit(42)\n"
            f"    if {outcome!r} == 'missing': sys.exit(0)\n"
            f"    sources = {sources!r}\n"
            f"    target = pathlib.Path({str(self.bin)!r}) / args[1]\n"
            f"    target.write_text({('#!' + sys.executable + chr(10))!r} + sources[args[1]])\n"
            "    target.chmod(0o755)\n"
            "else: sys.exit(98)\n",
        )

    def test_reuses_only_qualified_selected_tool_without_a_manager(self) -> None:
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
                with patch.object(sys, "stdin", io.StringIO("yes\n")):
                    self.assertEqual(dependencies.ensure(plugin), str(executable))
                self.assertFalse(self.record.exists())
                executable.unlink()

    def test_unqualified_malformed_and_failed_versions_never_offer_install(
        self,
    ) -> None:
        self.brew()
        for output, code in (
            ("Yazi 26.9.2", 0),
            ("Yazi 26.9.1-beta", 0),
            ("not a version", 0),
            ("Yazi 26.9.1", 7),
        ):
            with self.subTest(output=output, code=code):
                executable = self.tool("yazi", output, code)
                before = executable.read_bytes()
                stderr = io.StringIO()
                with (
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    redirect_stderr(stderr),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertEqual(executable.read_bytes(), before)
                self.assertFalse(self.record.exists())
                self.assertNotIn("[y/N]", stderr.getvalue())

    def test_noninteractive_input_cannot_consent_even_when_it_contains_yes(
        self,
    ) -> None:
        self.brew()
        with (
            patch.object(sys, "stdin", io.StringIO("yes\n")),
            self.assertRaisesRegex(RuntimeError, "noninteractive.*brew install yazi"),
        ):
            dependencies.ensure("files")
        self.assertFalse(self.record.exists())
        self.assertFalse((self.bin / "yazi").exists())

    def test_default_no_and_decline_show_exact_command_effects_and_manual_rerun(
        self,
    ) -> None:
        brew = self.brew()
        for answer in ("", "\n", "no\n", "sure\n"):
            with self.subTest(answer=answer):
                stderr = io.StringIO()
                with (
                    patch.object(sys, "stdin", _Terminal(answer)),
                    redirect_stderr(stderr),
                    self.assertRaisesRegex(
                        RuntimeError, "brew install yazi.*rexdeck enable files"
                    ),
                ):
                    dependencies.ensure("files")
                prompt = stderr.getvalue()
                self.assertIn(f"{brew} install yazi", prompt)
                self.assertIn("[y/N]", prompt)
                self.assertIn("package registries", prompt)
                self.assertIn("transitive", prompt)
                self.assertFalse(self.record.exists())
                self.assertFalse((self.bin / "yazi").exists())

    def test_ctrl_c_at_consent_returns_manual_guidance_without_starting_installer(
        self,
    ) -> None:
        self.brew()
        terminal = _Terminal()
        with (
            patch.object(terminal, "readline", side_effect=KeyboardInterrupt),
            patch.object(sys, "stdin", terminal),
            redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(
                RuntimeError, "consent cancelled.*rexdeck enable files"
            ),
        ):
            dependencies.ensure("files")
        self.assertFalse(self.record.exists())

    def test_missing_git_is_a_prerequisite_not_an_install_offer(self) -> None:
        (self.bin / "git").unlink()
        self.brew()
        stderr = io.StringIO()
        with (
            patch.object(sys, "stdin", _Terminal("yes\n")),
            redirect_stderr(stderr),
            self.assertRaisesRegex(RuntimeError, "Git.*PATH.*rexdeck enable git"),
        ):
            dependencies.ensure("git")
        self.assertEqual(stderr.getvalue(), "")
        self.assertFalse(self.record.exists())
        # The missing Git prerequisite has no effect on the independent Files tool.
        self.tool("yazi", "Yazi 26.9.1")
        self.assertEqual(dependencies.ensure("files"), str(self.bin / "yazi"))

    def test_unsupported_platform_and_missing_manager_have_manual_instructions(
        self,
    ) -> None:
        for system, machine in (
            ("Linux", "aarch64"),
            ("Darwin", "x86_64"),
            ("Darwin", "arm64"),
        ):
            with self.subTest(system=system, machine=machine):
                with (
                    patch.object(platform, "system", return_value=system),
                    patch.object(platform, "machine", return_value=machine),
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    self.assertRaisesRegex(
                        RuntimeError, "brew install lazygit.*rexdeck enable git"
                    ),
                ):
                    dependencies.ensure("git")
                self.assertFalse(self.record.exists())

    def test_unqualified_manager_layout_version_and_relocation_never_install(
        self,
    ) -> None:
        for options in (
            {"version": "7.0.13"},
            {"prefix": "relative"},
            {"prefix": str(self.prefix / "missing")},
            {"cellar": str(self.prefix / "not-standard")},
        ):
            with self.subTest(options=options):
                self.brew(**options)
                with (
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertFalse(self.record.exists())
        self.brew()
        for overrides in (
            {"HOMEBREW_NO_RELOCATE_BUILD_PREFIX": "1"},
            {"HOMEBREW_PREFIX": "/other-prefix"},
            {"HOMEBREW_CELLAR": "/other-cellar"},
            {"HOMEBREW_BREW_FILE": "/other/bin/brew"},
        ):
            with self.subTest(overrides=overrides):
                with (
                    patch.dict(os.environ, overrides),
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertFalse(self.record.exists())

    def test_consented_simulated_installer_rechecks_actual_new_executable(self) -> None:
        self.brew()
        for plugin, name in (("files", "yazi"), ("git", "lazygit")):
            with self.subTest(plugin=plugin):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    executable = dependencies.ensure(plugin)
                self.assertEqual(executable, str(self.bin / name))
                self.assertEqual(
                    (self.prefix / (name + ".probes")).read_text(), "version\n"
                )
                record = json.loads(self.record.read_text())
                self.assertEqual(record["argv"], ["install", name])
                self.assertEqual(
                    record["env"],
                    {
                        "HOMEBREW_NO_AUTO_UPDATE": "1",
                        "HOMEBREW_NO_INSTALL_UPGRADE": "1",
                        "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
                        "HOMEBREW_NO_INSTALL_CLEANUP": "1",
                    },
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("download progress", stderr.getvalue())
                self.assertIn("installer diagnostic", stderr.getvalue())
                for key in (
                    "HOMEBREW_NO_AUTO_UPDATE",
                    "HOMEBREW_NO_INSTALL_UPGRADE",
                    "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK",
                    "HOMEBREW_NO_INSTALL_CLEANUP",
                ):
                    self.assertNotIn(key, os.environ)
                Path(executable).unlink()

    def test_installer_nonzero_missing_and_bad_postinstall_versions_are_failures(
        self,
    ) -> None:
        for options, error in (
            ({"outcome": "nonzero"}, "status 42"),
            ({"outcome": "missing"}, "still missing"),
            ({"yazi_version": "99.0.0"}, "Unqualified yazi"),
        ):
            with self.subTest(options=options):
                self.brew(**options)
                with (
                    patch.object(sys, "stdin", _Terminal("y\n")),
                    redirect_stderr(io.StringIO()),
                    self.assertRaisesRegex(
                        RuntimeError, error + ".*rexdeck enable files"
                    ),
                ):
                    dependencies.ensure("files")
                self.assertTrue(self.record.exists())
                (self.bin / "yazi").unlink(missing_ok=True)

    def test_relative_path_cannot_execute_checkout_tools_or_bind_lifecycle(
        self,
    ) -> None:
        repository = self.prefix / "untrusted"
        relative = repository / "tools"
        relative.mkdir(parents=True)
        marker = self.prefix / "checkout-executed"
        for folder in (repository, relative):
            for name in ("brew", "yazi", "lazygit", "git", "rex"):
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
            patch.object(sys, "stdin", _Terminal("yes\n")),
            redirect_stderr(io.StringIO()),
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

    def test_missing_rex_has_explicit_external_prerequisite_guidance(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "externally installed prerequisite.*rexdeck setup"
        ):
            lifecycle._executable("rex")
        self.assertFalse(self.record.exists())

    def test_signal_cancels_only_owned_installer_group_and_reaps_its_leader(
        self,
    ) -> None:
        ready = self.prefix / "ready.json"
        installer = self.script(
            "long-installer",
            "import json, os, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
            f"pathlib.Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "print('installer active', flush=True)\n"
            "time.sleep(300)\n",
        )
        driver_source = (
            "import json, os, pathlib, sys\n"
            "from rexdeck.dependencies import _install\n"
            "try:\n"
            f"    _install([{str(installer)!r}])\n"
            "except RuntimeError as error:\n"
            f"    pid = json.loads(pathlib.Path({str(ready)!r}).read_text())[0]\n"
            "    reaped = False\n"
            "    try: os.waitpid(pid, os.WNOHANG)\n"
            "    except ChildProcessError: reaped = True\n"
            "    print(json.dumps({'error': str(error), 'reaped': reaped}))\n"
            "    sys.exit(23)\n"
        )
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"]
        )
        driver = subprocess.Popen(
            [sys.executable, "-c", driver_source],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(dependencies.__file__).parents[1]),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 8
            identities: list[int] = []
            while time.monotonic() < deadline:
                try:
                    identities = json.loads(ready.read_text())
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    if driver.poll() is not None:
                        break
                    time.sleep(0.01)
            self.assertEqual(
                len(identities), 2, "installer did not reach its child boundary"
            )
            leader, descendant = identities
            self.assertEqual(os.getpgid(leader), leader)
            self.assertEqual(os.getpgid(descendant), leader)
            driver.send_signal(signal.SIGTERM)
            stdout, stderr = driver.communicate(timeout=8)
            self.assertEqual(driver.returncode, 23, stderr)
            result = json.loads(stdout)
            self.assertIn("cancelled", result["error"])
            self.assertIs(result["reaped"], True)
            self.assertIsNone(unrelated.poll())
            # Orphan zombies may briefly await init; neither process may still execute.
            for pid in (leader, descendant):
                deadline = time.monotonic() + 3
                while True:
                    status = subprocess.run(
                        ["/bin/ps", "-p", str(pid), "-o", "stat="],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if status.returncode != 0 or status.stdout.strip().startswith("Z"):
                        break
                    self.assertLess(
                        time.monotonic(), deadline, f"installer process {pid} survived"
                    )
                    time.sleep(0.01)
        finally:
            cleanup_leader: int | None
            try:
                cleanup_leader = json.loads(ready.read_text())[0]
            except (FileNotFoundError, json.JSONDecodeError):
                cleanup_leader = None
            if cleanup_leader is not None:
                try:
                    os.killpg(cleanup_leader, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if driver.poll() is None:
                driver.kill()
            driver.communicate(timeout=8)
            unrelated.kill()
            unrelated.wait(timeout=8)


if __name__ == "__main__":
    unittest.main()

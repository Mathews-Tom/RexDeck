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
from typing import TypedDict
from unittest.mock import patch

from rexdeck import dependencies, lifecycle


class _BrewOptions(TypedDict, total=False):
    version: str
    prefix: str
    cellar: str
    outcome: str
    yazi_version: str
    metadata_output: str
    metadata_code: int


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        """Permit explicit consent while ordinary StringIO remains noninteractive."""
        return True


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # An eight-character directory below /tmp fits Homebrew's short-prefix rule.
        """Isolate executable discovery, prefix effects and user configuration."""
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
        """Create an executable fixture solely inside the disposable prefix."""
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return path

    def tool_source(self, name: str, output: str, code: int = 0) -> str:
        """Record genuine version subprocesses and control their exit behavior."""
        return (
            "import pathlib, sys\n"
            "if sys.argv[1:] != ['--version']: sys.exit(97)\n"
            f"with pathlib.Path({str(self.prefix / (name + '.probes'))!r}).open('a') as stream:\n"
            "    stream.write('version\\n')\n"
            f"print({output!r})\n"
            f"sys.exit({code})\n"
        )

    def tool(self, name: str, output: str, code: int = 0) -> Path:
        """Expose a versioned selected tool without requiring any package manager."""
        return self.script(name, self.tool_source(name, output, code))

    def brew(
        self,
        *,
        version: str = "7.0.12",
        prefix: str | None = None,
        cellar: str | None = None,
        outcome: str = "success",
        yazi_version: str = "26.9.1",
        dependencies_: tuple[str, ...] = (),
        metadata_fields: dict[str, object] | None = None,
        metadata_output: str | None = None,
        metadata_code: int = 0,
        metadata_warning: bool = False,
        concurrent_go: bool = False,
    ) -> Path:
        """Model metadata refusal and bottle/source effects, never real installation."""
        prefix = str(self.prefix) if prefix is None else prefix
        cellar = str(Path(prefix) / "Cellar") if cellar is None else cellar
        sources = {
            "yazi": self.tool_source("yazi", f"Yazi {yazi_version} (qualified build)"),
            "lazygit": self.tool_source(
                "lazygit",
                "commit=abc, build source=test, version=0.66.3, os=darwin, arch=arm64",
            ),
        }
        metadata = {}
        for name in ("yazi", "lazygit"):
            formula: dict[str, object] = {
                "name": name,
                "tap": "homebrew/core",
                "versions": {
                    "stable": dependencies._VERSIONS[name][-1],
                    "bottle": True,
                },
                "bottle": {"stable": {"files": {"arm64_tahoe": {"sha256": "fixture"}}}},
                "post_install_defined": False,
                "disabled": False,
                "dependencies": list(dependencies_),
                "recommended_dependencies": [],
                "optional_dependencies": [],
                "uses_from_macos": [],
                "requirements": [],
                "options": [],
                "build_dependencies": ["go" if name == "lazygit" else "rust"],
            }
            formula.update(metadata_fields or {})
            metadata[name] = metadata_output or json.dumps({"formulae": [formula]})
        return self.script(
            "brew",
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"if args == ['--version']: print('Homebrew {version}')\n"
            f"elif args == ['--prefix']: print({prefix!r})\n"
            f"elif args == ['--cellar']: print({cellar!r})\n"
            "elif len(args) == 4 and args[:3] == ['info', '--json=v2', '--formula']:\n"
            "    name = args[-1].rsplit('/', 1)[-1]\n"
            f"    if {metadata_warning!r}: print('Warning: owned fixture diagnostic', file=sys.stderr, flush=True)\n"
            f"    if {outcome!r} == 'oversized-metadata': print(' ' * {lifecycle._MAX_READ + 1}); sys.exit(0)\n"
            f"    if {concurrent_go!r}: pathlib.Path({str(self.prefix / 'dependency-go')!r}).write_text('concurrent old Go')\n"
            f"    print({metadata!r}[name])\n"
            f"    sys.exit({metadata_code})\n"
            "elif args[0] == 'install':\n"
            "    name = args[-1].rsplit('/', 1)[-1]\n"
            f"    record = pathlib.Path({str(self.record)!r})\n"
            "    record.write_text('install attempted')\n"
            "    forbidden = set(os.environ.get('HOMEBREW_FORBIDDEN_FORMULAE', '').split())\n"
            "    if name in forbidden or args[-1] in forbidden:\n"
            "        print('package installation forbidden', flush=True); sys.exit(43)\n"
            f"    go = pathlib.Path({str(self.prefix / 'dependency-go')!r})\n"
            "    if '--force-bottle' not in args and go.exists(): go.write_text('upgraded Go')\n"
            "    print('download progress', flush=True)\n"
            "    print('installer diagnostic', file=sys.stderr, flush=True)\n"
            f"    if {outcome!r} == 'nonzero': sys.exit(42)\n"
            f"    if {outcome!r} == 'missing': sys.exit(0)\n"
            f"    sources = {sources!r}\n"
            f"    target = pathlib.Path({str(self.bin)!r}) / name\n"
            f"    target.write_text({('#!' + sys.executable + chr(10))!r} + sources[name])\n"
            "    target.chmod(0o755)\n"
            "else: sys.exit(98)\n",
        )

    def test_reuses_only_qualified_selected_tool_without_a_manager(self) -> None:
        """Reusing one tool must neither validate its sibling nor invoke Homebrew."""
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
                    self.assertEqual(dependencies.ensure(plugin).path, str(executable))
                self.assertFalse(self.record.exists())
                executable.unlink()

    def test_unqualified_malformed_and_failed_versions_never_offer_install(
        self,
    ) -> None:
        """An installed but unqualified tool requires manual resolution, not upgrades."""
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
        """Piped affirmative text is not interactive installation consent."""
        self.brew()
        with (
            patch.object(sys, "stdin", io.StringIO("yes\n")),
            self.assertRaisesRegex(RuntimeError, "noninteractive.*brew install yazi"),
        ):
            dependencies.ensure("files")
        self.assertFalse(self.record.exists())
        self.assertFalse((self.bin / "yazi").exists())

    def test_default_no_and_decline_never_install(self) -> None:
        """Empty, negative and unrecognized input never authorize package mutation."""
        self.brew()
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
                self.assertFalse(self.record.exists())
                self.assertFalse((self.bin / "yazi").exists())

    def test_ctrl_c_at_consent_returns_manual_guidance_without_starting_installer(
        self,
    ) -> None:
        """Interrupting consent leaves the package prefix untouched."""
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
        """Missing Git prevents only Git enable and cannot trigger Git installation."""
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
        self.assertEqual(dependencies.ensure("files").path, str(self.bin / "yazi"))

    def test_unsupported_platform_and_missing_manager_have_manual_instructions(
        self,
    ) -> None:
        """Unsupported hosts and missing managers never start a package transaction."""
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
        """Reject incompatible managers and redirects before offering installation."""
        cases: tuple[_BrewOptions, ...] = (
            {"version": "7.0.13"},
            {"prefix": "relative"},
            {"prefix": str(self.prefix / "missing")},
            {"cellar": str(self.prefix / "not-standard")},
        )
        for options in cases:
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
            {"HOMEBREW_NO_INSTALL_FROM_API": "1"},
            {"HOMEBREW_FORCE_API_AUTO_UPDATE": "1"},
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
        """Accepted installation must re-resolve and probe the resulting executable."""
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
                self.assertEqual(executable.path, str(self.bin / name))
                self.assertEqual(
                    (self.prefix / (name + ".probes")).read_text(), "version\n"
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("download progress", stderr.getvalue())
                self.assertIn("installer diagnostic", stderr.getvalue())
                Path(executable.path).unlink()

    def test_concurrent_build_dependency_is_not_upgraded_by_bottle_install(
        self,
    ) -> None:
        """A package appearing after preflight must not become a source dependency."""
        self.brew(concurrent_go=True)
        with (
            patch.object(sys, "stdin", _Terminal("yes\n")),
            redirect_stderr(io.StringIO()),
        ):
            qualified = dependencies.ensure("git")
        qualified.require_current()
        self.assertEqual(qualified.path, str(self.bin / "lazygit"))
        self.assertEqual(
            (self.prefix / "dependency-go").read_text(), "concurrent old Go"
        )

    def test_runtime_dependencies_or_hooks_refuse_install_without_package_mutation(
        self,
    ) -> None:
        """Bottles requiring broader package effects take the explicit manual route."""
        cases: tuple[dict[str, object], ...] = (
            {"dependencies": ["go"]},
            {"recommended_dependencies": ["extra"]},
            {"optional_dependencies": ["extra"]},
            {"uses_from_macos": ["libiconv"]},
            {"requirements": [{"name": "java"}]},
            {"options": [{"option": "with-extra"}]},
            {"post_install_defined": True},
            {"versions": {"stable": "26.9.1", "bottle": False}},
            {"versions": {"stable": "99.0.0", "bottle": True}},
            {"bottle": {"stable": {"files": {}}}},
            {"tap": "owner/third-party"},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                self.brew(metadata_fields=fields)
                with (
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    redirect_stderr(io.StringIO()),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertFalse(self.record.exists())
                self.assertFalse((self.bin / "yazi").exists())

    def test_owner_forbidden_selected_formula_still_cannot_be_installed(self) -> None:
        """The private-cache environment must preserve the owner's package bans."""
        self.brew()
        with (
            patch.dict(os.environ, {"HOMEBREW_FORBIDDEN_FORMULAE": "yazi"}),
            patch.object(sys, "stdin", _Terminal("yes\n")),
            redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "status 43"),
        ):
            dependencies.ensure("files")
        self.assertFalse((self.bin / "yazi").exists())

    def test_valid_metadata_with_stderr_warning_still_qualifies_bottle(self) -> None:
        """Visible manager diagnostics must not corrupt the JSON qualification."""
        self.brew(metadata_warning=True)
        with (
            patch.object(sys, "stdin", _Terminal("yes\n")),
            redirect_stderr(io.StringIO()),
        ):
            qualified = dependencies.ensure("files")
        self.assertEqual(qualified.path, str(self.bin / "yazi"))
        qualified.require_current()

    def test_invalid_failed_and_oversized_metadata_never_start_installer(self) -> None:
        """Malformed or unsuccessful qualification must precede all package mutation."""
        cases: tuple[_BrewOptions, ...] = (
            {"metadata_output": "not JSON"},
            {"metadata_output": "[]"},
            {"metadata_output": "{}"},
            {"metadata_output": '{"formulae": null}'},
            {"metadata_output": '{"formulae": [null]}'},
            {"metadata_output": '{"formulae": [{"name": "go"}]}'},
            {"metadata_code": 42},
            {"outcome": "oversized-metadata"},
        )
        for options in cases:
            with self.subTest(options=options):
                self.brew(**options)
                with (
                    patch.object(sys, "stdin", _Terminal("yes\n")),
                    redirect_stderr(io.StringIO()),
                    self.assertRaisesRegex(RuntimeError, "rexdeck enable files"),
                ):
                    dependencies.ensure("files")
                self.assertFalse(self.record.exists())
                self.assertFalse((self.bin / "yazi").exists())

    def test_installer_nonzero_missing_and_bad_postinstall_versions_are_failures(
        self,
    ) -> None:
        """Installer success alone cannot authorize missing or unqualified tools."""
        cases: tuple[tuple[_BrewOptions, str], ...] = (
            ({"outcome": "nonzero"}, "status 42"),
            ({"outcome": "missing"}, "still missing"),
            ({"yazi_version": "99.0.0"}, "Unqualified yazi"),
        )
        for options, error in cases:
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
        """Relative PATH entries cannot execute checkout-local prerequisite impostors."""
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
        """Rex remains an external prerequisite, never an automatic dependency."""
        with self.assertRaisesRegex(
            RuntimeError, "externally installed prerequisite.*rexdeck setup"
        ):
            lifecycle._executable("rex")
        self.assertFalse(self.record.exists())

    def _exercise_owned_helpers(self, *, capture: bool, terminate: bool) -> None:
        """Observe exact-session cleanup while an unrelated process remains alive."""
        ready = self.prefix / "ready.json"
        ready.unlink(missing_ok=True)
        installer = self.script(
            "long-installer",
            "import json, os, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], process_group=0)\n"
            "assert os.getsid(child.pid) == os.getpid()\n"
            "assert os.getpgid(child.pid) != os.getpid()\n"
            f"pathlib.Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "print('installer active', flush=True)\n"
            "time.sleep(300)\n",
        )
        driver_source = (
            "import json, os, pathlib, sys\n"
            "from rexdeck import dependencies\n"
            f"dependencies._METADATA_TIMEOUT = {10 if terminate else 2}\n"
            "try:\n"
            "    with dependencies._installer_signals():\n"
            f"        dependencies._brew_command([{str(installer)!r}], dict(os.environ), capture={capture!r})\n"
            "except (RuntimeError, KeyboardInterrupt) as error:\n"
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
            if terminate:
                self.assertEqual(os.getsid(leader), leader)
                self.assertEqual(os.getsid(descendant), leader)
                self.assertNotEqual(os.getpgid(descendant), leader)
                driver.send_signal(signal.SIGTERM)
            stdout, stderr = driver.communicate(timeout=8)
            self.assertEqual(driver.returncode, 23, stderr)
            result = json.loads(stdout)
            self.assertIn(
                "Received signal" if terminate else "timed out", result["error"]
            )
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
            try:
                cleanup_ids = json.loads(ready.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                cleanup_ids = []
            for pid in cleanup_ids:
                try:
                    if os.getsid(pid) == cleanup_ids[0]:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if driver.poll() is None:
                driver.kill()
            driver.communicate(timeout=8)
            unrelated.kill()
            unrelated.wait(timeout=8)

    def test_signals_stop_separate_helper_groups_during_metadata_and_install(
        self,
    ) -> None:
        """Cancellation reaches curl-like helper groups in both Homebrew phases."""
        for capture in (True, False):
            with self.subTest(metadata=capture):
                self._exercise_owned_helpers(capture=capture, terminate=True)

    def test_metadata_timeout_stops_separate_helper_groups_before_return(self) -> None:
        """A bounded metadata failure cannot leave an owned helper executing."""
        self._exercise_owned_helpers(capture=True, terminate=False)


if __name__ == "__main__":
    unittest.main()

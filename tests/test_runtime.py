"""Consumer-visible runtime trust boundaries; no compiler or network required."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rexdeck import runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.workspace = self.root / "workspace"
        self.project.mkdir()
        self.runtime_root = self.workspace / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.archive = self.runtime_root / "upstream.tar.gz"
        lock_path = Path(__file__).resolve().parents[1] / "runtime.lock.json"
        self.lock: dict[str, object] = json.loads(lock_path.read_text(encoding="utf-8"))
        self.source_name = str(self.lock["source_directory"])
        self._make_archive()
        self.lock["archive_sha256"] = hashlib.sha256(
            self.archive.read_bytes()
        ).hexdigest()
        self._write_lock()
        self.addCleanup(patch.stopall)
        patch.object(runtime, "_ARCHIVE_HASH", self.lock["archive_sha256"]).start()
        patch.object(platform, "system", return_value="Darwin").start()
        patch.object(platform, "machine", return_value="arm64").start()
        self.download = patch.object(
            runtime, "_download", side_effect=AssertionError("network forbidden")
        ).start()
        self.build = patch.object(
            runtime, "_build", side_effect=self._fake_build
        ).start()
        self.process_run = patch.object(
            subprocess, "run", side_effect=AssertionError("compiler forbidden")
        ).start()

    def _write_lock(self) -> None:
        (self.project / "runtime.lock.json").write_text(
            json.dumps(self.lock), encoding="utf-8"
        )

    def _make_archive(self, *, extra: tarfile.TarInfo | None = None) -> None:
        with tarfile.open(self.archive, "w:gz") as bundle:
            for directory in ("", "/sdk", "/sdk/lua", "/hub", "/view"):
                member = tarfile.TarInfo(self.source_name + directory)
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
                bundle.addfile(member)
            for name, content in {
                "LICENSE": b"MIT License\nPermission is hereby granted.\n",
                "sdk/lua/init.lua": b"return {}\n",
                "hub/go.mod": b"module example/hub\ngo 1.27.3\n",
                "view/go.mod": b"module example/view\ngo 1.27.3\n",
            }.items():
                member = tarfile.TarInfo(f"{self.source_name}/{name}")
                member.mode = 0o644
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
            if extra is not None:
                bundle.addfile(
                    extra, io.BytesIO(b"x" * extra.size) if extra.isfile() else None
                )

    @staticmethod
    def _fake_build(
        source: Path, output: Path, temporary: Path, lock: dict[str, object]
    ) -> None:
        del source, temporary, lock
        output.mkdir()
        for name in ("lazyrex-hub", "lazyrex-view"):
            binary = output / name
            binary.write_bytes(f"verified test build {name}".encode())
            binary.chmod(0o755)

    def _prepare(self) -> runtime.RuntimePaths:
        return runtime.prepare_runtime(self.project, self.workspace)

    def _snapshot(self) -> dict[str, tuple[bytes | None, int, int]]:
        result: dict[str, tuple[bytes | None, int, int]] = {}
        for path in self.workspace.rglob("*"):
            info = path.lstat()
            result[str(path.relative_to(self.workspace))] = (
                path.read_bytes() if path.is_file() and not path.is_symlink() else None,
                info.st_mode,
                info.st_mtime_ns,
            )
        return result

    def test_prepare_receipts_only_built_artifacts_and_preserves_license(self) -> None:
        paths = self._prepare()
        self.assertEqual(paths.sdk, paths.source / "sdk/lua")
        self.assertEqual(paths.hub, self.runtime_root / "bin/lazyrex-hub")
        self.assertEqual(paths.view, self.runtime_root / "bin/lazyrex-view")
        self.assertEqual(
            (paths.source / "LICENSE").read_bytes(),
            b"MIT License\nPermission is hereby granted.\n",
        )
        receipt = json.loads((self.runtime_root / "build.json").read_text())
        self.assertEqual(
            receipt["binaries"]["lazyrex-hub"],
            hashlib.sha256(paths.hub.read_bytes()).hexdigest(),
        )
        self.build.assert_called_once()
        self.download.assert_not_called()
        self.process_run.assert_not_called()

    def test_verification_and_repeated_prepare_are_read_only(self) -> None:
        paths = self._prepare()
        before = self._snapshot()
        self.assertEqual(runtime.verify_runtime(self.project, self.workspace), paths)
        self.assertEqual(self._prepare(), paths)
        self.assertEqual(self._snapshot(), before)
        self.build.assert_called_once()
        self.process_run.assert_not_called()

    def test_missing_receipt_does_not_self_bless_or_build(self) -> None:
        self._prepare()
        (self.runtime_root / "build.json").unlink()
        before = self._snapshot()
        for operation in (runtime.verify_runtime, runtime.prepare_runtime):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaises(RuntimeError),
            ):
                operation(self.project, self.workspace)
        self.assertEqual(self._snapshot(), before)
        self.build.assert_called_once()

    def test_source_byte_tamper_is_refused_without_repair(self) -> None:
        paths = self._prepare()
        (paths.sdk / "init.lua").write_text("return {tampered=true}\n")
        before = self._snapshot()
        for operation in (runtime.verify_runtime, runtime.prepare_runtime):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(RuntimeError, "source modified"),
            ):
                operation(self.project, self.workspace)
        self.assertEqual(self._snapshot(), before)
        self.build.assert_called_once()

    def test_extra_source_file_is_refused(self) -> None:
        paths = self._prepare()
        (paths.source / "injected.go").write_text("package injected\n")
        with self.assertRaisesRegex(RuntimeError, "extra or missing"):
            runtime.verify_runtime(self.project, self.workspace)

    def test_binary_tamper_is_refused_without_rebuild(self) -> None:
        paths = self._prepare()
        paths.view.write_bytes(b"foreign executable")
        for operation in (runtime.verify_runtime, runtime.prepare_runtime):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(RuntimeError, "binary digest mismatch"),
            ):
                operation(self.project, self.workspace)
        self.build.assert_called_once()

    def test_archive_tamper_is_refused_before_build(self) -> None:
        self.archive.write_bytes(b"not the pinned archive")
        with self.assertRaisesRegex(RuntimeError, "archive digest mismatch"):
            self._prepare()
        self.build.assert_not_called()
        self.assertFalse((self.runtime_root / "build.json").exists())

    def test_wrong_platform_does_not_create_workspace(self) -> None:
        target = self.root / "absent"
        with patch.object(platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(RuntimeError, "darwin/arm64"):
                runtime.prepare_runtime(self.project, target)
        self.assertFalse(target.exists())
        self.build.assert_not_called()

    def test_malformed_lock_is_an_actionable_nonmutating_error(self) -> None:
        target = self.root / "absent"
        for text in ('{"schema":', '{"schema":1,"schema":1}', "[]", '{"schema":true}'):
            with self.subTest(text=text):
                (self.project / "runtime.lock.json").write_text(text)
                with self.assertRaises(RuntimeError):
                    runtime.prepare_runtime(self.project, target)
                self.assertFalse(target.exists())
        self.build.assert_not_called()

    def test_malformed_receipts_are_refused(self) -> None:
        self._prepare()
        receipt = self.runtime_root / "build.json"
        good = json.loads(receipt.read_text())
        variants: list[object] = [
            [],
            {},
            {**good, "schema": True},
            {**good, "binaries": []},
            {**good, "binaries": {"lazyrex-hub": "z" * 64}},
            {**good, "go": "go version go1.27.2 darwin/arm64"},
        ]
        for value in variants:
            with self.subTest(value=value):
                receipt.write_text(json.dumps(value))
                before = receipt.read_bytes()
                with self.assertRaises(RuntimeError):
                    runtime.verify_runtime(self.project, self.workspace)
                self.assertEqual(receipt.read_bytes(), before)
        receipt.write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(RuntimeError, "Duplicate JSON key"):
            runtime.verify_runtime(self.project, self.workspace)
        self.build.assert_called_once()

    def test_source_binary_and_receipt_links_are_refused(self) -> None:
        paths = self._prepare()
        for path in (
            paths.sdk / "init.lua",
            paths.hub,
            self.runtime_root / "build.json",
        ):
            with self.subTest(path=path):
                original = path.read_bytes()
                mode = path.stat().st_mode & 0o777
                external = self.root / "external"
                external.write_bytes(original)
                external.chmod(mode)
                path.unlink()
                path.symlink_to(external)
                with self.assertRaises(RuntimeError):
                    runtime.verify_runtime(self.project, self.workspace)
                self.assertEqual(external.read_bytes(), original)
                path.unlink()
                path.write_bytes(original)
                path.chmod(mode)

    def test_hardlinked_binary_and_nonexecutable_binary_are_refused(self) -> None:
        paths = self._prepare()
        alias = self.root / "hub-alias"
        os.link(paths.hub, alias)
        with self.assertRaises(RuntimeError):
            runtime.verify_runtime(self.project, self.workspace)
        alias.unlink()
        paths.hub.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "not executable"):
            runtime.verify_runtime(self.project, self.workspace)

    def test_foreign_runtime_is_not_adopted_or_overwritten(self) -> None:
        foreign = self.runtime_root / "bin"
        foreign.mkdir()
        (foreign / "lazyrex-hub").write_bytes(b"foreign")
        before = self._snapshot()
        with self.assertRaisesRegex(RuntimeError, "unreceipted"):
            self._prepare()
        self.assertEqual(self._snapshot(), before)
        self.build.assert_not_called()

    def test_symlinked_runtime_directory_does_not_mutate_target(self) -> None:
        foreign = self.root / "foreign"
        foreign.mkdir()
        self.archive.unlink()
        self.runtime_root.rmdir()
        self.runtime_root.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self._prepare()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_unsafe_archive_members_never_extract(self) -> None:
        for name, kind in (
            ("../escape", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE),
            (f"{self.source_name}/../escape", tarfile.REGTYPE),
            (f"{self.source_name}/linked", tarfile.SYMTYPE),
            (f"{self.source_name}/hardlink", tarfile.LNKTYPE),
            (f"{self.source_name}/.gitmodules", tarfile.REGTYPE),
        ):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = 0o644
                member.linkname = "../../escape"
                member.size = 1 if kind == tarfile.REGTYPE else 0
                self._make_archive(extra=member)
                digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
                self.lock["archive_sha256"] = digest
                self._write_lock()
                with patch.object(runtime, "_ARCHIVE_HASH", digest):
                    with self.assertRaisesRegex(
                        RuntimeError, "Unsafe source archive member"
                    ):
                        self._prepare()
                self.assertFalse((self.runtime_root / "build.json").exists())
                self.assertFalse((self.root / "escape").exists())
        self.build.assert_not_called()

    def test_failed_build_does_not_publish_source_binary_or_receipt(self) -> None:
        self.build.side_effect = RuntimeError("build failed")
        with self.assertRaisesRegex(RuntimeError, "build failed"):
            self._prepare()
        self.assertEqual(
            {path.name for path in self.runtime_root.iterdir()}, {"upstream.tar.gz"}
        )

    def test_source_changed_during_build_does_not_get_receipt(self) -> None:
        def change_source(
            source: Path, output: Path, temporary: Path, lock: dict[str, object]
        ) -> None:
            self._fake_build(source, output, temporary, lock)
            (source / "LICENSE").write_bytes(b"changed")

        self.build.side_effect = change_source
        with self.assertRaisesRegex(RuntimeError, "source modified"):
            self._prepare()
        self.assertFalse((self.runtime_root / "build.json").exists())

    def test_lock_recipe_changes_invalidate_existing_receipt(self) -> None:
        self._prepare()
        # Both directories are safe source paths, but an altered recipe must not
        # reuse evidence from the original build.
        self.lock["builds"] = {
            "lazyrex-hub": {
                "directory": "view",
                "arguments": ["build", "-trimpath", "-o", "{output}", "."],
            },
            "lazyrex-view": {
                "directory": "view",
                "arguments": ["build", "-trimpath", "-o", "{output}", "."],
            },
        }
        self._write_lock()
        before = self._snapshot()
        for operation in (runtime.verify_runtime, runtime.prepare_runtime):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(RuntimeError, "receipt"),
            ):
                operation(self.project, self.workspace)
        self.assertEqual(self._snapshot(), before)
        self.build.assert_called_once()

    def test_unsafe_lock_recipes_fail_before_build_or_workspace_creation(self) -> None:
        target = self.root / "absent"
        for directory, arguments in (
            ("../foreign", ["build", "-trimpath", "-o", "{output}", "."]),
            ("hub", ["build", "-overlay=foreign", "-o", "{output}", "."]),
            ("hub", ["build", "-trimpath", "-o", "{output}", "../foreign"]),
            ("hub", ["build", "-trimpath", "-o", "{output}", "./../../foreign"]),
            ("hub", ["build", "-trimpath", "-o", "{output}", "-race"]),
        ):
            with self.subTest(directory=directory, arguments=arguments):
                self.lock["builds"] = {
                    "lazyrex-hub": {"directory": directory, "arguments": arguments},
                    "lazyrex-view": {
                        "directory": "view",
                        "arguments": ["build", "-trimpath", "-o", "{output}", "."],
                    },
                }
                self._write_lock()
                with self.assertRaises(RuntimeError):
                    runtime.prepare_runtime(self.project, target)
                self.assertFalse(target.exists())
        self.build.assert_not_called()

    def test_build_environment_changes_invalidate_existing_receipt(self) -> None:
        self._prepare()
        environment = self.lock["build_environment"]
        assert isinstance(environment, dict)
        self.lock["build_environment"] = {**environment, "CGO_ENABLED": "0"}
        self._write_lock()
        before = self._snapshot()
        for operation in (runtime.verify_runtime, runtime.prepare_runtime):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(RuntimeError, "receipt"),
            ):
                operation(self.project, self.workspace)
        self.assertEqual(self._snapshot(), before)
        self.build.assert_called_once()

    def test_unsafe_build_environments_fail_before_workspace_creation(self) -> None:
        target = self.root / "absent"
        environment = self.lock["build_environment"]
        assert isinstance(environment, dict)
        for key, value in (
            ("GOOS", "linux"),
            ("GOARCH", "amd64"),
            ("CGO_ENABLED", True),
            ("CGO_ENABLED", "2"),
            ("GOENV", "/foreign/settings"),
            ("GOTOOLCHAIN", "auto"),
            ("GOWORK", "/foreign/go.work"),
            ("GOFLAGS", "-overlay=/foreign/overlay.json"),
            ("GOPROXY", "direct"),
            ("GOPROXY", "https://proxy.golang.org,https://foreign.example"),
            ("GOSUMDB", "off"),
            ("GOMODCACHE", "/foreign/modules"),
        ):
            with self.subTest(key=key, value=value):
                self.lock["build_environment"] = {**environment, key: value}
                self._write_lock()
                with self.assertRaises(RuntimeError):
                    runtime.prepare_runtime(self.project, target)
                self.assertFalse(target.exists())
        self.lock["build_environment"] = {
            key: value for key, value in environment.items() if key != "GOTOOLCHAIN"
        }
        self._write_lock()
        with self.assertRaises(RuntimeError):
            runtime.prepare_runtime(self.project, target)
        self.assertFalse(target.exists())
        self.build.assert_not_called()

    def test_wrong_go_version_does_not_build_or_receipt(self) -> None:
        environment = self.lock["build_environment"]
        assert isinstance(environment, dict)
        expected_environment = {**environment, "CGO_ENABLED": "0"}
        self.lock["build_environment"] = expected_environment
        self._write_lock()
        self.build.side_effect = self._real_build
        self.process_run.side_effect = None
        self.process_run.return_value = subprocess.CompletedProcess(
            ["go", "version"], 0, "go version go1.26.0 darwin/arm64\n", ""
        )
        with patch.object(shutil, "which", return_value="/test/go"):
            with self.assertRaisesRegex(RuntimeError, "requires exactly"):
                self._prepare()
        self.process_run.assert_called_once()
        received_environment = self.process_run.call_args.kwargs["env"]
        for key, value in expected_environment.items():
            self.assertEqual(received_environment[key], value)
        self.assertFalse((self.runtime_root / "build.json").exists())

    def test_wrong_build_metadata_never_gets_receipted(self) -> None:
        self.build.side_effect = self._real_build

        def fake_go(
            arguments: list[str], **_options: object
        ) -> subprocess.CompletedProcess[str]:
            if arguments[1:] == ["version"]:
                text = "go version go1.27.3 darwin/arm64"
            elif arguments[1] == "build":
                output = Path(arguments[arguments.index("-o") + 1])
                output.write_bytes(b"built for the wrong target")
                output.chmod(0o755)
                text = ""
            elif arguments[1:3] == ["version", "-m"]:
                text = (
                    f"{arguments[3]}: go1.27.3\n"
                    "\tbuild\t-trimpath=true\n"
                    "\tbuild\tGOOS=linux\n"
                    "\tbuild\tGOARCH=arm64\n"
                    "\tbuild\tCGO_ENABLED=1\n"
                )
            else:
                raise AssertionError(f"Unexpected compiler command: {arguments}")
            return subprocess.CompletedProcess(arguments, 0, text, "")

        self.process_run.side_effect = fake_go
        with patch.object(shutil, "which", return_value="/test/go"):
            with self.assertRaisesRegex(RuntimeError, "does not match pinned"):
                self._prepare()
        self.assertFalse((self.runtime_root / "build.json").exists())
        self.assertFalse((self.runtime_root / "bin").exists())

    # Assigned before setUp replaces the module attribute.
    _real_build = staticmethod(runtime._build)


if __name__ == "__main__":
    unittest.main()

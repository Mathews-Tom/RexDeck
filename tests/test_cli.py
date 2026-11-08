"""Consumer-visible target approval and persistent Files ownership boundaries."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch

from rexdeck import cli, lifecycle, settings


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "init.lua"
        self.original = b"-- personal config\n"
        self.config.write_bytes(self.original)
        self.endpoint = "unix:///tmp/rexdeck-cli-private.sock"
        self.personal = "unix:///tmp/rexdeck-cli-personal.sock"
        self.settings = self.root / "config/rexdeck/config.json"
        self.workspace = self.root / "state/rexdeck/files"
        self.data_root = self.root / "data/rexdeck"
        self.module = self.data_root / "plugins/files/rexdeck_files.lua"
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.reject_check = False
        self.status_failure = False
        self.reported_endpoint: str | None = None
        environment = {
            "HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PATH": os.environ.get("PATH", ""),
        }
        patcher = patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, replacement in (
            ("_run", self.transport),
            ("_executable", lambda name: sys.executable),
            (
                "_tools",
                lambda: {
                    "python": sys.executable,
                    "rex": sys.executable,
                    "yazi": sys.executable,
                },
            ),
        ):
            boundary = patch.object(lifecycle, name, replacement)
            boundary.start()
            self.addCleanup(boundary.stop)

    def transport(
        self, argv: list[str], env: dict[str, str], *, timeout: float = 8.0
    ) -> lifecycle._Result:
        self.calls.append((argv.copy(), env.copy()))
        if "status" in argv and "server" in argv:
            if self.status_failure:
                return lifecycle._Result(1, b"selected server unavailable")
            endpoint = argv[argv.index("-S") + 1] if "-S" in argv else self.personal
            reported = (
                self.reported_endpoint
                if "-S" in argv and self.reported_endpoint is not None
                else endpoint
            )
            return lifecycle._Result(
                0, json.dumps({"endpoint": reported, "instance_id": endpoint}).encode()
            )
        if "check" in argv:
            return lifecycle._Result(
                0,
                json.dumps(
                    {"diagnostics": ["rejected"] if self.reject_check else []}
                ).encode(),
            )
        if "reload" in argv:
            return lifecycle._Result(0, b'{"diagnostics": []}')
        if "actions" in argv:
            module = self.module
            actions: list[dict[str, str]] = []
            # Model only the external Rex registration boundary. Ownership, settings,
            # config edits, bundle generation, receipts and rollback remain real.
            if module.exists() and b"\nif true then install(" in module.read_bytes():
                actions.append(
                    {
                        "name": "rexdeck_files",
                        "kind": "lua",
                        "title": "RexDeck Files",
                        "category": "RexDeck",
                        "source": str(module) + ":1",
                    }
                )
            return lifecycle._Result(0, json.dumps({"actions": actions}).encode())
        raise AssertionError(f"Unexpected Rex command: {argv}")

    def invoke(
        self, *args: str, terminal: str | None = None
    ) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO() if terminal is None else _Terminal(terminal)
        with (
            patch.object(sys, "argv", ["rexdeck", *args]),
            patch.object(sys, "stdin", stdin),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            try:
                code = cli.main()
            except SystemExit as exc:
                code = int(cast(int, exc.code))
        output = (
            stdout.getvalue()
            if stdout.getvalue()
            else stderr.getvalue().splitlines()[-1]
        )
        # An interactive approval prompt precedes a possible JSON error on stderr.
        if not output.startswith("{"):
            output = output[output.index("{") :]
        value: object = json.loads(output)
        return code, cast(dict[str, object], value)

    def configure(self, *extra: str) -> None:
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", self.endpoint, *extra
        )
        self.assertEqual((code, result["state"]), (0, "configured"))

    def document(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.settings.read_text()))

    def test_setup_only_saves_then_commands_use_saved_target(self) -> None:
        self.configure()
        saved = self.document()
        self.assertEqual(saved["config"], str(self.config))
        self.assertEqual(saved["workspace"], str(self.workspace))
        self.assertIs(saved["real_server"], False)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any("reload" in argv for argv, _ in self.calls))
        before = self.settings.read_bytes()
        self.assertEqual(self.invoke("setup")[0], 0)
        self.assertEqual(self.settings.read_bytes(), before)
        with patch.dict(
            os.environ,
            {
                "REX_CONFIG": "/unrelated",
                "REX_SERVER": "unix:///tmp/foreign.sock",
                "REX_SESSION": "foreign",
                "REX_BLOCK": "foreign",
                "REX_CLIENT": "foreign",
            },
        ):
            self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
            self.assertEqual(self.invoke("status")[1]["state"], "ready")
            self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.root / "rexdeck_files.lua").exists())
        self.assertFalse(self.document()["real_server"])
        self.assertTrue((self.workspace / "setup.json").is_file())
        for argv, env in self.calls:
            self.assertNotIn("REX_SESSION", env)
            self.assertNotIn("REX_BLOCK", env)
            self.assertNotIn("REX_CLIENT", env)
            if "-S" in argv:
                self.assertEqual(argv[argv.index("-S") + 1], self.endpoint)
                self.assertNotIn("REX_SERVER", env)

    def test_native_discovery_requires_real_approval_and_saves_it(self) -> None:
        native_config = self.root / "config/rex/init.lua"
        native_config.parent.mkdir(parents=True)
        native_config.write_bytes(self.original)
        code, result = self.invoke("setup")
        self.assertEqual(code, 1)
        self.assertIn("approval", str(result["error"]))
        self.assertFalse(self.settings.exists())
        code, result = self.invoke("setup", terminal="no\n")
        self.assertEqual(code, 1)
        self.assertFalse(self.settings.exists())
        code, result = self.invoke("setup", terminal="yes\n")
        self.assertEqual((code, result["state"]), (0, "configured"))
        self.assertIs(self.document()["real_server"], True)
        self.assertEqual(self.document()["server"], self.personal)
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(native_config.read_bytes().count(b"-- BEGIN REXDECK"), 1)

    def test_automation_real_approval_and_initial_rex_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REX_CONFIG": str(self.config),
                "REX_SERVER": self.personal,
                "REX_SESSION": "remove",
                "REX_BLOCK": "remove",
                "REX_CLIENT": "remove",
            },
        ):
            code, result = self.invoke("setup", "--real-server")
        self.assertEqual((code, result["state"]), (0, "configured"))
        self.assertEqual(self.document()["server"], self.personal)
        self.assertEqual(self.document()["config"], str(self.config))
        self.assertEqual(self.calls[0][1]["REX_SERVER"], self.personal)
        self.assertNotIn("REX_SESSION", self.calls[0][1])
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_initial_environment_selects_private_target_not_personal_default(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"REX_CONFIG": str(self.config), "REX_SERVER": self.endpoint},
        ):
            code, result = self.invoke("setup")
        self.assertEqual((code, result["state"]), (0, "configured"))
        self.assertEqual(self.document()["server"], self.endpoint)
        self.assertIs(self.document()["real_server"], False)
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_symlink_target_is_frozen_for_every_saved_command(self) -> None:
        selected = self.root / "selected.lua"
        selected.symlink_to(self.config)
        self.assertEqual(
            self.invoke("setup", "--config", str(selected), "--server", self.endpoint)[
                0
            ],
            0,
        )
        other = self.root / "other.lua"
        other.write_bytes(b"-- other\n")
        selected.unlink()
        selected.symlink_to(other)
        for command in (
            ("enable", "files"),
            ("disable", "files"),
            ("status",),
            ("setup",),
        ):
            with self.subTest(command=command):
                count = len(self.calls)
                code, result = self.invoke(*command)
                self.assertEqual(code, 1)
                self.assertIn("target changed", str(result["error"]))
                self.assertEqual(len(self.calls), count)
        self.assertEqual(other.read_bytes(), b"-- other\n")
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_retarget_never_overwrites_an_existing_saved_identity(self) -> None:
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        previous = self.settings.read_bytes()
        other = self.root / "other/init.lua"
        other.parent.mkdir()
        other.write_bytes(b"-- other\n")
        command = (
            "setup",
            "--config",
            str(other),
            "--server",
            self.endpoint,
            "--workspace",
            str(self.root / "new-workspace"),
        )
        code, result = self.invoke(*command)
        self.assertEqual(code, 1)
        self.assertIn("disable files", str(result["error"]))
        self.assertEqual(self.settings.read_bytes(), previous)
        self.assertEqual(self.invoke("disable", "files")[0], 0)
        self.assertEqual(self.invoke(*command)[0], 1)
        self.assertEqual(self.settings.read_bytes(), previous)
        self.assertEqual(self.document()["config"], str(self.config))
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(other.read_bytes(), b"-- other\n")

    def test_existing_native_workspace_can_be_selected_explicitly(self) -> None:
        existing = self.root / "native-files"
        old_package = self.root / "old-checkout"
        old_files = old_package / "plugins/files"
        old_files.mkdir(parents=True)
        current_files = Path(cli.__file__).resolve().parent / "plugins/files"
        for name in ("main.lua", "launch.py"):
            (old_files / name).write_bytes((current_files / name).read_bytes())
        setup = lifecycle.Setup(
            old_package, existing, self.config, self.data_root, self.endpoint
        )
        lifecycle.apply_setup(setup)
        lifecycle.start(setup)
        self.configure("--workspace", str(existing))
        self.assertEqual(self.document()["workspace"], str(existing))
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(self.config.read_bytes().count(b"-- BEGIN REXDECK"), 1)

    def test_new_target_uses_fresh_workspace_after_explicit_settings_removal(
        self,
    ) -> None:
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        old_receipt = (self.workspace / "setup.json").read_bytes()
        self.settings.unlink()
        other = self.root / "other.lua"
        other.write_bytes(b"-- other target\n")
        fresh = self.root / "new-target-state"
        code, result = self.invoke(
            "setup",
            "--config",
            str(other),
            "--server",
            self.endpoint,
            "--workspace",
            str(fresh),
        )
        self.assertEqual((code, result["state"]), (0, "configured"))
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        self.assertEqual(other.read_bytes(), b"-- other target\n")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual((self.workspace / "setup.json").read_bytes(), old_receipt)

    def test_unknown_plugin_and_removed_commands_do_not_mutate(self) -> None:
        for command in (
            ("enable", "other"),
            ("disable", "other"),
            ("status", "other"),
            ("apply",),
            ("start",),
            ("stop",),
            ("inspect",),
        ):
            with self.subTest(command=command):
                code, result = self.invoke(*command)
                self.assertEqual((code, result["state"]), (2, "failed"))
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.calls)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_commands_before_setup_give_instruction(self) -> None:
        for command in (("enable", "files"), ("status",), ("disable", "files")):
            with self.subTest(command=command):
                code, result = self.invoke(*command)
                self.assertEqual(code, 1)
                self.assertIn("rexdeck setup", str(result["error"]))
        self.assertFalse(self.calls)

    def test_saved_data_destination_survives_ambient_xdg_changes(self) -> None:
        self.configure()
        self.assertEqual(self.document()["data_root"], str(self.data_root))
        changed = self.root / "changed-data"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(changed)}):
            self.assertEqual(self.invoke("setup")[0], 0)
            self.assertEqual(self.invoke("enable", "files")[0], 0)
            self.assertTrue(self.module.is_file())
            self.assertFalse((self.config.parent / "rexdeck_files.lua").exists())
            self.assertFalse(changed.exists())
            self.assertEqual(self.invoke("disable", "files")[0], 0)
        self.assertFalse(self.module.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_foreign_module_is_not_adopted_and_failed_enable_does_not_start(
        self,
    ) -> None:
        module = self.module
        module.parent.mkdir(parents=True)
        module.write_bytes(b"-- foreign\n")
        self.assertEqual(
            self.invoke(
                "setup", "--config", str(self.config), "--server", self.endpoint
            )[0],
            1,
        )
        self.assertFalse(self.settings.exists())
        module.unlink()
        self.configure()
        self.reject_check = True
        code, result = self.invoke("enable", "files")
        self.assertEqual((code, result["state"]), (1, "failed"))
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(module.exists())
        self.assertFalse((self.workspace / "setup.json").exists())

    def test_relative_or_empty_xdg_paths_are_rejected(self) -> None:
        for name in (
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
        ):
            for value in ("", "relative"):
                with (
                    self.subTest(name=name, value=value),
                    patch.dict(os.environ, {name: value}),
                ):
                    code, result = self.invoke(
                        "setup", "--config", str(self.config), "--server", self.endpoint
                    )
                    self.assertEqual(code, 1)
                    self.assertIn(name, str(result["error"]))
        self.assertFalse(self.settings.exists())

    def test_corrupt_settings_are_never_masked_or_replaced(self) -> None:
        self.configure()
        valid = self.document()
        variants: list[object] = [
            None,
            [],
            {**valid, "version": True},
            {**valid, "version": 1},
            {**valid, "data_root": "relative"},
            {**valid, "real_server": "yes"},
            {**valid, "workspace": "relative"},
            {**valid, "config_target": 1},
            {**valid, "server": "tcp://localhost"},
            {**valid, "extra": "unknown"},
        ]
        for value in variants:
            with self.subTest(value=value):
                content = json.dumps(value).encode()
                self.settings.write_bytes(content)
                count = len(self.calls)
                self.assertEqual(
                    self.invoke(
                        "setup", "--config", str(self.config), "--server", self.endpoint
                    )[0],
                    1,
                )
                self.assertEqual(self.invoke("enable", "files")[0], 1)
                self.assertEqual(self.settings.read_bytes(), content)
                self.assertEqual(len(self.calls), count)
        for content in (b"{", b'{"version":1,"version":1}', b" " * (16 * 1024 + 1)):
            self.settings.write_bytes(content)
            self.assertEqual(self.invoke("status")[0], 1)
            self.assertEqual(self.settings.read_bytes(), content)

    def test_approval_flag_cannot_relabel_an_isolated_server(self) -> None:
        code, result = self.invoke(
            "setup",
            "--config",
            str(self.config),
            "--server",
            self.endpoint,
            "--real-server",
        )
        self.assertEqual(code, 1)
        self.assertIn("isolated", str(result["error"]))
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_atomic_settings_publication_never_clobbers_an_existing_target(
        self,
    ) -> None:
        self.configure()
        before = self.settings.read_bytes()
        different = settings.SavedTarget(
            self.root / "different-workspace",
            self.data_root,
            self.config,
            self.config,
            self.endpoint,
            False,
        )
        with self.assertRaises(FileExistsError):
            settings.save(self.settings, different)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(list(self.settings.parent.glob(".config-*")), [])

    def test_explicit_personal_target_must_be_running_and_match_endpoint(self) -> None:
        known = "unix://" + str(
            self.root / "Library/Application Support/rex/server.sock"
        )
        self.status_failure = True
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", known, "--real-server"
        )
        self.assertEqual(code, 1)
        self.assertFalse(self.settings.exists())
        self.status_failure = False
        self.reported_endpoint = self.personal
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", known, "--real-server"
        )
        self.assertEqual(code, 1)
        self.assertIn("different socket", str(result["error"]))
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_settings_symlink_and_fifo_are_rejected_without_reading_or_writing(
        self,
    ) -> None:
        self.configure()
        original = self.settings.read_bytes()
        backing = self.root / "saved.json"
        self.settings.rename(backing)
        self.settings.symlink_to(backing)
        self.assertEqual(self.invoke("enable", "files")[0], 1)
        self.assertEqual(backing.read_bytes(), original)
        self.settings.unlink()
        os.mkfifo(self.settings)
        self.assertEqual(self.invoke("status")[0], 1)
        self.assertEqual(self.config.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()

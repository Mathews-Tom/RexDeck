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

from rexdeck import cli, dependencies, integrations, lifecycle, settings


class _Terminal(io.StringIO):
    """Provide interactive approval for saving a personal Rex target."""

    def isatty(self) -> bool:
        """Keep target approval interactive without using a real terminal."""
        return True


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        """Freeze private paths and substitute only executable/Rex boundaries."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "init.lua"
        self.original = b"-- personal config\n"
        self.config.write_bytes(self.original)
        self.endpoint = "unix://" + str(Path("/tmp/rexdeck-cli-private.sock").resolve())
        self.personal = "unix://" + str(
            Path("/tmp/rexdeck-cli-personal.sock").resolve()
        )
        self.settings = self.root / "config/rexdeck/config.json"
        self.workspace = self.root / "state/rexdeck/files"
        self.data_root = self.root / "data/rexdeck"
        self.module = self.data_root / "plugins/files/rexdeck_files.lua"
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.reject_check = False
        self.status_failure = False
        self.reported_endpoint: str | None = None
        self.running_config = self.config
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
        self.resolve_executable = lifecycle._executable
        for name, replacement in (
            ("_run", self.transport),
            ("_executable", lambda name: sys.executable),
            (
                "_tools",
                lambda tool="yazi": {
                    "python": sys.executable,
                    "rex": sys.executable,
                    tool: sys.executable,
                },
            ),
        ):
            boundary = patch.object(lifecycle, name, replacement)
            boundary.start()
            self.addCleanup(boundary.stop)
        for name, dependency_replacement in (
            ("_resolve", lambda name: sys.executable),
            ("_probe", lambda executable, name: name.versions[-1]),
        ):
            dependency_boundary = patch.object(
                dependencies, name, dependency_replacement
            )
            dependency_boundary.start()
            self.addCleanup(dependency_boundary.stop)

    def transport(
        self, argv: list[str], env: dict[str, str], *, timeout: float = 8.0
    ) -> lifecycle._Result:
        """Model native registration for both plugins while ownership stays real."""
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
        if "client.keymap" in argv:
            return lifecycle._Result(
                0, json.dumps({"config": {"path": str(self.running_config)}}).encode()
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
            actions: list[dict[str, str]] = []
            # Model only the external Rex registration boundary. Ownership, settings,
            # config edits, bundle generation, receipts and rollback remain real.
            for plugin, title in (
                ("files", "Files"),
                ("git", "Git"),
                ("markdown", "Markdown"),
                ("usage", "Usage"),
                ("github", "GitHub"),
            ):
                module = self.data_root / f"plugins/{plugin}/rexdeck_{plugin}.lua"
                if (
                    module.exists()
                    and b"\nif true then install(" in module.read_bytes()
                ):
                    actions.append(
                        {
                            "name": f"rexdeck_{plugin}",
                            "kind": "lua",
                            "title": f"RexDeck {title}",
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

    def invoke_terminal(self, *args: str) -> tuple[int, str]:
        """Run with an interactive stdout, as a person at a terminal would."""
        stdout = _Terminal()
        with (
            patch.object(sys, "argv", ["rexdeck", *args]),
            patch.object(sys, "stdin", io.StringIO()),
            patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            code = cli.main()
        return code, stdout.getvalue()

    def configure(self, *extra: str) -> None:
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", self.endpoint, *extra
        )
        self.assertEqual((code, result["state"]), (0, "configured"))

    def document(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.settings.read_text()))

    def test_invalid_layout_arguments_are_json_errors_without_mutation(self) -> None:
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        paths = (self.config, self.settings, self.module, self.workspace / "setup.json")
        before = {path: path.read_bytes() for path in paths}
        for option, value in (
            ("--size", "81"),
            ("--placement", "diagonal"),
            ("--size", "3.5"),
        ):
            with self.subTest(option=option, value=value):
                code, result = self.invoke("enable", "files", option, value)
                self.assertEqual(code, 2)
                self.assertIn("error", result)
                self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_layout_persists_partial_updates_and_disable_enable(self) -> None:
        self.configure()
        code, result = self.invoke(
            "enable", "files", "--placement", "below", "--size", "30"
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["layout"], {"placement": "below", "size": 30})
        receipt = self.workspace / "setup.json"
        self.assertEqual(json.loads(receipt.read_text())["layout"], result["layout"])
        self.assertEqual(self.invoke("status", "files")[1]["layout"], result["layout"])
        code, result = self.invoke("enable", "files", "--size", "40")
        self.assertEqual(code, 0)
        expected = {"placement": "below", "size": 40}
        self.assertEqual(result["layout"], expected)
        self.assertEqual(self.invoke("disable", "files")[0], 0)
        self.assertEqual(self.invoke("enable", "files")[1]["layout"], expected)
        self.assertEqual(json.loads(receipt.read_text())["layout"], expected)
        before = (self.module.read_bytes(), receipt.read_bytes())
        self.assertEqual(
            self.invoke("enable", "git", "--placement", "left", "--size", "35")[0], 0
        )
        self.assertEqual((self.module.read_bytes(), receipt.read_bytes()), before)

    def test_legacy_receipt_defaults_and_malformed_layout_faults_without_mutation(
        self,
    ) -> None:
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        receipt = self.workspace / "setup.json"
        document = json.loads(receipt.read_text())
        self.assertEqual(document["version"], 3)
        del document["layout"]
        receipt.write_text(json.dumps(document))
        before = (
            self.config.read_bytes(),
            self.module.read_bytes(),
            receipt.read_bytes(),
        )
        code, result = self.invoke("status", "files")
        self.assertEqual(code, 0)
        self.assertEqual(result["layout"], {"placement": "right", "size": 50})
        self.assertEqual(
            (self.config.read_bytes(), self.module.read_bytes(), receipt.read_bytes()),
            before,
        )
        document["layout"] = {"placement": "below", "size": True}
        receipt.write_text(json.dumps(document))
        before = (
            self.config.read_bytes(),
            self.module.read_bytes(),
            receipt.read_bytes(),
        )
        for command in ("status", "enable", "disable"):
            code, result = self.invoke(command, "files")
            self.assertNotEqual(code, 0)
            self.assertIn("malformed layout", str(result))
            self.assertEqual(
                (
                    self.config.read_bytes(),
                    self.module.read_bytes(),
                    receipt.read_bytes(),
                ),
                before,
            )

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
        self.running_config = native_config
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

    def test_config_parent_traversal_follows_filesystem_not_lexical_target(
        self,
    ) -> None:
        physical = self.root / "physical"
        (physical / "nested").mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(physical / "nested", target_is_directory=True)
        actual = self.root / "actual.lua"
        original = b"-- selected through a directory symlink\n"
        actual.write_bytes(original)
        self.running_config = actual
        leaf = physical / "init.lua"
        leaf.symlink_to(actual)
        selected = alias / ".." / "init.lua"
        for source in ("argument", "environment"):
            with self.subTest(source=source):
                if source == "argument":
                    code, result = self.invoke(
                        "setup", "--config", str(selected), "--server", self.endpoint
                    )
                else:
                    with patch.dict(os.environ, {"REX_CONFIG": str(selected)}):
                        code, result = self.invoke("setup", "--server", self.endpoint)
                self.assertEqual((code, result["state"]), (0, "configured"))
                self.assertEqual(self.document()["config"], str(leaf))
                self.assertEqual(self.document()["config_target"], str(actual))
                self.assertEqual(self.invoke("enable", "files")[0], 0)
                self.assertNotEqual(actual.read_bytes(), original)
                self.assertEqual(self.config.read_bytes(), self.original)
                self.assertEqual(self.invoke("disable", "files")[0], 0)
                self.assertEqual(actual.read_bytes(), original)
                self.assertTrue(leaf.is_symlink())
                self.settings.unlink()

    def test_socket_alias_is_frozen_before_it_can_be_redirected(self) -> None:
        alias = self.root / "selected.sock"
        alias.symlink_to(lifecycle._socket_path(self.endpoint))
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", "unix://" + str(alias)
        )
        self.assertEqual((code, result["state"]), (0, "configured"))
        self.assertEqual(self.document()["server"], self.endpoint)
        alias.unlink()
        alias.symlink_to(lifecycle._socket_path(self.personal))
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        self.assertEqual(self.invoke("status")[1]["state"], "ready")
        self.assertEqual(self.invoke("disable", "files")[0], 0)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_saved_real_approval_survives_a_change_to_the_default_server(self) -> None:
        old_default = self.personal
        self.personal = self.endpoint
        self.configure("--real-server")
        self.personal = old_default
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        self.assertEqual(self.invoke("status")[1]["state"], "ready")
        self.assertTrue(self.document()["real_server"])
        self.assertEqual(self.invoke("disable", "files")[0], 0)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_matching_default_instance_requires_approval_even_at_another_socket(
        self,
    ) -> None:
        transport = self.transport

        def same_instance(
            argv: list[str], env: dict[str, str], *, timeout: float = 8.0
        ) -> lifecycle._Result:
            result = transport(argv, env, timeout=timeout)
            if "status" in argv and "server" in argv:
                status = json.loads(result.output)
                status["instance_id"] = "same-personal-instance"
                return lifecycle._Result(result.code, json.dumps(status).encode())
            return result

        with patch.object(lifecycle, "_run", same_instance):
            code, result = self.invoke(
                "setup", "--config", str(self.config), "--server", self.endpoint
            )
        self.assertEqual((code, result["state"]), (1, "failed"))
        self.assertIn("approval", str(result["error"]).lower())
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.module.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_mismatched_running_config_is_rejected_before_saving_setup(self) -> None:
        self.running_config = self.root / "another-server-config.lua"
        self.running_config.write_bytes(b"-- actual server config\n")
        code, result = self.invoke(
            "setup", "--config", str(self.config), "--server", self.endpoint
        )
        self.assertEqual((code, result["state"]), (1, "conflict"))
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.module.exists())
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.running_config.read_bytes(), b"-- actual server config\n")

    def test_saved_target_cannot_mutate_a_server_restarted_with_another_config(
        self,
    ) -> None:
        self.configure()
        self.running_config = self.root / "another-server-config.lua"
        self.running_config.write_bytes(b"-- actual server config\n")
        code, _ = self.invoke("enable", "files")
        self.assertEqual(code, 1)
        self.assertFalse(self.module.exists())
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.running_config.read_bytes(), b"-- actual server config\n")

    def test_missing_running_config_identity_is_not_treated_as_a_match(self) -> None:
        transport = self.transport

        def no_config_identity(
            argv: list[str], env: dict[str, str], *, timeout: float = 8.0
        ) -> lifecycle._Result:
            if "client.keymap" in argv:
                return lifecycle._Result(0, b"{}")
            return transport(argv, env, timeout=timeout)

        with patch.object(lifecycle, "_run", no_config_identity):
            code, result = self.invoke(
                "setup", "--config", str(self.config), "--server", self.endpoint
            )
        self.assertEqual((code, result["state"]), (1, "conflict"))
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.module.exists())
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
        self.assertEqual(self.document()["config"], str(selected))
        self.assertEqual(self.document()["config_target"], str(self.config))
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
        """Refuse target replacement while retaining the original ownership record."""
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
        code, _ = self.invoke(*command)
        self.assertEqual(code, 1)
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
        old_files = old_package / "host"
        old_files.mkdir(parents=True)
        current_files = Path(cli.__file__).resolve().parent / "host"
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
        self.running_config = other
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

    def test_plugins_before_setup_is_read_only_and_lists_unconfigured_plugins(
        self,
    ) -> None:
        """Discover integrations without creating a target or contacting Rex."""
        code, result = self.invoke("plugins")
        self.assertEqual((code, result["state"]), (0, "not-configured"))
        self.assertEqual(
            result["plugins"],
            {
                plugin: {
                    "state": "not-configured",
                    "integration": integrations.get(plugin).describe(),
                }
                for plugin in integrations.PLUGINS
            },
        )
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.workspace.exists())
        self.assertFalse(self.data_root.exists())
        self.assertFalse(self.calls)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_plugins_reports_enabled_and_disabled_without_mutating_ownership(
        self,
    ) -> None:
        """List a mixed deployment without enabling its disabled sibling."""
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        before = {
            path: path.read_bytes()
            for path in (
                self.config,
                self.settings,
                self.module,
                self.workspace / "setup.json",
            )
        }
        code, result = self.invoke("plugins")
        self.assertEqual((code, result["state"]), (0, "ready"))
        plugins = cast(dict[str, dict[str, object]], result["plugins"])
        self.assertEqual(set(plugins), set(integrations.PLUGINS))
        self.assertEqual(plugins["files"]["state"], "ready")
        self.assertIs(plugins["files"]["enabled"], True)
        self.assertEqual(plugins["git"]["state"], "disabled")
        self.assertIs(plugins["git"]["enabled"], False)
        self.assertEqual(plugins["viewer"]["state"], "disabled")
        self.assertIs(plugins["viewer"]["enabled"], False)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse((self.workspace / "git/setup.json").exists())
        self.assertFalse((self.data_root / "plugins/git").exists())

    def test_aggregate_status_preserves_sibling_with_malformed_receipt(self) -> None:
        """Keep enabled Files visible when Git's saved receipt cannot be decoded."""
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        workspace = self.workspace / "git"
        workspace.mkdir()
        receipt = workspace / "setup.json"
        receipt.write_text("{malformed")

        for command in ("status", "plugins"):
            with self.subTest(command=command):
                code, result = self.invoke(command)
                self.assertEqual((code, result["state"]), (1, "fault"))
                plugins = cast(dict[str, dict[str, object]], result["plugins"])
                self.assertEqual(set(plugins), set(lifecycle.PLUGINS))
                self.assertEqual(plugins["git"]["state"], "fault")
                self.assertEqual(plugins["git"]["server"], self.endpoint)
                self.assertIn("git", str(plugins["git"]["error"]))
                self.assertIn(str(receipt), str(plugins["git"]["error"]))
                self.assertEqual(plugins["files"]["state"], "ready")
                self.assertIs(plugins["files"]["enabled"], True)
                self.assertEqual(plugins["files"]["server"], self.endpoint)
        code, explicit = self.invoke("status", "git")
        self.assertEqual((code, explicit["state"]), (1, "failed"))
        self.assertIn(str(receipt), str(explicit["error"]))
        self.assertNotIn("plugins", explicit)

    def test_interactive_listing_is_a_table_and_json_stays_available(self) -> None:
        """A terminal gets one row per plugin plus the fix; --json keeps the document."""
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        (self.workspace / "git").mkdir()
        (self.workspace / "git/setup.json").write_text("{malformed")

        for command in ("plugins", "status"):
            with self.subTest(command=command):
                code, text = self.invoke_terminal(command)
                self.assertEqual(code, 1)
                lines = text.splitlines()
                self.assertEqual(lines[0], f"RexDeck {command}: fault")
                rows = {
                    words[0]: words[1]
                    for words in (line.split() for line in lines)
                    if words and words[0] in lifecycle.PLUGINS
                }
                self.assertEqual(set(rows), set(lifecycle.PLUGINS))
                self.assertEqual(
                    (rows["files"], rows["git"], rows["viewer"]),
                    ("ready", "fault", "disabled"),
                )
                problems = text.split("Needs attention:", 1)[1]
                self.assertIn("  git:", problems)
                self.assertNotIn("files:", problems)
                self.assertNotIn('"state"', text)

        code, text = self.invoke_terminal("plugins", "--json")
        self.assertEqual(code, 1)
        document = json.loads(text)
        self.assertEqual(document["plugins"]["git"]["state"], "fault")

        code, text = self.invoke_terminal("status", "files")
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "RexDeck status: ready")
        single = [line.split() for line in lines if line.split()[:1] == ["files"]]
        self.assertEqual(single, [["files", "ready", "yazi", "26.9.1", "right", "50%"]])
        self.assertNotIn("Needs attention:", text)
        code, text = self.invoke_terminal("status", "files", "--json")
        self.assertEqual((code, json.loads(text)["state"]), (0, "ready"))

    def test_enable_and_disable_several_plugins_in_one_command(self) -> None:
        """Each named plugin is changed and reported; repeated names count once."""
        self.configure()

        code, result = self.invoke("enable", "files", "git", "files")
        self.assertEqual((code, result["state"]), (0, "ready"))
        plugins = cast(dict[str, dict[str, object]], result["plugins"])
        self.assertEqual(list(plugins), ["files", "git"])
        self.assertEqual({row["state"] for row in plugins.values()}, {"ready"})
        for name in ("files", "git"):
            self.assertEqual(self.invoke("status", name)[1]["state"], "ready")

        code, result = self.invoke("disable", "git", "files")
        self.assertEqual((code, result["state"]), (0, "disabled"))
        for name in ("files", "git"):
            self.assertEqual(self.invoke("status", name)[1]["state"], "disabled")

        # A single name keeps the original single-result document.
        code, single = self.invoke("enable", "git", "git")
        self.assertEqual((code, single["state"]), (0, "ready"))
        self.assertNotIn("plugins", single)

    def test_one_failing_plugin_does_not_stop_the_others(self) -> None:
        """A broken sibling is reported in its row while the rest still change."""
        self.configure()
        (self.workspace / "git").mkdir(parents=True)
        (self.workspace / "git/setup.json").write_text("{malformed")

        code, result = self.invoke("enable", "git", "files")

        self.assertEqual((code, result["state"]), (1, "fault"))
        plugins = cast(dict[str, dict[str, object]], result["plugins"])
        self.assertEqual(plugins["git"]["state"], "failed")
        self.assertIn("git", str(plugins["git"]["error"]))
        self.assertEqual(plugins["files"]["state"], "ready")
        self.assertEqual(self.invoke("status", "files")[1]["state"], "ready")

    def test_an_unknown_name_refuses_the_whole_command_before_any_change(self) -> None:
        self.configure()

        code, result = self.invoke("enable", "files", "clickp")

        self.assertEqual((code, result["state"]), (2, "failed"))
        self.assertIn("clickp", str(result["error"]))
        self.assertEqual(self.invoke("status", "files")[1]["state"], "disabled")

    def test_several_plugins_at_a_terminal_are_one_table(self) -> None:
        self.configure()

        code, text = self.invoke_terminal("enable", "files", "git")

        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "RexDeck enable: ready")
        rows = [
            line.split()[:2]
            for line in lines
            if line.split()[:1] in (["files"], ["git"])
        ]
        self.assertEqual(rows, [["files", "ready"], ["git", "ready"]])
        self.assertNotIn('"state"', text)

    def test_status_reports_selected_tool_removed_after_enable(self) -> None:
        """A vanished upstream tool is a missing dependency, not a generic fault."""
        self.configure()
        self.assertEqual(self.invoke("enable", "files")[0], 0)
        self.assertEqual(self.invoke("enable", "git")[0], 0)
        empty = self.root / "empty-bin"
        empty.mkdir()
        tools = lifecycle._tools

        def without(missing: str) -> object:
            def lookup(tool: str = "yazi") -> dict[str, str]:
                # Real lookup on a PATH lacking the named executable.
                for name in ("rex", tool):
                    if name == missing:
                        self.resolve_executable(name)
                return tools(tool)

            return lookup

        with (
            patch.dict(os.environ, {"PATH": str(empty)}),
            patch.object(lifecycle, "_tools", side_effect=without("yazi")),
        ):
            code, explicit = self.invoke("status", "files")
            self.assertEqual((code, explicit["state"]), (1, "missing-dependency"))
            self.assertIs(explicit["enabled"], True)
            self.assertIs(explicit["applied"], True)
            self.assertEqual(explicit["layout"], {"placement": "right", "size": 50})
            self.assertIn("yazi", str(explicit["error"]))
            code, listing = self.invoke("plugins")
        self.assertEqual((code, listing["state"]), (1, "fault"))
        plugins = cast(dict[str, dict[str, object]], listing["plugins"])
        self.assertEqual(plugins["files"]["state"], "missing-dependency")
        self.assertEqual(plugins["git"]["state"], "ready")
        self.assertEqual(plugins["markdown"]["state"], "disabled")
        # Rex itself is a prerequisite of RexDeck, not of one integration.
        with (
            patch.dict(os.environ, {"PATH": str(empty)}),
            patch.object(lifecycle, "_tools", side_effect=without("rex")),
        ):
            code, explicit = self.invoke("status", "files")
        self.assertEqual((code, explicit["state"]), (1, "fault"))
        self.assertIn("Rex", str(explicit["error"]))
        self.assertEqual(self.invoke("status", "files")[1]["state"], "ready")

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

    def test_initial_symlinked_app_root_is_rejected_before_any_writes(self) -> None:
        self.data_root.parent.mkdir()
        for existing in (True, False):
            with self.subTest(existing=existing):
                target = self.root / ("foreign-data" if existing else "missing-data")
                marker = target / "keep.txt"
                if existing:
                    target.mkdir()
                    marker.write_bytes(b"foreign data\n")
                self.data_root.symlink_to(target, target_is_directory=True)
                code, result = self.invoke(
                    "setup", "--config", str(self.config), "--server", self.endpoint
                )
                self.assertEqual((code, result["state"]), (1, "failed"))
                self.assertIn("directory is redirected", str(result["error"]))
                self.assertFalse(self.settings.parent.exists())
                self.assertFalse(self.workspace.exists())
                self.assertFalse(self.calls)
                self.assertTrue(self.data_root.is_symlink())
                self.assertEqual(self.config.read_bytes(), self.original)
                if existing:
                    self.assertEqual(list(target.iterdir()), [marker])
                    self.assertEqual(marker.read_bytes(), b"foreign data\n")
                else:
                    self.assertFalse(target.exists())
                self.data_root.unlink()

    def test_xdg_data_base_alias_saves_canonical_root_and_allows_lifecycle(
        self,
    ) -> None:
        self.data_root.parent.mkdir()
        alias = self.root / "data-alias"
        alias.symlink_to(self.data_root.parent, target_is_directory=True)
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(alias)}):
            self.configure()
            self.assertEqual(self.document()["data_root"], str(self.data_root))
            self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
            self.assertTrue(self.module.is_file())
            self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.config.read_bytes(), self.original)

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

    def test_git_only_commands_ignore_unused_redirected_files_deployment(self) -> None:
        """Settings loading cannot let unused Files paths block healthy Git."""
        self.configure()
        outside = self.root / "unrelated-files-data"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_bytes(b"unrelated")
        files = self.data_root / "plugins/files"
        files.parent.mkdir(parents=True, exist_ok=True)
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                if kind == "file":
                    files.write_bytes(b"unrelated")
                else:
                    files.symlink_to(outside, target_is_directory=True)
                self.assertEqual(self.invoke("enable", "git")[1]["state"], "ready")
                code, result = self.invoke("status")
                self.assertEqual(code, 1)
                plugins = cast(dict[str, dict[str, object]], result["plugins"])
                self.assertEqual(plugins["files"]["state"], "fault")
                self.assertIn("files", str(plugins["files"]["error"]))
                self.assertEqual(plugins["git"]["state"], "ready")
                self.assertEqual(self.invoke("status", "git")[1]["state"], "ready")
                self.assertEqual(self.invoke("disable", "git")[1]["state"], "disabled")
                self.assertEqual(self.config.read_bytes(), self.original)
                self.assertEqual(marker.read_bytes(), b"unrelated")
                if kind == "file":
                    self.assertEqual(files.read_bytes(), b"unrelated")
                else:
                    self.assertEqual(files.readlink(), outside)
                files.unlink()

    def test_setup_and_status_never_prepare_optional_dependencies(self) -> None:
        """Target approval and inspection never install optional integration tools."""
        with (
            patch.object(
                dependencies, "ensure", side_effect=AssertionError("must not install")
            ),
            patch.object(
                lifecycle,
                "_selected_tools",
                side_effect=RuntimeError("optional tool is absent"),
            ),
        ):
            self.configure()
            self.assertEqual(self.invoke("setup")[1]["state"], "configured")
            self.assertEqual(self.invoke("status")[1]["state"], "disabled")
            self.assertEqual(self.invoke("status", "git")[1]["state"], "disabled")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.workspace.exists())
        self.assertFalse(self.data_root.exists())

    def test_missing_tool_preserves_sibling_until_owner_installs_it(self) -> None:
        """Missing-tool guidance never registers actions, even with affirmative input."""
        self.configure()
        self.assertEqual(self.invoke("enable", "git")[1]["state"], "ready")
        git_module = self.data_root / "plugins/git/rexdeck_git.lua"
        git_receipt = self.workspace / "git/setup.json"
        before = {
            path: path.read_bytes()
            for path in (self.config, self.settings, git_module, git_receipt)
        }
        with (
            patch.object(
                dependencies,
                "_resolve",
                side_effect=lambda name: None if name == "yazi" else sys.executable,
            ),
        ):
            for terminal in (None, "yes\n"):
                with self.subTest(terminal=terminal):
                    code, result = self.invoke("enable", "files", terminal=terminal)
                    self.assertEqual(code, 1)
                    self.assertIn("brew install yazi", str(result["error"]))
                    self.assertIn("rexdeck enable files", str(result["error"]))
                    self.assertFalse((self.workspace / "setup.json").exists())
                    for path, content in before.items():
                        self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(self.invoke("status", "git")[1]["state"], "ready")
        # A deliberate manual install and rerun use the same ordinary enable path.
        self.assertEqual(self.invoke("enable", "files")[1]["state"], "ready")
        self.assertEqual(self.invoke("disable", "files")[1]["state"], "disabled")
        self.assertEqual(self.invoke("status", "git")[1]["state"], "ready")

    def _reject_changed_tool_identity(self, boundary: str) -> None:
        """Exercise both owners with target changes at a locked mutation boundary."""
        self.configure()
        for plugin, sibling, tool in (
            ("files", "git", "yazi"),
            ("git", "files", "lazygit"),
        ):
            self.assertEqual(self.invoke("enable", sibling)[1]["state"], "ready")
            for mutation in ("atomic", "in_place", "symlink"):
                with self.subTest(plugin=plugin, mutation=mutation, boundary=boundary):
                    directory = self.root / f"{boundary}-{plugin}-{mutation}"
                    directory.mkdir()
                    target = directory / "target"
                    target.write_text("#!/bin/sh\nexit 0\n")
                    target.chmod(0o755)
                    qualified = directory / tool
                    qualified.symlink_to(target)
                    watched = (
                        self.settings,
                        self.config,
                        self.module,
                        self.workspace / "setup.json",
                        self.data_root / "plugins/git/rexdeck_git.lua",
                        self.workspace / "git/setup.json",
                    )

                    def snapshot() -> dict[Path, bytes | None]:
                        """Freeze both integrations and the approved target together."""
                        return {
                            path: path.read_bytes() if path.exists() else None
                            for path in watched
                        }

                    before = snapshot()
                    changed = False
                    tools = lifecycle._tools
                    bind = lifecycle._selected_tools
                    apply = lifecycle.apply_setup

                    def mutate() -> None:
                        """Change only the executable, never any owned integration file."""
                        nonlocal changed
                        self.assertFalse(changed)
                        previous = target.stat()
                        if mutation == "in_place":
                            target.write_text("#!/bin/sh\nexit 1\n")
                            os.utime(
                                target,
                                ns=(previous.st_atime_ns, previous.st_mtime_ns),
                            )
                            self.assertEqual(target.stat().st_ino, previous.st_ino)
                            self.assertEqual(target.stat().st_size, previous.st_size)
                        else:
                            replacement = directory / "replacement"
                            replacement.write_text("#!/bin/sh\nexit 9\n")
                            replacement.chmod(0o755)
                            if mutation == "atomic":
                                replacement.replace(target)
                            else:
                                qualified.unlink()
                                qualified.symlink_to(replacement)
                        changed = True

                    def tool_paths(name: str = "yazi") -> dict[str, str]:
                        """Keep the logical binding fixed while its target changes."""
                        result = tools(name)
                        if name == tool:
                            result[name] = str(qualified)
                        return result

                    def bind_then_mutate(setup: lifecycle.Setup) -> dict[str, str]:
                        """Replace the followed target after apply has bound its path."""
                        result = bind(setup)
                        if (
                            boundary == "apply"
                            and setup.plugin == plugin
                            and not changed
                        ):
                            mutate()
                        return result

                    def apply_then_mutate(
                        setup: lifecycle.Setup,
                        *,
                        expected_tool: lifecycle.QualifiedTool | None = None,
                        placement: str | None = None,
                        size: int | None = None,
                    ) -> dict[str, object]:
                        """Preserve the applied disabled bundle when start is rejected."""
                        nonlocal before
                        result = apply(
                            setup,
                            expected_tool=expected_tool,
                            placement=placement,
                            size=size,
                        )
                        if boundary == "start":
                            before = snapshot()
                            mutate()
                        return result

                    with (
                        patch.object(lifecycle, "_tools", side_effect=tool_paths),
                        patch.object(
                            lifecycle, "_selected_tools", side_effect=bind_then_mutate
                        ),
                        patch.object(
                            lifecycle, "apply_setup", side_effect=apply_then_mutate
                        ),
                        patch.object(
                            dependencies,
                            "_resolve",
                            side_effect=lambda name: (
                                str(qualified) if name == tool else sys.executable
                            ),
                        ),
                    ):
                        code, result = self.invoke("enable", plugin)
                        self.assertEqual(code, 1)
                        self.assertIn(
                            "changed after qualification", str(result["error"])
                        )
                        self.assertTrue(changed)
                        self.assertEqual(snapshot(), before)
                        self.assertEqual(
                            self.invoke("status", sibling)[1]["state"], "ready"
                        )
                        if boundary == "start":
                            receipt_path = self.workspace / (
                                "setup.json" if plugin == "files" else "git/setup.json"
                            )
                            receipt = json.loads(receipt_path.read_bytes())
                            self.assertIs(receipt["enabled"], False)
                            self.assertEqual(receipt["tools"][tool], str(qualified))
                        self.assertEqual(
                            self.invoke("disable", plugin)[1]["state"], "disabled"
                        )
            self.assertEqual(self.invoke("disable", sibling)[1]["state"], "disabled")
            self.assertEqual(self.config.read_bytes(), self.original)

    def test_changed_executable_identity_cannot_apply(self) -> None:
        """Same-path replacements and edits cannot register either integration."""
        self._reject_changed_tool_identity("apply")

    def test_changed_executable_identity_cannot_start(self) -> None:
        """A changed executable cannot enable an otherwise current applied bundle."""
        self._reject_changed_tool_identity("start")

    def test_path_shadow_at_apply_cannot_replace_qualified_selected_tool(self) -> None:
        """Reject a new PATH winner at the actual binding, preserving both owners."""
        self.configure()
        for plugin, sibling, tool in (
            ("files", "git", "yazi"),
            ("git", "files", "lazygit"),
        ):
            with self.subTest(plugin=plugin):
                self.assertEqual(self.invoke("enable", sibling)[1]["state"], "ready")
                selected_module = (
                    self.data_root / f"plugins/{plugin}/rexdeck_{plugin}.lua"
                )
                selected_receipt = self.workspace / (
                    "setup.json" if plugin == "files" else "git/setup.json"
                )
                watched = (
                    self.config,
                    self.settings,
                    self.module,
                    self.workspace / "setup.json",
                    self.data_root / "plugins/git/rexdeck_git.lua",
                    self.workspace / "git/setup.json",
                )
                before = {
                    path: path.read_bytes() if path.exists() else None
                    for path in watched
                }
                qualified = self.root / f"{plugin}-qualified" / tool
                shadow = self.root / f"{plugin}-earlier" / tool
                qualified.parent.mkdir()
                shadow.parent.mkdir()
                qualified.write_text("#!/bin/sh\nexit 0\n")
                qualified.chmod(0o755)
                tools = lifecycle._tools
                bind = lifecycle._selected_tools
                appeared = False
                probes: list[str] = []

                def tool_paths(name: str = "yazi") -> dict[str, str]:
                    """Expose the actual PATH winner only for the selected tool."""
                    result = tools(name)
                    if name == tool:
                        result[name] = self.resolve_executable(name)
                    return result

                def appear_at_binding(setup: lifecycle.Setup) -> dict[str, str]:
                    """Insert a new PATH winner after qualification but before binding."""
                    nonlocal appeared
                    if setup.plugin == plugin and not appeared:
                        shadow.write_text("#!/bin/sh\nexit 99\n")
                        shadow.chmod(0o755)
                        appeared = True
                    return bind(setup)

                def probe(executable: str, name: integrations.Integration) -> str:
                    """Record the binding actually version-qualified by this enable."""
                    probes.append(executable)
                    return name.versions[-1]

                with (
                    patch.dict(
                        os.environ,
                        {
                            "PATH": os.pathsep.join(
                                map(str, (shadow.parent, qualified.parent))
                            )
                        },
                    ),
                    patch.object(lifecycle, "_tools", side_effect=tool_paths),
                    patch.object(
                        lifecycle, "_selected_tools", side_effect=appear_at_binding
                    ),
                    patch.object(
                        dependencies,
                        "_resolve",
                        side_effect=lambda name: (
                            self.resolve_executable(name)
                            if name == tool
                            else sys.executable
                        ),
                    ),
                    patch.object(dependencies, "_probe", side_effect=probe),
                ):
                    code, result = self.invoke("enable", plugin)
                    self.assertEqual(code, 1)
                    self.assertIn("changed after qualification", str(result["error"]))
                    self.assertEqual(probes, [str(qualified)])
                    for path, content in before.items():
                        self.assertEqual(
                            path.read_bytes() if path.exists() else None, content
                        )
                    self.assertEqual(
                        self.invoke("status", sibling)[1]["state"], "ready"
                    )
                    shadow.unlink()
                    self.assertEqual(self.invoke("enable", plugin)[1]["state"], "ready")
                    receipt = json.loads(selected_receipt.read_text())
                    self.assertEqual(receipt["tools"][tool], str(qualified))
                    self.assertIn(
                        f'["{tool}"] = "{qualified}"'.encode(),
                        selected_module.read_bytes(),
                    )
                    self.assertEqual(probes, [str(qualified), str(qualified)])
                    self.assertEqual(
                        self.invoke("disable", plugin)[1]["state"], "disabled"
                    )
                self.assertEqual(self.invoke("status", sibling)[1]["state"], "ready")
                self.assertEqual(
                    self.invoke("disable", sibling)[1]["state"], "disabled"
                )
                self.assertEqual(self.config.read_bytes(), self.original)

    def test_replaced_bundle_between_apply_and_start_cannot_lose_qualification(
        self,
    ) -> None:
        """A separately refreshed receipt cannot authorize this enable's start."""
        self.configure()
        self.assertEqual(self.invoke("enable", "git")[1]["state"], "ready")
        qualified = self.root / "qualified/yazi"
        shadow = self.root / "earlier/yazi"
        qualified.parent.mkdir()
        shadow.parent.mkdir()
        qualified.write_text("#!/bin/sh\nexit 0\n")
        qualified.chmod(0o755)
        tools = lifecycle._tools
        apply = lifecycle.apply_setup
        replaced: dict[Path, bytes] = {}

        def tool_paths(name: str = "yazi") -> dict[str, str]:
            """Bind the selected tool through PATH while preserving sibling tools."""
            result = tools(name)
            if name == "yazi":
                result[name] = self.resolve_executable(name)
            return result

        def replace_after_apply(
            setup: lifecycle.Setup,
            *,
            expected_tool: lifecycle.QualifiedTool | None = None,
            placement: str | None = None,
            size: int | None = None,
        ) -> dict[str, object]:
            """Model another deliberate lower-level apply between CLI transactions."""
            result = apply(
                setup, expected_tool=expected_tool, placement=placement, size=size
            )
            shadow.write_text("#!/bin/sh\nexit 99\n")
            shadow.chmod(0o755)
            apply(setup)
            replaced.update(
                (path, path.read_bytes())
                for path in (
                    self.settings,
                    self.config,
                    self.module,
                    self.workspace / "setup.json",
                    self.data_root / "plugins/git/rexdeck_git.lua",
                    self.workspace / "git/setup.json",
                )
            )
            return result

        with (
            patch.dict(
                os.environ,
                {"PATH": os.pathsep.join(map(str, (shadow.parent, qualified.parent)))},
            ),
            patch.object(lifecycle, "_tools", side_effect=tool_paths),
            patch.object(
                dependencies,
                "_resolve",
                side_effect=lambda name: (
                    self.resolve_executable(name) if name == "yazi" else sys.executable
                ),
            ),
            patch.object(lifecycle, "apply_setup", side_effect=replace_after_apply),
        ):
            code, result = self.invoke("enable", "files")
            self.assertEqual(code, 1)
            self.assertIn("changed after qualification", str(result["error"]))
            self.assertTrue(replaced)
            for path, content in replaced.items():
                self.assertEqual(path.read_bytes(), content)
            receipt = json.loads((self.workspace / "setup.json").read_text())
            self.assertEqual(receipt["tools"]["yazi"], str(shadow))
            self.assertIs(receipt["enabled"], False)
            self.assertEqual(self.invoke("status", "files")[1]["state"], "disabled")
            self.assertEqual(self.invoke("status", "git")[1]["state"], "ready")

    def test_unqualified_preinstalled_tool_never_registers(self) -> None:
        """An unsupported owner-installed tool is refused without upstream mutation."""
        self.configure()
        for plugin, tool in (("files", "yazi"), ("git", "lazygit")):
            with self.subTest(plugin=plugin):
                with patch.object(
                    dependencies,
                    "_probe",
                    side_effect=RuntimeError(f"Unqualified {tool} version 99.0.0"),
                ):
                    code, result = self.invoke("enable", plugin)
                self.assertEqual(code, 1)
                self.assertIn(f"rexdeck enable {plugin}", str(result["error"]))
                self.assertEqual(self.config.read_bytes(), self.original)
                self.assertFalse(self.workspace.exists())
                self.assertFalse(self.module.exists())

    def test_tool_qualification_rejects_changed_saved_target(self) -> None:
        """An independent settings edit aborts stale-target registration."""
        self.configure()
        before = self.settings.read_bytes()

        def probe(_executable: str, name: integrations.Integration) -> str:
            """Model another operation changing the target during a version probe."""
            value = self.document()
            value["server"] = "unix:///tmp/rexdeck-replaced-target.sock"
            self.settings.write_text(json.dumps(value))
            return name.versions[-1]

        with patch.object(dependencies, "_probe", side_effect=probe):
            code, result = self.invoke("enable", "files")
        self.assertEqual(code, 1)
        self.assertIn("target changed", str(result["error"]))
        self.assertNotEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.workspace.exists())
        self.assertFalse(self.module.exists())

    def test_tool_qualification_revalidates_config_and_server(self) -> None:
        """A version probe cannot bypass frozen-target and live-server checks."""
        self.configure()
        backing = self.root / "backing.lua"
        self.config.rename(backing)
        self.config.symlink_to(backing)
        # Save the same selected symlink and its real target before dependency work.
        value = self.document()
        value["config_target"] = str(backing)
        self.settings.write_text(json.dumps(value))
        for change in ("config", "server"):
            with self.subTest(change=change):
                self.status_failure = False

                def probe(_executable: str, name: integrations.Integration) -> str:
                    """Redirect config or stop the server during executable validation."""
                    if change == "config":
                        other = self.root / "other.lua"
                        other.write_bytes(b"-- unrelated target\n")
                        self.config.unlink()
                        self.config.symlink_to(other)
                    else:
                        self.status_failure = True
                    return name.versions[-1]

                with patch.object(dependencies, "_probe", side_effect=probe):
                    code, _ = self.invoke("enable", "files")
                self.assertEqual(code, 1)
                self.assertEqual(backing.read_bytes(), self.original)
                self.assertFalse(self.workspace.exists())
                self.assertFalse(self.module.exists())
                self.config.unlink()
                self.config.symlink_to(backing)


if __name__ == "__main__":
    unittest.main()

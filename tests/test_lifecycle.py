"""Deterministic lifecycle tests: all Rex effects and runtime verification are isolated."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from rexdeck import lifecycle as life
from rexdeck.runtime import RuntimePaths


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "checkout"
        self.project.mkdir()
        files = self.project / "plugins" / "files"
        files.mkdir(parents=True)
        (files / "lazyrex.json").write_bytes(
            life._json({"name": "rexdeck_files", "main": "main.lua", "sdk": "0"})
        )
        (files / "main.lua").write_bytes(b"-- isolated test fixture; never executed\n")
        self.workspace = self.root / "workspace"
        self.target = self.root / "selected.lua"
        self.original = b"-- unrelated configuration without final newline"
        self.target.write_bytes(self.original)
        self.target.chmod(0o640)
        self.config = self.root / "init.lua"
        self.config.symlink_to(self.target)
        self.setup = life.Setup(
            self.project,
            self.workspace,
            self.config,
            "unix:///tmp/rexdeck-isolated-test.sock",
        )
        self.runtime = RuntimePaths(
            self.root / "source",
            self.root / "source/sdk/lua",
            self.root / "bin/lazyrex-hub",
            self.root / "bin/lazyrex-view",
        )
        self.clients: list[object] = []
        self.plugin_entries: list[object] = []
        self.calls: list[tuple[str, ...]] = []
        self.check_count = 0
        self.reject_check: int | None = None
        self.on_reload: object = None
        self.hub_info: dict[str, object] = {}
        self.process_identity: str | None = None
        self.endpoint_policy = life._endpoint_policy
        self.patch("_endpoint_policy", return_value=None)
        self.patch("verify_runtime", return_value=self.runtime)
        self.patch(
            "_tools",
            return_value={
                "python": "/python",
                "rex": "/rex",
                "bat": "/bat",
                "less": "/less",
            },
        )
        self.patch("_executable", side_effect=lambda name: f"/{name}")
        self.patch("_rex", side_effect=self.rex)
        self.patch("_process", side_effect=lambda _pid: self.process_identity)

    def patch(
        self, name: str, *, return_value: object = None, side_effect: object = None
    ) -> Mock:
        patcher = patch.object(
            life, name, return_value=return_value, side_effect=side_effect
        )
        mocked = cast(Mock, patcher.start())
        self.addCleanup(patcher.stop)
        return mocked

    def rex(self, _setup: life.Setup, *args: str) -> dict[str, object]:
        self.calls.append(args)
        if args == ("client", "ls", "--json"):
            return {"clients": self.clients}
        if args == ("config", "check", "--json"):
            self.check_count += 1
            if self.check_count == self.reject_check:
                return {"diagnostics": [{"message": "invalid config"}]}
            return {"diagnostics": []}
        if args == ("config", "reload", "--json"):
            if callable(self.on_reload):
                self.on_reload()
            return {"diagnostics": []}
        if args[-1] == "hub.info":
            return self.hub_info
        if args[-1] == "hub.plugins":
            return {"entries": self.plugin_entries}
        if args[-1] == "hub.admin.shutdown":
            self.process_identity = None
            self.clients = []
            palette = self.root / "rexdeck_palette.lua"
            palette.write_bytes(life._PALETTE)
            palette.chmod(0o644)
            return {"stopping": True}
        raise AssertionError(f"Unexpected Rex request: {args}")

    def test_clients_without_optional_metadata_do_not_prevent_setup(self) -> None:
        self.clients = [
            {"client_id": "unidentified", "connection_state": "connected"},
            {"client_id": "regular", "info": {"kind": "cli"}},
        ]
        result = life.apply_setup(self.setup)
        self.assertEqual(result["state"], "applied")
        self.assertTrue(self.config.is_symlink())
        self.assertTrue(self.target.read_bytes().startswith(self.original))
        self.assertTrue((self.workspace / "setup.json").is_file())

    def apply(self) -> None:
        life.apply_setup(self.setup)

    def read_receipt(self) -> dict[str, object]:
        return cast(
            dict[str, object], json.loads((self.workspace / "setup.json").read_bytes())
        )

    def write_receipt(self, receipt: dict[str, object]) -> None:
        (self.workspace / "setup.json").write_bytes(life._json(receipt))

    def own_running_hub(self) -> None:
        receipt = self.read_receipt()
        argv = [
            str(self.runtime.hub),
            "-config",
            str(self.workspace / "hub.json"),
            "-sdk",
            str(self.runtime.sdk),
        ]
        self.process_identity = "Sat Nov  7 12:00:00 2026 " + " ".join(argv)
        receipt["launch"] = {
            "pid": 1234,
            "argv": argv,
            "identity": self.process_identity,
        }
        self.write_receipt(receipt)
        self.clients = [
            {
                "client_id": "own",
                "connection_state": "connected",
                "info": {"label": "lazyrex-hub"},
            }
        ]
        self.hub_info = {
            "pid": 1234,
            "state": str(self.workspace / "hub-state"),
            "generation": "7",
        }

    def events(self, *events: dict[str, object]) -> None:
        (self.workspace / "hub-state/events.jsonl").write_bytes(
            b"".join(json.dumps(event).encode() + b"\n" for event in events)
        )

    def selected_file(self) -> Path:
        return self.workspace / "hub-state/plugins.json"

    def test_apply_seeds_only_home_and_managed_files_preserving_symlink_mode(
        self,
    ) -> None:
        self.apply()
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertTrue(self.target.read_bytes().startswith(self.original + b"\n"))
        hub = cast(
            dict[str, object], json.loads((self.workspace / "hub.json").read_bytes())
        )
        self.assertEqual(
            hub["plugins"], [{"source": str(self.runtime.source / "plugins/lazyrex")}]
        )
        self.assertEqual(hub["home"], "lazyrex")
        self.assertEqual(hub["profiles"], "")
        selected = cast(
            dict[str, object], json.loads(self.selected_file().read_bytes())
        )
        entries = cast(list[dict[str, object]], selected["plugins"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "files")
        self.assertEqual(entries[0]["source"], str(self.project / "plugins/files"))
        args = cast(dict[str, object], entries[0]["args"])
        self.assertEqual(
            args,
            {
                "python": "/python",
                "rex": "/rex",
                "bat": "/bat",
                "server": self.setup.server,
            },
        )

    def test_reapply_keeps_removed_selection_byte_for_byte(self) -> None:
        self.apply()
        removed = b'{"plugins": []}\n'
        self.selected_file().write_bytes(removed)
        result = life.apply_setup(self.setup)
        self.assertEqual(result["changed"], [])
        self.assertEqual(self.selected_file().read_bytes(), removed)

    def test_reseed_merges_instead_of_erasing_manager_selections(self) -> None:
        self.apply()
        other = {"id": "other", "source": "/somewhere/else", "args": {"keep": "yes"}}
        self.selected_file().write_bytes(life._json({"plugins": [other]}))
        life.apply_setup(self.setup, reseed=True)
        selected = cast(
            dict[str, object], json.loads(self.selected_file().read_bytes())
        )
        self.assertEqual(cast(list[object], selected["plugins"])[0], other)
        self.assertEqual(len(cast(list[object], selected["plugins"])), 2)

    def test_foreign_hub_refused_before_any_workspace_or_config_write(self) -> None:
        self.clients = [
            {
                "client_id": "foreign",
                "connection_state": "connected",
                "info": {"label": "lazyrex-hub"},
            }
        ]
        self.hub_info = {"pid": 99, "state": "/elsewhere", "generation": "1"}
        with self.assertRaisesRegex(RuntimeError, "Foreign or unverifiable"):
            life.apply_setup(self.setup)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse((self.root / "rexdeck_palette.lua").exists())

    def test_two_connected_hubs_are_ambiguous_even_with_same_state(self) -> None:
        self.clients = [
            {
                "client_id": str(i),
                "connection_state": "connected",
                "info": {"label": "lazyrex-hub"},
            }
            for i in range(2)
        ]
        with self.assertRaisesRegex(RuntimeError, "Multiple connected"):
            self.apply()
        self.assertFalse(self.workspace.exists())

    def test_lost_old_hub_client_does_not_block_setup(self) -> None:
        self.clients = [
            {
                "client_id": "old",
                "connection_state": "lost",
                "info": {"label": "lazyrex-hub"},
            }
        ]
        self.apply()
        self.assertTrue((self.workspace / "setup.json").exists())

    def test_rejected_config_rolls_back_loader_palette_hub_and_seed(self) -> None:
        self.reject_check = 2
        with self.assertRaisesRegex(RuntimeError, "owned changes rolled back"):
            self.apply()
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse((self.workspace / "hub.json").exists())
        self.assertFalse(self.selected_file().exists())
        self.assertFalse((self.root / "rexdeck_palette.lua").exists())
        self.assertFalse((self.workspace / "setup.json").exists())

    def test_failed_reload_rollback_preserves_concurrent_unrelated_config_append(
        self,
    ) -> None:
        first = True

        def rejected_reload() -> None:
            nonlocal first
            if first:
                first = False
                self.target.write_bytes(
                    self.target.read_bytes() + b"-- concurrent user edit\n"
                )
                raise RuntimeError("reload refused")

        self.on_reload = rejected_reload
        with self.assertRaisesRegex(RuntimeError, "reload refused"):
            self.apply()
        self.assertEqual(
            self.target.read_bytes(), self.original + b"-- concurrent user edit\n"
        )
        self.assertTrue(self.config.is_symlink())

    def test_partial_write_failure_removes_only_completed_owned_artifacts(self) -> None:
        original_replace = life._replace

        def partial(
            path: Path,
            before: life._File,
            after: bytes | None,
            *,
            mode: int | None = None,
        ) -> None:
            if path == self.target and after is not None and life._BEGIN in after:
                raise RuntimeError("write refused")
            original_replace(path, before, after, mode=mode)

        with patch.object(life, "_replace", side_effect=partial):
            with self.assertRaisesRegex(RuntimeError, "write refused"):
                self.apply()
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse((self.workspace / "hub.json").exists())
        self.assertFalse((self.root / "rexdeck_palette.lua").exists())

    def test_rollback_preserves_changed_artifact_and_reports_conflict(self) -> None:
        palette = self.root / "rexdeck_palette.lua"

        def changed_reload() -> None:
            palette.write_bytes(b"-- another writer\n")
            raise RuntimeError("reload rejected")

        self.on_reload = changed_reload
        with self.assertRaisesRegex(RuntimeError, "recovery needs judgment"):
            self.apply()
        self.assertEqual(palette.read_bytes(), b"-- another writer\n")
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_existing_palette_is_never_claimed(self) -> None:
        palette = self.root / "rexdeck_palette.lua"
        palette.write_bytes(b"-- user file\n")
        with self.assertRaisesRegex(RuntimeError, "Unowned existing artifact"):
            self.apply()
        self.assertEqual(palette.read_bytes(), b"-- user file\n")
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_disable_restores_exact_original_and_preserves_manager_data(self) -> None:
        self.apply()
        selected = self.selected_file().read_bytes()
        unrelated = b"-- later user setting\n"
        self.target.write_bytes(self.target.read_bytes() + unrelated)
        result = life.disable(self.setup)
        self.assertEqual(result["state"], "disabled")
        self.assertEqual(self.target.read_bytes(), self.original + unrelated)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(self.selected_file().read_bytes(), selected)
        self.assertFalse((self.root / "rexdeck_palette.lua").exists())
        self.assertFalse((self.workspace / "hub.json").exists())
        self.assertEqual(life.status(self.setup)["state"], "disabled")

    def test_disable_waits_for_owned_stop_then_removes_upstream_stop_header(
        self,
    ) -> None:
        self.apply()
        self.own_running_hub()
        result = life.disable(self.setup)
        self.assertEqual(result["state"], "disabled")
        self.assertIn(("-C", "own", "do", "hub.admin.shutdown"), self.calls)
        self.assertFalse((self.root / "rexdeck_palette.lua").exists())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_changed_palette_is_preserved_but_loader_is_removed(self) -> None:
        self.apply()
        palette = self.root / "rexdeck_palette.lua"
        palette.write_bytes(b"-- changed by operator\n")
        result = life.disable(self.setup)
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(palette.read_bytes(), b"-- changed by operator\n")
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_changed_loader_prevents_destructive_disable(self) -> None:
        self.apply()
        changed = self.target.read_bytes().replace(
            b"pcall(dofile", b"pcall(custom_loader"
        )
        self.target.write_bytes(changed)
        self.assertEqual(life.disable(self.setup)["state"], "conflict")
        self.assertEqual(self.target.read_bytes(), changed)
        self.assertTrue((self.root / "rexdeck_palette.lua").exists())

    def test_changed_owned_mode_is_preserved_as_conflict(self) -> None:
        self.apply()
        hub = self.workspace / "hub.json"
        hub.chmod(0o644)
        result = life.disable(self.setup)
        self.assertEqual(result["state"], "conflict")
        self.assertTrue(hub.exists())
        self.assertEqual(stat.S_IMODE(hub.stat().st_mode), 0o644)

    def test_status_reads_persisted_stop_without_reseeding_or_starting(self) -> None:
        self.apply()
        database = self.workspace / "hub-state/hub.db"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE plugins (name TEXT, desired TEXT, instance_id TEXT)"
            )
            connection.execute(
                "INSERT INTO plugins VALUES ('rexdeck_files', 'stopped', 'old')"
            )
        selected = self.selected_file().read_bytes()
        receipt = (self.workspace / "setup.json").read_bytes()
        with patch.object(subprocess, "Popen") as popen:
            result = life.status(self.setup)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(
            cast(dict[str, object], result["feature"])["state"], "disabled"
        )
        self.assertEqual(self.selected_file().read_bytes(), selected)
        self.assertEqual((self.workspace / "setup.json").read_bytes(), receipt)
        popen.assert_not_called()

    def test_removed_files_reports_host_ready_disabled_not_failed(self) -> None:
        self.apply()
        self.own_running_hub()
        self.plugin_entries = []
        result = life.status(self.setup)
        self.assertEqual(result["state"], "host-ready")
        self.assertEqual(
            cast(dict[str, object], result["feature"])["state"], "disabled"
        )

    def test_readiness_rejects_previous_generation_or_wrong_instance(self) -> None:
        self.apply()
        self.own_running_hub()
        self.plugin_entries = [
            {
                "managed": "files",
                "source": str(self.project / "plugins/files"),
                "name": "rexdeck_files",
                "state": "running",
                "desired": "running",
                "registered": True,
                "instance_id": "current",
            }
        ]
        self.events(
            {
                "event": "hub_started",
                "pid": 1234,
                "client_id": "own",
                "generation": "7",
            },
            {
                "event": "plugin_registered",
                "plugin": "rexdeck_files",
                "generation": "6",
                "instance_id": "current",
            },
            {
                "event": "plugin_registered",
                "plugin": "rexdeck_files",
                "generation": "7",
                "instance_id": "old",
            },
        )
        result = life.status(self.setup)
        self.assertEqual(result["state"], "host-ready")
        self.assertEqual(
            cast(dict[str, object], result["feature"])["state"], "starting"
        )
        self.events(
            {
                "event": "hub_started",
                "pid": 1234,
                "client_id": "own",
                "generation": "7",
            },
            {
                "event": "plugin_registered",
                "plugin": "rexdeck_files",
                "generation": "7",
                "instance_id": "current",
            },
        )
        self.assertEqual(life.status(self.setup)["state"], "feature-ready")

    def test_reused_pid_is_never_stopped(self) -> None:
        self.apply()
        self.own_running_hub()
        self.clients = []  # The crashed hub is no longer connected.
        self.process_identity = "different start time /unrelated/executable"
        with patch.object(os, "kill") as kill:
            self.assertEqual(life.status(self.setup)["state"], "stopped")
            self.assertEqual(life.stop(self.setup)["state"], "stopped")
        kill.assert_not_called()
        self.assertFalse(any(args[-1] == "hub.admin.shutdown" for args in self.calls))

    def test_inspect_does_not_apply_or_reload(self) -> None:
        result = life.inspect_setup(self.setup)
        self.assertEqual(result["state"], "inspected")
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(any(args[:1] == ("config",) for args in self.calls))

    def test_complete_bad_event_is_error_but_partial_append_is_ignored(self) -> None:
        self.apply()
        path = self.workspace / "hub-state/events.jsonl"
        path.write_bytes(b'{"event":"one"}\n{"incomplete":')
        self.assertEqual(life._events(life._paths(self.setup)), [{"event": "one"}])
        path.write_bytes(b'{"event":"one"}\nbad complete event\n')
        with self.assertRaisesRegex(RuntimeError, "Invalid JSON"):
            life._events(life._paths(self.setup))

    def test_explicit_config_environment_never_inherits_context(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REX_SERVER": "live",
                "REX_SESSION": "session",
                "REX_BLOCK": "block",
                "REX_CLIENT": "client",
            },
        ):
            env = life._env(self.setup)
        self.assertEqual(env["REX_CONFIG"], str(self.config))
        for key in ("REX_SERVER", "REX_SESSION", "REX_BLOCK", "REX_CLIENT"):
            self.assertNotIn(key, env)

    def mock_launch(self) -> Mock:
        child = Mock()
        child.pid = 1234
        child.poll.return_value = None

        def launch(argv: list[str], **_kwargs: object) -> Mock:
            self.process_identity = "Sat Nov  7 12:00:00 2026 " + " ".join(argv)
            self.clients = [
                {
                    "client_id": "own",
                    "connection_state": "connected",
                    "info": {"label": "lazyrex-hub"},
                }
            ]
            self.hub_info = {
                "pid": 1234,
                "state": str(self.workspace / "hub-state"),
                "generation": "7",
            }
            self.events(
                {
                    "event": "hub_started",
                    "pid": 1234,
                    "client_id": "own",
                    "generation": "7",
                }
            )
            return child

        popen = patch.object(subprocess, "Popen", side_effect=launch)
        mocked = cast(Mock, popen.start())
        self.addCleanup(popen.stop)
        return mocked

    def test_start_removed_files_starts_once_without_reseeding_or_real_flag(
        self,
    ) -> None:
        self.apply()
        self.selected_file().write_bytes(b'{"plugins": []}\n')
        selected = self.selected_file().read_bytes()
        popen = self.mock_launch()
        result = life.start(self.setup)
        self.assertEqual(result["state"], "host-ready")
        self.assertEqual(
            cast(dict[str, object], result["feature"])["state"], "disabled"
        )
        self.assertEqual(self.selected_file().read_bytes(), selected)
        popen.assert_called_once()
        call = popen.call_args
        assert call is not None
        argv = cast(list[str], call.args[0])
        self.assertNotIn("-real-server", argv)
        self.assertEqual(self.read_receipt()["real_server"], False)

    def test_start_missing_prepared_runtime_never_launches_or_reseeds(self) -> None:
        self.apply()
        selected = self.selected_file().read_bytes()
        with patch.object(
            life,
            "verify_runtime",
            side_effect=RuntimeError("prepare the pinned runtime"),
        ):
            with patch.object(subprocess, "Popen") as popen:
                with self.assertRaisesRegex(RuntimeError, "prepare the pinned runtime"):
                    life.start(self.setup)
        popen.assert_not_called()
        self.assertEqual(self.selected_file().read_bytes(), selected)

    def test_start_timeout_leaves_owned_process_receipt_without_launch_retry(
        self,
    ) -> None:
        self.apply()
        popen = self.mock_launch()
        with patch.object(life, "_START_WAIT", 0):
            with self.assertRaisesRegex(RuntimeError, "hub left intact"):
                life.start(self.setup)
        popen.assert_called_once()
        self.assertIsNotNone(self.read_receipt()["launch"])

    def test_disable_failed_config_check_restores_removed_artifact_modes(self) -> None:
        self.apply()
        palette = self.root / "rexdeck_palette.lua"
        palette.chmod(0o644)
        receipt = self.read_receipt()
        receipt["palette_mode"] = 0o644
        self.write_receipt(receipt)
        enabled_config = self.target.read_bytes()
        self.reject_check = self.check_count + 1
        with self.assertRaisesRegex(RuntimeError, "Disable failed"):
            life.disable(self.setup)
        self.assertEqual(self.target.read_bytes(), enabled_config)
        self.assertEqual(palette.read_bytes(), life._PALETTE)
        self.assertEqual(stat.S_IMODE(palette.stat().st_mode), 0o644)
        self.assertTrue((self.workspace / "hub.json").exists())
        self.assertEqual(self.read_receipt()["enabled"], True)

    def test_isolated_non_tmp_socket_refused_before_any_write(self) -> None:
        setup = life.Setup(
            self.project, self.workspace, self.config, "unix:///var/rexdeck.sock"
        )
        with self.assertRaisesRegex(RuntimeError, "under /tmp or /private/tmp"):
            life.apply_setup(setup)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_tmp_symlink_to_non_tmp_socket_is_not_an_isolation_bypass(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            link = Path(temporary) / "outside"
            link.symlink_to("/var")
            setup = life.Setup(
                self.project, self.workspace, self.config, f"unix://{link}/rexdeck.sock"
            )
            with self.assertRaisesRegex(RuntimeError, "under /tmp or /private/tmp"):
                life.apply_setup(setup)
        self.assertFalse(self.workspace.exists())

    def test_endpoint_policy_refuses_same_server_instance_under_another_socket(
        self,
    ) -> None:
        default = {"endpoint": "unix:///some/live.sock", "instance_id": "same"}
        selected = {"endpoint": self.setup.server, "instance_id": "same"}
        with patch.object(life, "_server_status", side_effect=[default, selected]):
            with self.assertRaisesRegex(RuntimeError, "explicit --real-server"):
                self.endpoint_policy(self.setup)

    def test_real_server_flag_is_never_inferred_or_accepted_for_a_lab(self) -> None:
        real = life.Setup(
            self.project,
            self.workspace,
            self.config,
            self.setup.server,
            real_server=True,
        )
        default = {"endpoint": "unix:///some/live.sock", "instance_id": "live"}
        selected = {"endpoint": self.setup.server, "instance_id": "lab"}
        with patch.object(life, "_server_status", side_effect=[default, selected]):
            with self.assertRaisesRegex(RuntimeError, "remove the flag"):
                self.endpoint_policy(real)

    def test_saved_exact_readiness_survives_bounded_event_tail_rotation(self) -> None:
        self.apply()
        self.own_running_hub()
        self.plugin_entries = [
            {
                "managed": "files",
                "source": str(self.project / "plugins/files"),
                "name": "rexdeck_files",
                "state": "running",
                "desired": "running",
                "registered": True,
                "instance_id": "current",
            }
        ]
        receipt = self.read_receipt()
        receipt["readiness"] = {
            "pid": 1234,
            "client_id": "own",
            "generation": "7",
            "instance_id": "current",
        }
        self.write_receipt(receipt)
        self.events({"event": "later", "generation": "7"})
        self.assertEqual(life.status(self.setup)["state"], "feature-ready")
        self.plugin_entries[0] = cast(dict[str, object], self.plugin_entries[0]) | {
            "instance_id": "replacement"
        }
        self.assertEqual(life.status(self.setup)["state"], "feature-ready")
        self.plugin_entries[0] = cast(dict[str, object], self.plugin_entries[0]) | {
            "registered": False
        }
        self.assertEqual(life.status(self.setup)["state"], "host-ready")

    def test_explicit_real_opt_in_propagates_only_the_unchanged_flag(self) -> None:
        self.setup = life.Setup(
            self.project,
            self.workspace,
            self.config,
            self.setup.server,
            real_server=True,
        )
        self.apply()
        self.selected_file().write_bytes(b'{"plugins": []}\n')
        popen = self.mock_launch()
        self.assertEqual(life.start(self.setup)["state"], "host-ready")
        call = popen.call_args
        assert call is not None
        self.assertEqual(cast(list[str], call.args[0])[-1], "-real-server")
        self.assertEqual(self.read_receipt()["real_server"], True)

    def test_stop_does_not_report_success_before_owned_process_termination(
        self,
    ) -> None:
        self.apply()
        self.own_running_hub()
        palette = self.root / "rexdeck_palette.lua"
        before = palette.read_bytes()

        def pending_shutdown(setup: life.Setup, *args: str) -> dict[str, object]:
            if args[-1] == "hub.admin.shutdown":
                return {"stopping": True}
            return self.rex(setup, *args)

        with patch.object(life, "_rex", side_effect=pending_shutdown):
            with patch.object(life, "_STOP_WAIT", 0):
                with self.assertRaisesRegex(RuntimeError, "did not terminate"):
                    life.stop(self.setup)
        self.assertEqual(palette.read_bytes(), before)
        self.assertIsNotNone(self.read_receipt()["launch"])

    def test_concurrent_lifecycle_operation_cannot_disable_starting_writer(
        self,
    ) -> None:
        self.apply()
        enabled = self.target.read_bytes()
        with life._operation_lock(life._paths(self.setup)):
            with self.assertRaisesRegex(
                RuntimeError, "lifecycle operation is in progress"
            ):
                life.disable(self.setup)
        self.assertEqual(self.target.read_bytes(), enabled)
        self.assertEqual(self.read_receipt()["enabled"], True)

    def test_first_apply_never_adopts_an_unreceipted_upstream_database(self) -> None:
        state = self.workspace / "hub-state"
        state.mkdir(parents=True)
        database = state / "hub.db"
        database.write_bytes(b"unrelated state")
        with self.assertRaisesRegex(RuntimeError, "Unowned existing hub state"):
            self.apply()
        self.assertEqual(database.read_bytes(), b"unrelated state")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse((self.workspace / "setup.json").exists())

    def test_selected_wrong_source_fails_before_process_launch(self) -> None:
        self.apply()
        manifest = self.project / "plugins/files/lazyrex.json"
        manifest.write_bytes(life._json({"name": "another_plugin", "main": "main.lua"}))
        with patch.object(subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "not the RexDeck Files plugin"):
                life.start(self.setup)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

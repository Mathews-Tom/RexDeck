"""Native lifecycle boundaries with real Lua loading and isolated Rex transport."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from rexdeck import integrations
from rexdeck import lifecycle as life

# This stand-in owns only the external Rex boundary. It executes the selected Lua
# config and generated module, rather than inferring actions from receipt state.
_LUA_CONFIG = r"""
local actions = {}
rex = {
  action = function(action)
    assert(type(action.run) == "function", "action has no callable run")
    local info = debug.getinfo(2, "Sl")
    local filename = info.source:gsub("^@", "")
    local prefix = arg[1]:match("^(.*[/])")
    if filename:sub(1, #prefix) == prefix then
      filename = filename:sub(#prefix + 1)
    end
    actions[#actions + 1] = {
      name = action.name, kind = "lua", title = action.title,
      category = action.category, source = filename .. ":" .. info.currentline,
    }
  end,
}
local previous_path = package.path
local cached = {}
package.loaded.rexdeck_files = cached
local ok, err = pcall(dofile, arg[1])
assert(package.path == previous_path, "loader leaked module search path")
assert(package.loaded.rexdeck_files == cached, "loader corrupted module cache")
if not ok then io.stderr:write(tostring(err)); os.exit(1) end
local function hex(value)
  assert(type(value) == "string", "action metadata is not a string")
  return (value:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end
for _, action in ipairs(actions) do
  print(table.concat({hex(action.name), hex(action.kind), hex(action.title),
    hex(action.category), hex(action.source)}, "\t"))
end
"""


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        lua = shutil.which("lua") or shutil.which("luajit")
        if lua is None:
            self.skipTest("Lua is required to exercise native config/module loading")
        self.lua = lua
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'checkout-"-é'
        self.project.mkdir()
        files = self.project / "host"
        files.mkdir(parents=True)
        canonical = Path(life.__file__).resolve().parent / "host"
        (files / "main.lua").write_bytes((canonical / "main.lua").read_bytes())
        (files / "launch.py").write_bytes((canonical / "launch.py").read_bytes())
        self.workspace = self.root / "workspace"
        self.target = self.root / 'selected-"-é.lua'
        self.original = b"-- unrelated configuration without final newline"
        self.target.write_bytes(self.original)
        self.target.chmod(0o640)
        self.config = self.root / "init.lua"
        self.config.symlink_to(self.target)
        self.setup = life.Setup(
            self.project,
            self.workspace,
            self.config,
            self.root / "data/rexdeck",
            "unix:///tmp/rexdeck-isolated-test.sock",
        )
        self.module = self.setup.data_root / "plugins/files/rexdeck_files.lua"
        self.receipt_path = self.workspace / "setup.json"
        self.harness = self.root / "config-harness.lua"
        self.harness.write_text(_LUA_CONFIG)
        self.actions: list[dict[str, object]] = []
        self.calls: list[tuple[str, ...]] = []
        self.check_count = 0
        self.reject_check: int | None = None
        self.fail_reload = False
        self.on_reload: object = None
        self.patch("_endpoint_policy", return_value=None)
        self.patch("_executable", side_effect=lambda name: f"/{name}")
        self.patch(
            "_tools",
            return_value={
                "python": "/a python/é",
                "rex": '/a "rex"',
                "yazi": "/yazi",
            },
        )
        self.patch("_rex", side_effect=self.rex)

    def patch(
        self, name: str, *, return_value: object = None, side_effect: object = None
    ) -> Mock:
        patcher = patch.object(
            life, name, return_value=return_value, side_effect=side_effect
        )
        mocked = cast(Mock, patcher.start())
        self.addCleanup(patcher.stop)
        return mocked

    def evaluate(self) -> list[dict[str, object]]:
        result = subprocess.run(
            [self.lua, str(self.harness), str(self.config)],
            capture_output=True,
            check=False,
            timeout=5,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        rows: list[dict[str, object]] = []
        for line in result.stdout.decode().splitlines():
            values = [bytes.fromhex(value).decode() for value in line.split("\t")]
            rows.append(
                dict(
                    zip(
                        ("name", "kind", "title", "category", "source"),
                        values,
                        strict=True,
                    )
                )
            )
        return rows

    def rex(self, _setup: life.Setup, *args: str) -> dict[str, object]:
        self.calls.append(args)
        if args == ("actions", "--json"):
            return {"actions": self.actions}
        if args == ("config", "check", "--json"):
            self.check_count += 1
            if self.check_count == self.reject_check:
                return {"diagnostics": [{"message": "injected invalid configuration"}]}
            try:
                self.evaluate()
            except RuntimeError as exc:
                return {"diagnostics": [{"message": str(exc)}]}
            return {"diagnostics": []}
        if args == ("config", "reload", "--json"):
            self.actions = self.evaluate()
            if callable(self.on_reload):
                self.on_reload()
            if self.fail_reload:
                self.fail_reload = False
                return {"diagnostics": [{"message": "injected reload failure"}]}
            return {"diagnostics": []}
        raise AssertionError(f"Unexpected Rex request: {args}")

    def receipt(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.receipt_path.read_bytes()))

    def snapshot(self) -> tuple[bytes, bytes | None, bytes | None]:
        return (
            self.target.read_bytes(),
            self.module.read_bytes() if self.module.exists() else None,
            self.receipt_path.read_bytes() if self.receipt_path.exists() else None,
        )

    def updated_package(self) -> life.Setup:
        project = self.root / 'installed-"-é'
        shutil.copytree(self.project, project)
        for name, comment in (
            ("main.lua", b"-- update\n"),
            ("launch.py", b"# update\n"),
        ):
            asset = project / "host" / name
            asset.write_bytes(asset.read_bytes() + b"\n" + comment)
        shutil.rmtree(self.project)
        return replace(self.setup, project=project)

    def test_applied_launcher_does_not_import_from_the_invoking_directory(self) -> None:
        project = self.root / "untrusted-project"
        project.mkdir()
        marker = project / "imported-project-code"
        (project / "json.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "raise RuntimeError('project code imported by launcher')\n"
        )
        harness = self.root / "execute-action.lua"
        harness.write_text(
            r"""
local action
local function quote(value)
  return "'" .. value:gsub("'", "'\\''") .. "'"
end
local function execute(argv)
  local words = {}
  for _, value in ipairs(argv) do words[#words + 1] = quote(value) end
  local ok = os.execute(table.concat(words, " ") .. " >" .. quote(arg[3]) .. " 2>&1")
  local stream = assert(io.open(arg[3], "rb"))
  local output = stream:read("*a")
  stream:close()
  return {ok = ok == 0 or ok == true, stdout = output, stderr = output}
end
rex = {
  action = function(value) action = value end,
  call = function(name)
    if name == "session.attach" then return {} end
    assert(name == "com.superlogical.terminal.pwd")
    return {pwd = arg[2]}
  end,
  exec = execute,
  json = {decode = function(raw)
    local cwd, target = raw:match('^{"cwd": "([^"]+)", "target": "([^"]+)"}%s*$')
    assert(cwd == arg[2] and target == arg[2], raw)
    return {cwd = cwd, target = target}
  end},
  session = {new_split = function(request)
    local result = execute(request.layout.block.options.command)
    assert(result.ok, result.stderr)
    return {block_ids = {"launched"}}
  end},
}
dofile(arg[1])
action.run({session_id = "session", block_id = "origin"})
"""
        )
        # Real Python runs both entry points; true substitutes only external tools.
        with patch.object(
            life,
            "_tools",
            return_value={
                "python": sys.executable,
                "rex": "/usr/bin/true",
                "yazi": "/usr/bin/true",
            },
        ):
            life.apply_setup(self.setup)
            life.start(self.setup)
        result = subprocess.run(
            [
                self.lua,
                str(harness),
                str(self.module),
                str(project),
                str(self.root / "out"),
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), "Opening Files imported project code")

    def test_env_script_pane_starts_with_its_qualified_interpreter_first(
        self,
    ) -> None:
        # The app-started Rex server PATH lacks e.g. nvm's node for `env node` tools.
        harness = self.root / "split-request.lua"
        harness.write_text(
            r"""
local action
rex = {
  action = function(value) action = value end,
  call = function(name)
    if name == "session.attach" then return {} end
    return {pwd = arg[2]}
  end,
  exec = function() return {ok = true, stdout = "{}"} end,
  json = {decode = function() return {cwd = arg[2], target = arg[2]} end},
  session = {new_split = function(request)
    local options = request.layout.block.options
    assert(options.shell == "none", "tool must start without a shell")
    local prepend = options.path_prepend
    print(prepend == nil and "<none>" or table.concat(prepend, ":"))
    return {block_ids = {"launched"}}
  end},
}
dofile(arg[1])
action.run({session_id = "session", block_id = "origin"})
"""
        )

        def prepended(tools: dict[str, str]) -> str:
            with patch.object(life, "_tools", return_value=tools):
                life.apply_setup(self.setup)
                life.start(self.setup)
                # The interpreter is part of the receipt's qualified tool set.
                self.assertEqual(life.status(self.setup)["state"], "ready")
            result = subprocess.run(
                [self.lua, str(harness), str(self.module), str(self.root)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        tools = {"python": sys.executable, "rex": "/rex", "yazi": "/opt/yazi"}
        self.assertEqual(prepended(tools), "<none>")
        tools["interpreter"] = "/home/u/.nvm/versions/node/v23/bin/node"
        self.assertEqual(prepended(tools), "/home/u/.nvm/versions/node/v23/bin")

    def test_oversized_planned_config_is_rejected_before_owned_writes(self) -> None:
        self.target.write_bytes(b"--" + b"x" * (life._MAX_READ - 2))
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(self.actions, [])

    def test_rollback_collects_unlink_failure_and_restores_other_artifacts(
        self,
    ) -> None:
        unlink = Path.unlink

        def deny_module(path: Path, missing_ok: bool = False) -> None:
            if path == self.module:
                raise PermissionError(f"Cannot remove {path}")
            unlink(path, missing_ok=missing_ok)

        def leave_stale_registration() -> None:
            self.on_reload = None
            self.actions = [{"name": "stale-registration-from-failed-reload"}]

        self.on_reload = leave_stale_registration
        self.fail_reload = True
        with patch.object(Path, "unlink", deny_module):
            with self.assertRaises(RuntimeError) as failure:
                life.apply_setup(self.setup)
        self.assertIn(str(self.module), str(failure.exception))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertFalse(self.receipt_path.exists())
        self.assertTrue(self.module.is_file())
        self.assertEqual(self.actions, [])

    def test_repeated_disable_preserves_a_later_workspace_installation(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        life.disable(self.setup)
        old_receipt = self.receipt_path.read_bytes()
        other = replace(self.setup, workspace=self.root / "another-workspace")
        life.apply_setup(other)
        life.start(other)
        other_receipt = other.workspace / "setup.json"
        before = (
            self.target.read_bytes(),
            self.module.read_bytes(),
            other_receipt.read_bytes(),
        )
        self.assertEqual(life.disable(self.setup)["state"], "disabled")
        self.assertEqual(
            (
                self.target.read_bytes(),
                self.module.read_bytes(),
                other_receipt.read_bytes(),
            ),
            before,
        )
        self.assertEqual(self.receipt_path.read_bytes(), old_receipt)
        self.assertEqual(life.status(other)["state"], "ready")

    def test_real_termination_after_reload_recovers_before_exit(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum):
                before = self.snapshot()

                def terminate() -> None:
                    self.on_reload = None
                    os.kill(os.getpid(), signum)

                self.on_reload = terminate
                child = os.fork()
                if child == 0:
                    try:
                        life.apply_setup(self.setup)
                    except KeyboardInterrupt:
                        os._exit(0)
                    except BaseException:
                        os._exit(3)
                    os._exit(2)
                _, status = os.waitpid(child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)

    def test_start_preserves_module_edited_during_registration_check(self) -> None:
        life.apply_setup(self.setup)
        changed = self.module.read_bytes() + b"-- concurrent operator edit\n"
        receipt = self.receipt_path.read_bytes()
        expect_action = life._expect_action

        def edit_module(setup: life.Setup, enabled: bool) -> None:
            expect_action(setup, enabled)
            if not enabled:
                self.module.write_bytes(changed)

        with patch.object(life, "_expect_action", edit_module):
            with self.assertRaises(RuntimeError):
                life.start(self.setup)
        self.assertEqual(self.module.read_bytes(), changed)
        self.assertEqual(self.receipt_path.read_bytes(), receipt)
        self.assertEqual(self.actions, [])

    def test_apply_starts_disabled_and_native_selection_survives_reapply(self) -> None:
        life.apply_setup(self.setup)
        self.assertFalse((self.target.parent / "rexdeck_files.lua").exists())
        self.assertEqual(self.actions, [])
        self.assertEqual(life.status(self.setup)["state"], "disabled")
        self.assertTrue(self.receipt()["applied"])
        self.assertFalse(self.receipt()["enabled"])
        life.apply_setup(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "disabled")
        self.assertEqual(life.start(self.setup)["state"], "ready")
        self.assertEqual([row["name"] for row in self.actions], ["rexdeck_files"])
        life.apply_setup(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "ready")
        self.assertEqual(life.disable(self.setup)["state"], "disabled")
        self.assertEqual(self.actions, [])
        life.apply_setup(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "disabled")
        life.start(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "ready")

    def test_disable_restores_symlink_target_bytes_and_mode(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        result = life.disable(self.setup)
        self.assertEqual(result["state"], "disabled")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse(self.module.exists())
        self.assertEqual(self.actions, [])
        self.assertFalse(self.receipt()["applied"])
        life.apply_setup(self.setup)
        self.assertFalse(self.receipt()["enabled"])

    def test_disable_never_starts_or_kills_processes(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        with patch.object(os, "kill") as kill, patch.object(life, "_run") as run:
            life.disable(self.setup)
        kill.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.actions, [])

    def test_native_registration_not_receipt_determines_readiness(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        self.actions = []
        self.assertEqual(life.status(self.setup)["state"], "fault")
        life.start(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "ready")
        self.actions[0]["kind"] = "remote"
        self.assertEqual(life.status(self.setup)["state"], "fault")

    def test_wrong_source_or_ambiguous_registration_is_not_ready(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        original = self.actions[0].copy()
        relative_source = self.module.relative_to(self.config.parent)
        for owned_source in (f"{relative_source}:3", f"{self.module}:3"):
            with self.subTest(owned_source=owned_source):
                self.actions = [original | {"source": owned_source}]
                self.assertEqual(life.status(self.setup)["state"], "ready")
        for source in (
            None,
            "rexdeck_files.lua:3",
            "other.lua:3",
            "rexdeck_files.lua:not-a-line",
            str(self.root / "foreign" / "rexdeck_files.lua") + ":3",
        ):
            with self.subTest(source=source):
                self.actions = [original | {"source": source}]
                self.assertEqual(life.status(self.setup)["state"], "fault")
        self.actions = [original, original.copy()]
        self.assertEqual(life.status(self.setup)["state"], "fault")

    def test_disabled_selection_detects_unexpected_action(self) -> None:
        life.apply_setup(self.setup)
        self.actions = [{"name": "rexdeck_files", "kind": "lua"}]
        self.assertEqual(life.status(self.setup)["state"], "fault")

    def test_missing_tool_does_not_mask_registration_fault(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        missing = life.MissingDependency("Required executable not found: yazi")
        with patch.object(life, "_tools", side_effect=missing):
            row = life.status(self.setup)
            self.assertEqual(row["state"], "missing-dependency")
            self.assertEqual(row["layout"], {"placement": "right", "size": 50})
            self.actions = []  # registration lost as well
            row = life.status(self.setup)
        self.assertEqual(row["state"], "fault")
        self.assertIn("yazi", str(row["error"]))

    def test_status_does_not_write_or_reload(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        self.calls.clear()
        life.status(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.calls, [("actions", "--json")])

    def test_foreign_action_refuses_without_creating_workspace(self) -> None:
        self.actions = [{"name": "rexdeck_files", "kind": "lua"}]
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertFalse(self.receipt_path.exists())
        self.assertFalse(self.workspace.exists())
        self.assertFalse(self.module.exists())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_unrelated_native_actions_are_preserved_by_transport(self) -> None:
        self.target.write_bytes(
            b"rex.action {name='foreign',title='Foreign',category='Other',run=function() end}\n"
        )
        life.apply_setup(self.setup)
        life.start(self.setup)
        self.assertEqual(
            [row["name"] for row in self.actions], ["foreign", "rexdeck_files"]
        )
        life.disable(self.setup)
        self.assertEqual([row["name"] for row in self.actions], ["foreign"])

    def test_foreign_lazyrex_artifacts_are_neither_required_nor_touched(self) -> None:
        foreign = self.root / "lazyrex_palette.lua"
        foreign.write_bytes(b"-- unrelated owner file\n")
        self.target.write_bytes((f"dofile({life._lua(str(foreign))})\n").encode())
        original = self.target.read_bytes()
        life.apply_setup(self.setup)
        life.start(self.setup)
        life.disable(self.setup)
        self.assertEqual(foreign.read_bytes(), b"-- unrelated owner file\n")
        self.assertEqual(self.target.read_bytes(), original)
        self.assertFalse(any("client" in call for call in self.calls))

    def test_canonical_or_launcher_change_requires_explicit_reapply(self) -> None:
        life.apply_setup(self.setup)
        for name in ("main.lua", "launch.py"):
            source = self.project / "host" / name
            source.write_bytes(source.read_bytes() + b"\n")
            before = self.snapshot()
            with self.assertRaises(RuntimeError):
                life.start(self.setup)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(life.status(self.setup)["state"], "fault")
            life.apply_setup(self.setup)
            life.start(self.setup)
            self.assertEqual(life.status(self.setup)["state"], "ready")

    def test_updated_package_reapply_refreshes_provenance_and_preserves_enabled(
        self,
    ) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        previous = self.receipt()
        updated = self.updated_package()
        before = self.snapshot()
        self.assertEqual(life.status(updated)["state"], "fault")
        self.assertEqual(self.snapshot(), before)
        life.apply_setup(updated)
        refreshed = self.receipt()
        self.assertEqual(refreshed["project"], str(updated.project))
        for key in ("module_sha256", "source_sha256", "launcher_sha256"):
            self.assertNotEqual(refreshed[key], previous[key])
        self.assertTrue(refreshed["enabled"])
        self.assertEqual(life.status(updated)["state"], "ready")
        life.disable(updated)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertFalse(self.module.exists())

    def test_updated_package_can_disable_without_refreshing_old_assets(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        updated = self.updated_package()
        self.assertEqual(life.disable(updated)["state"], "disabled")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse(self.module.exists())
        self.assertFalse(self.receipt()["applied"])
        self.assertEqual(self.actions, [])
        life.apply_setup(updated)
        self.assertFalse(self.receipt()["enabled"])
        self.assertEqual(self.receipt()["project"], str(updated.project))
        life.start(updated)
        self.assertEqual(life.status(updated)["state"], "ready")

    def test_owned_module_change_refuses_start_and_reapply_but_disable_preserves(
        self,
    ) -> None:
        life.apply_setup(self.setup)
        changed = self.module.read_bytes() + b"-- operator change\n"
        self.module.write_bytes(changed)
        updated = self.updated_package()
        for operation in (life.start, life.apply_setup):
            with self.assertRaises(RuntimeError):
                operation(updated)
        result = life.disable(updated)
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(self.module.read_bytes(), changed)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_module_mode_change_is_preserved_as_conflict(self) -> None:
        life.apply_setup(self.setup)
        self.module.chmod(0o644)
        self.assertEqual(life.disable(self.setup)["state"], "conflict")
        self.assertEqual(stat.S_IMODE(self.module.stat().st_mode), 0o644)

    def test_changed_loader_is_not_removed_or_claimed(self) -> None:
        life.apply_setup(self.setup)
        changed = self.target.read_bytes().replace(
            integrations.get("files").loader_markers[0],
            integrations.get("files").loader_markers[0] + b"-- operator edit\n",
        )
        self.target.write_bytes(changed)
        self.assertEqual(life.disable(self.setup)["state"], "conflict")
        self.assertEqual(self.target.read_bytes(), changed)
        self.assertTrue(self.module.exists())

    def test_unrelated_appended_config_and_new_mode_survive_disable(self) -> None:
        life.apply_setup(self.setup)
        extra = b"-- concurrent statement\n"
        self.target.write_bytes(self.target.read_bytes() + extra)
        self.target.chmod(0o600)
        life.disable(self.setup)
        self.assertEqual(self.target.read_bytes(), self.original + b"\n" + extra)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_rejected_apply_rolls_back_original_config_and_module(self) -> None:
        self.reject_check = 1
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), (self.original, None, None))
        self.assertEqual(self.actions, [])

    def test_failed_start_restores_disabled_selection(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        self.fail_reload = True
        with self.assertRaises(RuntimeError):
            life.start(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actions, [])
        self.assertEqual(life.status(self.setup)["state"], "disabled")

    def test_failed_disable_restores_enabled_action_and_config(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        before = self.snapshot()
        self.fail_reload = True
        with self.assertRaises(RuntimeError):
            life.disable(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(life.status(self.setup)["state"], "ready")

    def test_rollback_preserves_concurrent_config_append(self) -> None:
        extra = b"-- concurrent user append\n"

        def change_then_fail() -> None:
            self.on_reload = None
            self.target.write_bytes(self.target.read_bytes() + extra)
            raise RuntimeError("injected failure after concurrent append")

        self.on_reload = change_then_fail
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertEqual(self.target.read_bytes(), self.original + b"\n" + extra)
        self.assertFalse(self.module.exists())
        self.assertFalse(self.receipt_path.exists())

    def test_ctrl_c_after_reload_recovers_before_propagating(self) -> None:
        def cancel() -> None:
            self.on_reload = None
            raise KeyboardInterrupt

        self.on_reload = cancel
        with self.assertRaises(KeyboardInterrupt) as raised:
            life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), (self.original, None, None))
        self.assertEqual(self.actions, [])
        self.assertTrue(raised.exception.__notes__)

    def test_ctrl_c_after_atomic_replace_is_rolled_back(self) -> None:
        replace = life._replace

        def cancel_after_write(
            path: Path,
            before: life._File,
            after: bytes | None,
            *,
            mode: int | None = None,
        ) -> None:
            replace(path, before, after, mode=mode)
            if path == self.module and after is not None:
                raise KeyboardInterrupt

        with patch.object(life, "_replace", side_effect=cancel_after_write):
            with self.assertRaises(KeyboardInterrupt):
                life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), (self.original, None, None))

    def test_receipt_publication_failure_rolls_back_action(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        replace = life._replace
        failed = False

        def fail_after_publication(
            path: Path,
            previous: life._File,
            after: bytes | None,
            *,
            mode: int | None = None,
        ) -> None:
            nonlocal failed
            replace(path, previous, after, mode=mode)
            if path == self.receipt_path and not failed:
                failed = True
                raise RuntimeError("publication handoff failed")

        with patch.object(life, "_replace", side_effect=fail_after_publication):
            with self.assertRaises(RuntimeError):
                life.start(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actions, [])

    def test_package_refresh_failure_restores_previous_owned_bundle(self) -> None:
        life.apply_setup(self.setup)
        life.start(self.setup)
        before = self.snapshot()
        actions = self.actions.copy()
        updated = self.updated_package()
        self.reject_check = self.check_count + 1
        with self.assertRaises(RuntimeError):
            life.apply_setup(updated)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actions, actions)
        life.disable(updated)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.module.exists())

    def test_package_provenance_must_remain_an_absolute_path(self) -> None:
        life.apply_setup(self.setup)
        receipt = self.receipt()
        project: object
        for project in (None, "", "relative/package", "/invalid\x00package", 7, []):
            self.receipt_path.write_bytes(life._json(receipt | {"project": project}))
            before = self.snapshot()
            for operation in (life.apply_setup, life.disable):
                with (
                    self.subTest(project=project, operation=operation.__name__),
                    self.assertRaises(RuntimeError),
                ):
                    operation(self.setup)
                self.assertEqual(self.snapshot(), before)

    def test_updated_package_does_not_relax_setup_ownership(self) -> None:
        life.apply_setup(self.setup)
        receipt = self.receipt()
        updated = self.updated_package()
        different: dict[str, object] = {
            "version": 999,
            "kind": "other",
            "workspace": str(self.root / "other-workspace"),
            "data_root": str(self.root / "other-data"),
            "config": str(self.target),
            "config_target": str(self.root / "other.lua"),
            "server": "unix:///tmp/other.sock",
            "real_server": True,
        }
        for key, value in different.items():
            self.receipt_path.write_bytes(life._json(receipt | {key: value}))
            before = self.snapshot()
            for operation in (life.apply_setup, life.disable):
                with (
                    self.subTest(key=key, operation=operation.__name__),
                    self.assertRaises(RuntimeError),
                ):
                    operation(updated)
                self.assertEqual(self.snapshot(), before)

    def test_unknown_or_legacy_receipt_is_refused_without_changes(self) -> None:
        self.workspace.mkdir()
        for value in (
            {"version": 1, "enabled": True},
            {"version": 2},
            {"version": 999},
            {},
        ):
            self.receipt_path.write_bytes(life._json(value))
            before = self.snapshot()
            for operation in (
                life.inspect_setup,
                life.apply_setup,
                life.start,
                life.status,
                life.disable,
            ):
                with (
                    self.subTest(value=value, operation=operation.__name__),
                    self.assertRaises(RuntimeError),
                ):
                    operation(self.setup)
                self.assertEqual(self.snapshot(), before)

    def test_redirected_deployment_directories_are_refused_before_writes(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        for directory in (
            self.setup.data_root,
            self.setup.data_root / "plugins",
            self.setup.data_root / "plugins/files",
        ):
            directory.parent.mkdir(parents=True, exist_ok=True)
            directory.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                life.apply_setup(self.setup)
            self.assertEqual(self.target.read_bytes(), self.original)
            self.assertFalse(self.workspace.exists())
            self.assertEqual(list(outside.iterdir()), [])
            directory.unlink()

    def test_unused_git_deployment_does_not_block_files_lifecycle(self) -> None:
        """Files stays removable when an unused Git deployment is redirected."""
        outside = self.root / "unrelated-git-data"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_bytes(b"unrelated")
        git = self.setup.data_root / "plugins/git"
        git.parent.mkdir(parents=True)
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                self.target.write_bytes(self.original)
                if kind == "file":
                    git.write_bytes(b"unrelated")
                else:
                    git.symlink_to(outside, target_is_directory=True)
                life.apply_setup(self.setup)
                self.assertEqual(life.start(self.setup)["state"], "ready")
                self.assertEqual(life.status(self.setup)["state"], "ready")
                # A personal append is not a sibling loader or its dependency.
                tail = b"-- personal append\n"
                self.target.write_bytes(self.target.read_bytes() + tail)
                self.assertEqual(life.disable(self.setup)["state"], "disabled")
                self.assertEqual(self.target.read_bytes(), self.original + b"\n" + tail)
                self.assertEqual(marker.read_bytes(), b"unrelated")
                if kind == "file":
                    self.assertEqual(git.read_bytes(), b"unrelated")
                else:
                    self.assertEqual(git.readlink(), outside)
                git.unlink()

    def test_loader_free_files_ignores_unused_git_receipt(self) -> None:
        """A foreign Git receipt cannot block a loader-free Files installation."""
        git_receipt = self.workspace / "git/setup.json"
        git_receipt.parent.mkdir(parents=True)
        git_receipt.write_bytes(b"not JSON")
        self.assertEqual(life.inspect_setup(self.setup)["state"], "inspected")
        life.apply_setup(self.setup)
        self.assertEqual(life.start(self.setup)["state"], "ready")
        self.assertEqual(life.disable(self.setup)["state"], "disabled")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(git_receipt.read_bytes(), b"not JSON")

    def test_loader_free_files_ignores_redirected_git_workspace(self) -> None:
        """Unused sibling workspace redirection is neither read nor adopted."""
        outside = self.root / "foreign-workspace"
        outside.mkdir()
        marker = outside / "setup.json"
        marker.write_bytes(b"foreign receipt")
        self.workspace.mkdir()
        git_workspace = self.workspace / "git"
        git_workspace.symlink_to(outside, target_is_directory=True)
        self.assertEqual(life.inspect_setup(self.setup)["state"], "inspected")
        life.apply_setup(self.setup)
        self.assertEqual(life.start(self.setup)["state"], "ready")
        self.assertEqual(life.disable(self.setup)["state"], "disabled")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(git_workspace.readlink(), outside)
        self.assertEqual(marker.read_bytes(), b"foreign receipt")

    def test_changed_data_root_is_not_receipt_ownership(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        other = replace(self.setup, data_root=self.root / "other-data")
        for operation in (life.apply_setup, life.start, life.disable, life.status):
            with self.assertRaises(RuntimeError):
                operation(other)
            self.assertEqual(self.snapshot(), before)

    def test_foreign_artifacts_and_symlinked_module_are_not_adopted(self) -> None:
        self.module.parent.mkdir(parents=True)
        for path in (self.module, self.root / "rexdeck_palette.lua"):
            path.write_bytes(b"foreign")
            with self.assertRaises(RuntimeError):
                life.apply_setup(self.setup)
            self.assertEqual(path.read_bytes(), b"foreign")
            self.assertEqual(self.target.read_bytes(), self.original)
            path.unlink()
        self.module.symlink_to(self.target)
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertTrue(self.module.is_symlink())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_old_hub_workspace_refused_without_touching_its_state(self) -> None:
        state = self.workspace / "hub-state"
        state.mkdir(parents=True)
        marker = state / "hub.db"
        marker.write_bytes(b"old owned database")
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertEqual(marker.read_bytes(), b"old owned database")
        self.assertFalse(self.receipt_path.exists())

    def test_symlink_retarget_during_apply_rolls_back_module(self) -> None:
        other = self.root / "other.lua"
        other.write_bytes(b"-- another target")
        replace = life._replace

        def retarget(
            path: Path,
            before: life._File,
            after: bytes | None,
            *,
            mode: int | None = None,
        ) -> None:
            replace(path, before, after, mode=mode)
            if path == self.module and after is not None:
                self.config.unlink()
                self.config.symlink_to(other)

        with patch.object(life, "_replace", side_effect=retarget):
            with self.assertRaises(RuntimeError):
                life.apply_setup(self.setup)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(other.read_bytes(), b"-- another target")
        self.assertFalse(self.module.exists())

    def test_shared_data_root_serializes_different_workspaces(self) -> None:
        """The common deployment-root lock rejects another workspace's mutation."""
        other_dir = self.root / "other-config"
        other_dir.mkdir()
        other_config = other_dir / "other.lua"
        other_config.write_bytes(b"-- other target\n")
        other = replace(
            self.setup, workspace=self.root / "other-workspace", config=other_config
        )
        tools = life._tools()
        attempted = False

        def install_concurrently(_tool: str) -> dict[str, str]:
            """Attempt a sibling mutation while the first workspace owns the lock."""
            nonlocal attempted
            if not attempted:
                attempted = True
                with self.assertRaises(RuntimeError):
                    life.apply_setup(other)
            return tools

        with patch.object(life, "_tools", side_effect=install_concurrently):
            life.apply_setup(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "disabled")
        self.assertFalse((other.workspace / "setup.json").exists())
        self.assertEqual(other_config.read_bytes(), b"-- other target\n")
        with self.assertRaisesRegex(RuntimeError, "Unowned existing artifact"):
            life.apply_setup(other)

    def test_newly_appeared_module_is_not_adopted(self) -> None:
        tools = life._tools()
        foreign = b"-- concurrently installed foreign module\n"

        def install_foreign_module(_tool: str) -> dict[str, str]:
            self.module.parent.mkdir(parents=True, exist_ok=True)
            self.module.write_bytes(foreign)
            return tools

        with patch.object(life, "_tools", side_effect=install_foreign_module):
            with self.assertRaisesRegex(RuntimeError, "Concurrent edit"):
                life.apply_setup(self.setup)
        self.assertEqual(self.module.read_bytes(), foreign)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.receipt_path.exists())

    def test_concurrent_lifecycle_lock_refuses_mutation(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        with (self.workspace / "lifecycle.lock").open("r+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):
                life.start(self.setup)
        self.assertEqual(self.snapshot(), before)

    def test_missing_yazi_does_not_install_or_apply(self) -> None:
        with patch.object(
            life,
            "_tools",
            side_effect=RuntimeError("Required executable not found: yazi"),
        ):
            with self.assertRaises(RuntimeError):
                life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), (self.original, None, None))

    def test_start_refuses_foreign_action_appearing_while_stopped(self) -> None:
        life.apply_setup(self.setup)
        before = self.snapshot()
        self.actions = [{"name": "rexdeck_files", "kind": "remote"}]
        with self.assertRaises(RuntimeError):
            life.start(self.setup)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actions[0]["kind"], "remote")

    def test_config_separator_round_trip_for_empty_and_terminated_files(self) -> None:
        for original in (b"", b"-- terminated\n"):
            with self.subTest(original=original):
                self.target.write_bytes(original)
                life.apply_setup(self.setup)
                life.start(self.setup)
                life.disable(self.setup)
                self.assertEqual(self.target.read_bytes(), original)

    def test_invalid_canonical_lua_fails_without_leaking_loader_cache(self) -> None:
        (self.project / "host/main.lua").write_bytes(b"return function( invalid\n")
        with self.assertRaises(RuntimeError):
            life.apply_setup(self.setup)
        self.assertEqual(self.snapshot(), (self.original, None, None))

    def test_generated_options_are_lua_quoted_not_shell_or_json_literals(self) -> None:
        # Execute a consumer that validates all tricky option bytes at its API.
        source = self.project / "host/main.lua"
        source.write_text(
            "return function(opts)\n"
            'assert(opts.python == "/a python/\\195\\169")\n'
            'assert(opts.rex == "/a \\"rex\\"")\n'
            'rex.action {name="rexdeck_files",title="RexDeck Files",category="RexDeck",run=function() end}\n'
            "end\n"
        )
        life.apply_setup(self.setup)
        life.start(self.setup)
        self.assertEqual(life.status(self.setup)["state"], "ready")


class ProcessBoundaryTests(unittest.TestCase):
    def test_deferred_sigint_reaches_previous_handler_after_handoff(self) -> None:
        events: list[str] = []

        def interrupt(_signum: int, _frame: object) -> None:
            events.append("delivered")
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGINT, interrupt)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        with self.assertRaises(KeyboardInterrupt):
            with life._defer_cancellation():
                os.kill(os.getpid(), signal.SIGINT)
                events.append("owned")
        self.assertEqual(events, ["owned", "delivered"])

    def test_helper_cancellation_kills_only_exact_child(self) -> None:
        child = Mock()
        child.poll.return_value = None

        def interrupted_popen(*_args: object, **_kwargs: object) -> Mock:
            os.kill(os.getpid(), signal.SIGINT)
            return child

        with patch.object(subprocess, "Popen", side_effect=interrupted_popen):
            with self.assertRaises(KeyboardInterrupt):
                life._run([sys.executable, "-c", "pass"], {})
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=2)
        child.stdout.close.assert_called_once_with()

    def test_rex_environment_removes_ambient_targets(self) -> None:
        setup = life.Setup(
            Path("/project"),
            Path("/workspace"),
            Path("/selected.lua"),
            Path("/data/rexdeck"),
            "unix:///tmp/private.sock",
        )
        with patch.dict(
            os.environ,
            {
                "REX_SERVER": "foreign",
                "REX_SESSION": "foreign",
                "REX_BLOCK": "foreign",
                "REX_CLIENT": "foreign",
            },
        ):
            env = life._env(setup)
        self.assertTrue(
            {"REX_SERVER", "REX_SESSION", "REX_BLOCK", "REX_CLIENT"}.isdisjoint(env)
        )
        self.assertEqual(env["REX_CONFIG"], "/selected.lua")

    def test_env_script_tools_bind_the_interpreter_found_on_enabling_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            tools, runtime = root / "tools", root / "runtime"
            tools.mkdir()
            runtime.mkdir()

            def executable(path: Path, body: str) -> None:
                path.write_text(body)
                path.chmod(0o755)

            executable(tools / "rex", "#!/bin/sh\n")
            executable(runtime / "node", "#!/bin/sh\n")
            executable(tools / "npm-tool", "#!/usr/bin/env node\nrequire('x')\n")
            executable(tools / "split-tool", "#!/usr/bin/env -S node --flag\n")
            executable(tools / "sh-tool", "#!/bin/sh\n")
            executable(tools / "bad-tool", "#!/usr/bin/env -i node\n")
            path = os.pathsep.join((str(tools), str(runtime), "relative/bin"))
            with patch.dict(os.environ, {"PATH": path}):
                for name in ("npm-tool", "split-tool"):
                    self.assertEqual(
                        life._tools(name)["interpreter"], str(runtime / "node")
                    )
                self.assertNotIn("interpreter", life._tools("sh-tool"))
                with self.assertRaisesRegex(RuntimeError, "Unsupported interpreter"):
                    life._tools("bad-tool")
                (runtime / "node").unlink()
                with self.assertRaises(life.MissingDependency):
                    life._tools("npm-tool")


if __name__ == "__main__":
    unittest.main()

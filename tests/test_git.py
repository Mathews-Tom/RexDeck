"""Consumer-visible Git context and independently owned sibling lifecycles."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from rexdeck import lifecycle as life
from rexdeck.plugins.files import launch

LAUNCHER = Path(launch.__file__)

# Only the external Rex boundary is substituted. Lua executes both packaged
# integrations and the real selected config; receipts never manufacture actions.
_LUA_CONFIG = r"""
local actions = {}
rex = {
  action = function(action)
    assert(type(action.run) == "function", "action has no callable run")
    local info = debug.getinfo(2, "Sl")
    actions[#actions + 1] = {
      action.name, "lua", action.title, action.category,
      info.source:gsub("^@", "") .. ":" .. info.currentline,
    }
  end,
}
local previous_path = package.path
local cached_files, cached_git = {}, {}
package.loaded.rexdeck_files = cached_files
package.loaded.rexdeck_git = cached_git
local ok, err = pcall(dofile, arg[1])
assert(package.path == previous_path, "loader leaked module search path")
assert(package.loaded.rexdeck_files == cached_files, "Files cache changed")
assert(package.loaded.rexdeck_git == cached_git, "Git cache changed")
if not ok then io.stderr:write(tostring(err)); os.exit(1) end
local function hex(value)
  assert(type(value) == "string", "action metadata is not a string")
  return (value:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end
for _, action in ipairs(actions) do
  local row = {}
  for _, value in ipairs(action) do row[#row + 1] = hex(value) end
  print(table.concat(row, "\t"))
end
"""


class NativeConfig:
    def __init__(self, root: Path, lua: str) -> None:
        self.root = root
        self.lua = lua
        self.target = root / 'selected "雪 config.lua'
        self.target.write_bytes(b"-- personal config without final newline")
        self.target.chmod(0o640)
        self.config = root / "init.lua"
        self.config.symlink_to(self.target.name)
        self.harness = root / "config-harness.lua"
        self.harness.write_text(_LUA_CONFIG, encoding="utf-8")
        self.files = life.Setup(
            project=Path(life.__file__).resolve().parent,
            workspace=root / "workspace",
            config=self.config,
            data_root=root / "data/rexdeck",
            server="unix:///tmp/rexdeck-git-isolated-test.sock",
        )
        self.git = replace(self.files, plugin="git")
        self.actions: list[dict[str, object]] = []
        self.fail_reload = False

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
        if args == ("actions", "--json"):
            return {"actions": self.actions}
        if args == ("config", "check", "--json"):
            try:
                self.evaluate()
            except RuntimeError as error:
                return {"diagnostics": [{"message": str(error)}]}
            return {"diagnostics": []}
        if args == ("config", "reload", "--json"):
            self.actions = self.evaluate()
            if self.fail_reload:
                self.fail_reload = False
                return {"diagnostics": [{"message": "injected reload failure"}]}
            return {"diagnostics": []}
        raise AssertionError(f"Unexpected Rex request: {args}")

    def owned_paths(self) -> tuple[Path, ...]:
        return (
            self.target,
            self.files.data_root / "plugins/files/rexdeck_files.lua",
            self.files.data_root / "plugins/git/rexdeck_git.lua",
            self.files.workspace / "setup.json",
            self.files.workspace / "git/setup.json",
        )

    def snapshot(self) -> dict[Path, tuple[bytes, int] | None]:
        return {
            path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            if path.exists()
            else None
            for path in self.owned_paths()
        }

    def enable(self, setup: life.Setup) -> None:
        life.apply_setup(setup)
        assert life.start(setup)["state"] == "ready"
        assert life.status(setup)["state"] == "ready"

    def assert_config_identity(self, original: bytes) -> None:
        assert self.target.read_bytes() == original
        assert stat.S_IMODE(self.target.stat().st_mode) == 0o640
        assert self.config.is_symlink()
        assert self.config.readlink() == Path(self.target.name)


@pytest.fixture
def native_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NativeConfig:
    lua = shutil.which("lua") or shutil.which("luajit")
    if lua is None:
        pytest.skip("Lua is required to exercise sibling native config loading")
    native = NativeConfig(tmp_path, lua)
    monkeypatch.setattr(life, "_endpoint_policy", lambda _setup: None)
    monkeypatch.setattr(life, "_rex", native.rex)
    monkeypatch.setattr(life, "_executable", lambda name: sys.executable)
    return native


@pytest.mark.parametrize("enable_first", ["files", "git"])
@pytest.mark.parametrize("disable_first", ["files", "git"])
@pytest.mark.parametrize("original", [b"", b"-- terminated\n", b"-- no final newline"])
def test_sibling_disable_orders_restore_exact_config_and_keep_other_ready(
    native_config: NativeConfig,
    enable_first: str,
    disable_first: str,
    original: bytes,
) -> None:
    native = native_config
    native.target.write_bytes(original)
    setups = {"files": native.files, "git": native.git}
    for plugin in (enable_first, "git" if enable_first == "files" else "files"):
        native.enable(setups[plugin])
    assert {action["name"] for action in native.actions} == {
        "rexdeck_files",
        "rexdeck_git",
    }
    sibling = "git" if disable_first == "files" else "files"
    sibling_module = native.files.data_root / f"plugins/{sibling}/rexdeck_{sibling}.lua"
    module_before = sibling_module.read_bytes()
    assert life.disable(setups[disable_first])["state"] == "disabled"
    assert life.status(setups[sibling])["state"] == "ready"
    assert sibling_module.read_bytes() == module_before
    assert native.evaluate() == native.actions
    assert [action["name"] for action in native.actions] == [f"rexdeck_{sibling}"]
    assert native.config.is_symlink()
    assert native.config.readlink() == Path(native.target.name)
    assert stat.S_IMODE(native.target.stat().st_mode) == 0o640
    assert life.disable(setups[sibling])["state"] == "disabled"
    native.assert_config_identity(original)
    assert native.actions == []
    assert native.evaluate() == []
    assert not sibling_module.exists()
    # An old receipt must not reclaim the separator or touch its former sibling.
    for setup in setups.values():
        assert life.disable(setup)["state"] == "disabled"
    native.assert_config_identity(original)


@pytest.mark.parametrize("first_plugin", ["files", "git"])
@pytest.mark.parametrize("operation", ["apply", "start", "disable"])
def test_failed_sibling_mutation_rolls_back_without_disturbing_ready_integration(
    native_config: NativeConfig, first_plugin: str, operation: str
) -> None:
    native = native_config
    first, second = (
        (native.files, native.git)
        if first_plugin == "files"
        else (native.git, native.files)
    )
    native.enable(first)
    if operation != "apply":
        life.apply_setup(second)
    if operation == "disable":
        life.start(second)
    before = native.snapshot()
    registered = native.actions.copy()
    native.fail_reload = True
    mutate = {"apply": life.apply_setup, "start": life.start, "disable": life.disable}
    with pytest.raises(RuntimeError, match="injected reload failure"):
        mutate[operation](second)
    assert native.snapshot() == before
    assert native.actions == registered
    assert native.evaluate() == registered
    assert life.status(first)["state"] == "ready"
    assert native.config.is_symlink()
    assert native.config.readlink() == Path(native.target.name)


@pytest.mark.parametrize("first_plugin", ["files", "git"])
def test_failed_first_disable_restores_sibling_separator_ownership(
    native_config: NativeConfig, first_plugin: str
) -> None:
    native = native_config
    original = native.target.read_bytes()
    first, second = (
        (native.files, native.git)
        if first_plugin == "files"
        else (native.git, native.files)
    )
    native.enable(first)
    native.enable(second)
    before = native.snapshot()
    registered = native.actions.copy()
    native.fail_reload = True
    with pytest.raises(RuntimeError, match="injected reload failure"):
        life.disable(first)
    assert native.snapshot() == before
    assert native.actions == registered
    for setup in (first, second):
        assert life.status(setup)["state"] == "ready"
    assert life.disable(first)["state"] == "disabled"
    assert life.status(second)["state"] == "ready"
    assert life.disable(second)["state"] == "disabled"
    native.assert_config_identity(original)


@pytest.mark.parametrize("first_plugin", ["files", "git"])
def test_shared_config_lock_rejects_sibling_using_different_data_root(
    native_config: NativeConfig,
    monkeypatch: pytest.MonkeyPatch,
    first_plugin: str,
) -> None:
    native = native_config
    first, second = (
        (native.files, native.git)
        if first_plugin == "files"
        else (native.git, native.files)
    )
    # Different selected spelling, workspace and data root still share a target.
    other = replace(
        second,
        config=native.target,
        workspace=native.root / "other-workspace",
        data_root=native.root / "other-data",
    )
    tools = life._tools
    attempted = False

    def tools_with_overlap(tool: str = "yazi") -> dict[str, str]:
        nonlocal attempted
        if not attempted:
            attempted = True
            before = native.snapshot()
            with pytest.raises(RuntimeError, match="(?i)lock|operation|using"):
                life.apply_setup(other)
            assert native.snapshot() == before
        return tools(tool)

    monkeypatch.setattr(life, "_tools", tools_with_overlap)
    native.enable(first)
    assert attempted
    assert life.status(first)["state"] == "ready"
    assert not (other.workspace / "setup.json").exists()
    assert not (other.workspace / "git/setup.json").exists()
    assert not (other.data_root / "plugins").exists()


def test_missing_lazygit_never_registers_git_or_changes_files(
    native_config: NativeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = native_config
    native.enable(native.files)
    before = native.snapshot()
    registered = native.actions.copy()

    def missing_executable(name: str) -> str:
        if name == "lazygit":
            raise RuntimeError("Required executable not found: lazygit")
        return sys.executable

    # Exercise actual dependency resolution, not a mocked apply/start result.
    monkeypatch.setattr(life, "_executable", missing_executable)
    with pytest.raises(RuntimeError, match="lazygit"):
        life.apply_setup(native.git)
    assert native.snapshot() == before
    assert native.actions == registered
    assert native.evaluate() == registered
    assert life.status(native.files)["state"] == "ready"


@pytest.fixture
def git_environment(tmp_path: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        HOME=str(tmp_path),
    )
    return environment


@pytest.fixture
def repository_contexts(
    tmp_path: Path, git_environment: dict[str, str]
) -> tuple[Path, Path]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required to exercise repository/worktree discovery")
    repository = tmp_path / "repository 雪 space"
    repository.mkdir()

    def git_command(*args: str) -> None:
        subprocess.run(
            [git, "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=repository,
            env=git_environment,
            capture_output=True,
            check=True,
            timeout=10,
        )

    git_command("init", "--quiet")
    git_command(
        "-c",
        "user.name=RexDeck disposable test",
        "-c",
        "user.email=rexdeck-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "Private worktree fixture",
    )
    worktree = tmp_path / "linked worktree 雪"
    git_command("worktree", "add", "--quiet", "-b", "private-worktree", str(worktree))
    ordinary = repository / "nested directory"
    linked = worktree / "nested directory"
    ordinary.mkdir()
    linked.mkdir()
    assert (worktree / ".git").is_file()
    return ordinary, linked


@pytest.mark.parametrize("context_index", [0, 1], ids=["repository", "linked-worktree"])
@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-uri"])
def test_git_cwd_preserves_exact_invoking_subdirectory(
    repository_contexts: tuple[Path, Path],
    git_environment: dict[str, str],
    context_index: int,
    as_uri: bool,
) -> None:
    cwd = repository_contexts[context_index]
    result = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER),
            "git-cwd",
            cwd.as_uri() if as_uri else str(cwd),
        ],
        env=git_environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cwd": str(cwd.resolve())}


@pytest.fixture
def git_consumers(tmp_path: Path) -> tuple[Path, Path, Path]:
    tool_state = tmp_path / "tool-invoked"
    tool = tmp_path / "lazygit test consumer"
    tool.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(tool_state)!r}).touch()\n",
        encoding="utf-8",
    )
    rex = tmp_path / "rex test consumer"
    rex.write_text(
        f"#!{sys.executable}\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    for executable in (tool, rex):
        executable.chmod(0o700)
    return tool, rex, tool_state


@pytest.mark.parametrize("entrypoint", ["git-cwd", "git-run"])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GIT_DIR", "/unrelated/.git"),
        ("GIT_WORK_TREE", "/unrelated"),
        ("GIT_COMMON_DIR", "/unrelated/.git"),
        ("GIT_INDEX_FILE", "/unrelated/index"),
        ("GIT_OBJECT_DIRECTORY", "/unrelated/objects"),
        ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "/unrelated/objects"),
        ("GIT_NAMESPACE", "other"),
        ("GIT_CONFIG", "/unrelated/config"),
        ("GIT_CONFIG_COUNT", "1"),
        ("GIT_CONFIG_PARAMETERS", "'core.bare=true'"),
        ("GIT_CEILING_DIRECTORIES", "/"),
        ("GIT_DISCOVERY_ACROSS_FILESYSTEM", "1"),
    ],
)
def test_git_entrypoints_reject_inherited_repository_and_discovery_overrides(
    tmp_path: Path,
    git_environment: dict[str, str],
    git_consumers: tuple[Path, Path, Path],
    entrypoint: str,
    name: str,
    value: str,
) -> None:
    tool, rex, tool_state = git_consumers
    arguments = [str(tmp_path)]
    if entrypoint == "git-run":
        arguments = [
            str(tool),
            str(rex),
            "unix:///private captured.sock",
            "captured-session",
            "captured-origin",
            str(tmp_path),
        ]
    environment = git_environment | {name: value}
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), entrypoint, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert name in result.stderr
    assert "Traceback" not in result.stderr
    assert not tool_state.exists()

# RexDeck

A collection of independently selectable plugins and extensions that bring existing tools into your own Rex application.

Files and local Git bring unmodified Yazi and lazygit into native Rex terminals. GitHub workflows, rich viewers, agent/subagent views, and task-provider integrations remain planned. Select the tools you need without installing an unrelated default suite.

## Plugins and extensions

| Integration | Purpose | Status |
| --- | --- | --- |
| [Files](plugins/files/README.md) | Browse, preview, and manage files using unmodified Yazi in a native Rex terminal | Available |
| [Git](plugins/git/README.md) | Inspect, stage, and commit in the invoking repository/worktree using unmodified lazygit | Available |
| GitHub | Pull-request/issue workflows with explicit repository/host/account | Planned |
| Rich viewers | Rendered Markdown, PDF, and image viewing | Planned |
| Agents and subagents | Attention, observed state, and navigation to agent work | Planned |
| Tasks | Independently selectable Linear and ClickUp workflows | Planned |

Planned integrations are not implemented or installable yet. RexDeck is not published on PyPI; install the current source package as described below.

## Getting started

Install once from a RexDeck checkout with Python 3.12+ and `uv`:

```sh
uv tool install .
rexdeck setup
rexdeck enable files
rexdeck status
```

The installed command works from any directory; the package includes the shared native action and Python launcher for Files and Git. No shell wrapper, `RD_*` variables, or developer checkout is needed at runtime. Ensure the tool executable directory reported by `uv` is on `PATH`.

`setup` remembers the selected Rex configuration and running server without changing Rex configuration. Interactive setup asks for approval before saving a personal-server target. Use `enable files` or `enable git` independently, `status [files|git]` to inspect ownership/registration, and `disable <id>` to remove only that integration. Unqualified `status` reports both integrations, retaining healthy results alongside an integration-specific fault if its sibling cannot be inspected. See the plugin READMEs for prerequisites, capabilities, updates, and removal.

The uv-managed package remains the runtime source. Setup freezes the app data root from `$XDG_DATA_HOME/rexdeck` (default `~/.local/share/rexdeck`); each integration deploys `plugins/<id>/rexdeck_<id>.lua` there with an independently owned tracked loader in Rex's `init.lua`. Approved preferences remain in `$XDG_CONFIG_HOME/rexdeck/config.json`. Files receipt/lock paths remain in the saved `$XDG_STATE_HOME/rexdeck/files` workspace; Git uses its `git/` subdirectory. Later `XDG_DATA_HOME` changes do not retarget the saved installation. Run `disable` for every applied integration before uninstalling RexDeck or deliberately resetting a saved target, including integrations reported as disabled after a failed enable.

Each available integration owns its requirements and usage instructions. There is no collection-wide install-all command, and external tools are not auto-installed.

## Design principles

- **Reuse tools, do not rebuild them.** Preserve their normal interfaces, configuration, authentication, and capabilities.
- **Prefer native Rex integration.** Use native actions and panes where sufficient. LazyRex is optional only where a particular integration needs an existing capability; it is not a collection-wide dependency.
- **Keep features independently selectable.** Install only the dependencies needed by the chosen integration; no implicit default suite, hooks, or autostart.
- **Preserve user context and ownership.** Pass explicit launch context and structured arguments, keep unrelated work intact, and make removal reversible.
- **Disclose capabilities accurately.** External tools run as your OS account and can be write-capable; they are not sandboxed or automatically read-only.

## Development

Install LuaJIT before running the suite: native Lua behavior tests require `lua` or `luajit` and otherwise skip. On macOS:

```sh
brew install luajit
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
```

Plugin-specific syntax checks and real interaction verification belong in each plugin's README. Keep plugin documentation under `plugins/<name>/README.md` and packaged runtime assets under `src/rexdeck/plugins/<name>/`. Link available integrations from the table above. Do not create empty plugin folders for planned features.

## License

RexDeck is [MIT licensed](LICENSE). Integrated external tools retain their own licenses.

# RexDeck

A collection of independently selectable plugins and extensions that bring existing tools into your own Rex application.

RexDeck is more than a file explorer. Files is the first available integration; Git/GitHub workflows, rich viewers, agent/subagent views, and task-provider integrations are planned. Select the tools you need without installing an unrelated default suite.

## Plugins and extensions

| Integration | Purpose | Status |
| --- | --- | --- |
| [Files](plugins/files/README.md) | Browse, preview, and manage files using unmodified Yazi in a native Rex terminal | Available locally |
| Git and GitHub | Repository/worktree tools and pull-request/issue workflows | Planned |
| Rich viewers | Rendered Markdown, PDF, and image viewing | Planned |
| Agents and subagents | Attention, observed state, and navigation to agent work | Planned |
| Tasks | Independently selectable Linear and ClickUp workflows | Planned |

Planned integrations are not implemented or installable yet. Public distribution is also future work; the current Files integration runs from this checkout.

## Getting started

Choose an available integration above and follow the README in its folder. Each plugin owns its requirements, setup, configuration, usage, removal, and verification instructions. There is no collection-wide install-all command.

## Design principles

- **Reuse tools, do not rebuild them.** Preserve their normal interfaces, configuration, authentication, and capabilities.
- **Prefer native Rex integration.** Use native actions and panes where sufficient. LazyRex is optional only where a particular integration needs an existing capability; it is not a collection-wide dependency.
- **Keep features independently selectable.** Install only the dependencies needed by the chosen integration; no implicit default suite, hooks, or autostart.
- **Preserve user context and ownership.** Pass explicit launch context and structured arguments, keep unrelated work intact, and make removal reversible.
- **Disclose capabilities accurately.** External tools run as your OS account and can be write-capable; they are not sandboxed or automatically read-only.

## Development

Run from the repository root:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
```

Plugin-specific syntax checks and real interaction verification belong in each plugin's README. Keep new plugin documentation alongside its implementation under `plugins/<name>/README.md`, and link available integrations from the table above. Do not create empty plugin folders for planned features.

## License

RexDeck is [MIT licensed](LICENSE). Integrated external tools retain their own licenses.

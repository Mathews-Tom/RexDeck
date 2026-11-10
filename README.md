# RexDeck

![RexDeck: terminal tools and purpose-built views in Rex](https://raw.githubusercontent.com/Mathews-Tom/RexDeck/main/assets/rexdeck-banner.png)

**Your tools and data, at home in Rex.**

Discover and enable familiar terminal tools and purpose-built interfaces for CLI output and APIs—all in native Rex panes.

Keep the TUIs you already use, and add focused views over the commands and APIs behind your workflow. Each integration is independently selectable, with its own setup and controls. Open one from Rex, work in a native pane, and return to the exact terminal you came from when you quit. Installing RexDeck does not enable a suite of tools or replace your existing applications.

[![Illustrated RexDeck walkthrough: open a terminal tool or a purpose-built view in a native Rex pane, then return to your terminal](https://raw.githubusercontent.com/Mathews-Tom/RexDeck/main/assets/rexdeck-launch.gif)](https://github.com/Mathews-Tom/RexDeck/blob/main/assets/rexdeck-launch.mp4)

[Watch or download the MP4](https://github.com/Mathews-Tom/RexDeck/blob/main/assets/rexdeck-launch.mp4) · The banner and animation are generic illustrations, not screen recordings.

## Getting started

You need **Rex already running, Python 3.12+ and [uv](https://docs.astral.sh/uv/)**. The qualified platform is macOS arm64 with Rex API revision 10 as the baseline.

```sh
uv tool install --default-index https://pypi.org/simple rexdeck
rexdeck setup              # select your Rex configuration and running server
rexdeck plugins            # list every integration, its state and qualified tool versions
rexdeck enable <id>        # turn on only what you need
```

Install a hosted tool yourself before enabling it: RexDeck never installs Rex, Python, uv or upstream tools. Then open the integration from Rex's command palette as **RexDeck &lt;Title&gt;**, or run `rex do rexdeck_<id>` in a Rex terminal. Some integrations need arguments, such as `file=` for a selected file or `account=` for a GitHub account; each integration's guide lists them. Quit the tool or view to return focus to the invoking terminal.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Integrations](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/integrations.md) | Every available integration by category, with links to each one's guide |
| [Usage](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/usage.md) | Install, upgrade, `setup`, `plugins`, `enable`, `status`, `disable`, states and uninstalling |
| [Configuration](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/configuration.md) | Where RexDeck keeps its files, pane placement and size, and where each tool's own settings live |
| [Dependencies](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/dependencies.md) | Installing qualified tools yourself, Homebrew guidance and executable discovery |
| [Development](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/development.md) | Local checks, documentation layout, media and adding an integration |
| [Releasing](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/releasing.md) | The owner-gated release workflow |
| [Changelog](https://github.com/Mathews-Tom/RexDeck/blob/main/CHANGELOG.md) | Changes in each version |

## Design principles

- **Make tools and output useful in Rex.** Host established TUIs without rewriting their interfaces, and build focused views for supported CLI output and APIs.
- **Prefer native Rex integration.** Use native actions and panes where sufficient. LazyRex is optional, used only where a particular integration needs an existing capability; it is not a collection-wide dependency.
- **Keep features independently selectable.** Install only the dependencies needed by the chosen integration; there is no implicit default suite, no hooks and no autostart.
- **Preserve user context and ownership.** Pass explicit launch context and structured arguments, keep unrelated work intact, and make removal reversible.
- **Disclose capabilities accurately.** External tools run as your OS account and can be write-capable; they are not sandboxed or automatically read-only.

## Contributing

Issues and pull requests are welcome. See [Development](https://github.com/Mathews-Tom/RexDeck/blob/main/docs/development.md) for local checks and how to add an integration. For an environment the maintainer cannot qualify, include what you ran and what you observed.

## License

RexDeck is [MIT licensed](https://github.com/Mathews-Tom/RexDeck/blob/main/LICENSE), and the license is included in the wheel and sdist metadata. External executables are **not bundled**. Each keeps its upstream license and terms, named in its integration guide and in `rexdeck plugins` output. Rex is an externally installed, separately licensed application prerequisite; RexDeck's MIT license grants no rights to Rex and does not redistribute it.

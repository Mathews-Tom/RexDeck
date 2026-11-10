# Usage

[Back to RexDeck](../README.md) · [Integrations](integrations.md) · [Configuration](configuration.md)

## Install and upgrade

You need **Rex already running, Python 3.12+ and [uv](https://docs.astral.sh/uv/)**. The qualified platform is **macOS arm64 with Rex API revision 10 as the baseline**, not an exact-version gate. Other platforms and remote or multi-device operation are not yet qualified.

```sh
uv tool install --default-index https://pypi.org/simple rexdeck
```

Published releases are on [PyPI](https://pypi.org/project/rexdeck/). For changes merged after a release, pin an exact public source commit rather than a moving branch:

```sh
uv tool install "rexdeck @ git+https://github.com/Mathews-Tom/RexDeck@<full-commit-sha>"
```

The installed command works from any directory. The package includes the shared native action and Python launcher for every integration; no shell wrapper, `RD_*` variables or developer checkout is needed. Make sure the tool executable directory reported by `uv` is on `PATH`.

Upgrade a PyPI installation with `uv tool upgrade rexdeck`. A Git-pinned installation keeps its source constraint, so rerun `uv tool install` with the new commit instead.

Installing or upgrading replaces only the RexDeck package. If an upgrade changes the bundled host, `plugins` and `status` report previously enabled integrations as `outdated`, with one combined command such as `rexdeck enable files git` that refreshes all of them. A missing selected tool reports `missing-dependency` instead: restore that tool first, then rerun `rexdeck enable <id>`. Integrations you never enabled stay unselected.

## Set up once

```sh
rexdeck setup
```

`setup` remembers the selected Rex configuration and running server without changing Rex configuration. Interactive setup asks for approval before saving a personal-server target.

## Find, enable and open integrations

```sh
rexdeck plugins                 # every integration in this installation, with state
rexdeck enable <id> [<id> ...]  # validate preinstalled tools and register the native action
rexdeck status [<id>]           # inspect ownership and registration
rexdeck disable <id> [<id> ...] # remove only the named integrations
```

Then open an integration from Rex's command palette as **RexDeck &lt;Title&gt;**, or run its native action from a Rex terminal:

```sh
rex do rexdeck_<id>                      # integrations that use the invoking directory
rex do rexdeck_<id> file="path/to/file"  # integrations that need a selected file
```

Each guide in [Integrations](integrations.md) lists that integration's arguments. After a successful `enable`, a **Next steps** section shows the exact open command for integrations that need `file=` or `account=`, and any setup outside RexDeck the tool needs first, such as a GitHub login, a running Docker daemon or a ClickUp token. Opening an integration without a required argument fails with a message naming the `rex do rexdeck_<id> file=PATH` or `account=LOGIN` command. The pane opens split from the invoking terminal. Quitting the tool or view returns focus to that exact terminal.

With several names, `enable` and `disable` change each integration independently, in the order given. A failure is reported in that integration's row while the others still change, and the command exits nonzero if any failed. An unknown name refuses the whole command before anything changes. `--placement` and `--size` apply to every named integration; put them before or after the list of names, not between names. See [Pane placement and size](configuration.md#pane-placement-and-size).

At a terminal, `enable`, `disable`, `status` and `plugins` print a table; add `--json` (or pipe the output) for JSON. A single name keeps the single-integration JSON document. Unqualified `status` reports every integration, keeping healthy results alongside an integration-specific fault if a sibling cannot be inspected.

### `rexdeck plugins`

`plugins` prints a table (plugin, state, tool and qualified versions, pane layout) followed by a **Needs attention** section. That section states each problem once, even when several plugins share it, followed by a single `Fix:` command for all of them when RexDeck knows it. Configured JSON rows include `enabled: true` or `enabled: false`; `outdated` rows also carry that `fix` command. Before setup, rows report `not-configured` rather than guessing that a target is disabled. Configured rows also report the saved pane `layout`. Every row's `integration` field discloses:

- the hosted tool and its qualified versions
- the upstream license
- the launch context
- write, network and process capabilities
- the default layout

Inspection faults stay visible alongside healthy sibling rows and return a nonzero exit status. Listing never enables integrations or installs dependencies, and planned integrations are not included. Purpose-built RexDeck views carry a `[RexDeck UI]` badge in the tool label.

### States

| State | Meaning |
| --- | --- |
| `disabled` | Not selected, or turned off |
| `ready` | Its native action is registered in the running Rex. This says nothing about whether the tool is currently open |
| `missing-dependency` | An applied integration's tool is not on the current `PATH`; `enabled` shows whether it is turned on. Make the tool available (reinstall it yourself if it was removed), then rerun `status`, or `rexdeck enable <id>` if its path changed |
| `conflict` | The Rex configuration, the RexDeck loader or the registered action no longer matches RexDeck's ownership record (for example, an unowned or edited loader). The mismatches are listed in `conflicts`. Changed or unowned artifacts are kept for your judgment; unchanged owned state may still be removed |
| `outdated` | Enabled before a RexDeck upgrade changed the bundled host, or before a tool moved on `PATH`. Run the `fix` command (`rexdeck enable <id>`) to refresh it |
| `fault` | An ownership or registration problem, with an `error` |
| `not-configured` | Shown by `plugins` before setup |
| `failed` | A command-level error, such as no saved target or an unknown or old receipt for `status <id>` |

`missing-dependency`, `conflict`, `outdated`, `fault` and `failed` exit nonzero. For `plugins` and unqualified `status`, the overall state is `fault` if any row has a problem other than `outdated`, otherwise `outdated` if any row is outdated.

## Attention

`attention` is a built-in CLI command, not a pane plugin, so it needs setup but no `enable`:

```sh
rexdeck attention --help
rexdeck attention list
```

See the [attention guide](../plugins/attention/README.md) for observe, list, jump, optional provider hooks and recovery.

## Uninstall or reset

Run `rexdeck disable <id>` for every applied integration before uninstalling RexDeck or deliberately resetting a saved target. That includes integrations reported as disabled after a failed enable. Then uninstall the package with `uv tool uninstall rexdeck`. `disable` and package uninstall leave upstream tools and their configuration installed. To point RexDeck at a different Rex target instead, see [Retarget a saved setup](configuration.md#retarget-a-saved-setup).

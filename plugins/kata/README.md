# Kata

Browse and triage the issues of the [kata](https://github.com/kenn-io/kata) project bound to the invoking directory in a native Rex pane with unmodified [kata 0.23.0](https://github.com/kenn-io/kata/releases/tag/v0.23.0) `kata tui`. Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and [RexDeck setup](../../README.md#getting-started).
- Owner-installed **kata 0.23.0** on a trusted absolute `PATH` entry, from the [qualified upstream release](https://github.com/kenn-io/kata/releases/tag/v0.23.0) or Homebrew (`brew install kata`, qualified build `distribution: homebrew`). Check `kata --version`; the first line is `kata v0.23.0`. RexDeck never installs, upgrades or removes kata; missing and other versions are refused with manual installation guidance before registration.
- A kata project bound to the directory you open it from: run `kata init` there once. kata starts its own local daemon on demand unless its configuration sets `autostart = false`; then run `kata daemon start` first. The pane runs with the **Rex server's environment**, so `KATA_HOME`, `KATA_SERVER` and similar variables come from there, not from your shell.

## Enable and open

```sh
rexdeck setup   # once, if not already done
rexdeck enable kata
rexdeck status kata
```

Open **RexDeck Kata** from Rex's command palette, or `rex do rexdeck_kata` in a Rex terminal inside the kata-bound directory.

Kata is independently selectable. Default placement is right, 50%; the shared `--placement` and `--size` options apply. `kata tui` needs a pane at least 80 columns wide.

## Use

RexDeck launches `kata tui` with no other arguments, using the invoking terminal's directory as cwd. kata walks up from there to the nearest `.kata.toml` (up to the git root) and opens that project's issue list.

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move through issues |
| `space` | Expand or collapse a parent's children |
| `Enter` | Open the selected issue's detail |
| `Esc` | Return from a detail |
| `1`–`4` | Switch between the Kata, Jobs, Runs and Workflows tabs |
| `?` | Help |
| `q`, then `y` | Quit (kata asks for confirmation) and restore invoking-terminal focus |

Controls exercised in the private Rex qualification lab, against a disposable project with one parent issue, one child and one comment:

- The pane opened on the bound project (`Project: proj`, `Daemon: local · v0.23.0`) with open, closed and total counts.
- `space` expanded the parent to show its child; `↓` then `Enter` opened the child's detail with its body, parent link, events and links.
- At **40 columns** kata replaced the view with "kata tui needs more space (>=80 columns)"; widening the pane to 80 columns redrew the list with the selection kept.
- `q` showed kata's "Quit kata?" confirmation; `y` quit and restored focus to the exact origin block.
- From a directory with no kata binding, kata opened its project picker (all projects on the daemon, plus "All projects") instead of an error; `q`, `y` still returned to the origin.

## Configuration

### Pane layout

Use `rexdeck enable kata --placement below --size 60` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80. Default: right, 50%. Pick a size that leaves the pane at least 80 columns wide.

### kata settings

kata reads its client configuration from `<KATA_HOME>/config.toml` (default `~/.kata/config.toml`) and the workspace's `.kata.toml` and `.kata.local.toml`. `[tui] confirm_quit = false` makes `q` quit without the confirmation. See kata's [configuration reference](https://github.com/kenn-io/kata/blob/v0.23.0/docs/reference/configuration.md).

RexDeck passes only the `tui` subcommand. It adds no project, daemon, actor or credential flags and never edits kata's configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed.

## Capabilities and limits

**This is not a read-only viewer.** `kata tui` creates, edits, comments on, labels, links, claims, closes, reopens, moves and deletes issues through kata's daemon as your kata actor. The Jobs and Workflows tabs create and edit cron jobs, agent-prompt jobs, workflows and notifications that kata's daemon or worker runs later. Workflow definitions open in `$VISUAL` or `$EDITOR` from the Rex server's environment.

kata keeps issues in its own database under `KATA_HOME`, or on a configured remote daemon. Running kata commands can auto-start kata's local daemon, which also serves kata's local web UI on a loopback port. RexDeck adds no storage of its own.

Qualification used an isolated lab HOME with its own `KATA_HOME` and kata daemon, and a private Rex endpoint. No issue writes were made from the pane; the lab daemon was stopped and its data removed afterwards.

## Disable

```sh
rexdeck disable kata
```

Only Kata's owned registration and module change. In qualification, an open Kata pane and an open System sibling pane both survived `rexdeck disable kata`, System stayed enabled, and `rex do rexdeck_kata` then reported no such action. kata, its daemon, its data and other plugins remain untouched.

kata is an unbundled, owner-managed [MIT-licensed upstream tool](https://github.com/kenn-io/kata/blob/v0.23.0/LICENSE).

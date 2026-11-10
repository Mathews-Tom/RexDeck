# Focus

Start a work session in a native Rex pane with unmodified [pomo 1.2.1](https://github.com/Bahaaio/pomo/releases/tag/v1.2.1), a Pomodoro work/break timer. Quitting returns focus to the exact terminal that invoked it.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server.
- Preinstalled pomo **1.2.1** on PATH. Install it yourself from the upstream release or `brew install --cask bahaaio/pomo/pomo` after checking that it selects 1.2.1. Verify with `pomo --version` (`pomo version 1.2.1`). RexDeck never installs, upgrades or removes pomo. A missing or different version makes `rexdeck enable focus` print actionable guidance and register nothing.

pomo loads config **even for `--version`**. The enable probe runs in your shell's current directory with the full environment: a local `pomo.yaml` with a wrong-typed value can make `pomo --version` exit 1, so enable refuses and prints install guidance even though the binary is installed. With `DEBUG` set to a nonempty value other than `0`, this probe also writes `debug.log` there. Run `rexdeck enable focus` from a neutral directory without `pomo.yaml`, with a valid `~/.config/pomo/pomo.yaml` if present.

## Enable and open

```sh
rexdeck enable focus
rexdeck status focus
# From a terminal inside Rex, in a directory you trust:
rex do rexdeck_focus
```

Focus runs `pomo` with **no arguments** in the invoking terminal's reported directory. It starts a work session using config defaults, not a fixed duration. The action offers no `file` selection or duration override; extra action arguments are ignored, not forwarded to pomo. Missing, remote or invalid directory context is refused before launch with a visible message.

Open **RexDeck Focus** from Rex’s command palette, or use the action above.

## Use

These are pomo's own keys, unchanged:

| Key | Action |
| --- | --- |
| Space | While timing, pause / resume |
| Up / `k` | While timing, add one minute |
| Left / `h` | While timing, reset to the initial duration |
| `s` | While timing, skip to the next session; at the completion prompt, start a 2-minute short session |
| `y` / `n` | At the completion prompt, start the next session / quit |
| Tab / `h` / `l` / Left / Right | At the completion prompt, switch the selected choice |
| Enter | At the completion prompt, submit the selected choice |
| `q` / Ctrl+C | Quit and return to the invoking terminal; a second `q` forces exit while waiting for commands |

Start the timer, pause when needed, and choose whether to begin the next session at completion.

## Configuration

### Pane layout

`rexdeck enable focus --placement right|left|below|above --size 20-80`; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### pomo settings

Edit YAML with `$EDITOR ./pomo.yaml` for a trusted directory-specific timer, or `$EDITOR ~/.config/pomo/pomo.yaml` for user defaults; see the [pinned config loader](https://github.com/Bahaaio/pomo/blob/v1.2.1/config/config.go). Duration, `onSessionEnd`, notification settings and completion commands are particularly relevant. RexDeck passes no tool arguments, config overlay or tool-specific environment overrides; the inherited environment is preserved with helper lookup restricted to trusted absolute PATH entries. RexDeck never edits upstream config.

**Only invoke Focus from directories you trust.** A `pomo.yaml` in the invoking directory is used **instead of** `~/.config/pomo/pomo.yaml`: the user config is not merged. Without local config, pomo uses the user config if present, or built-in defaults. RexDeck deliberately preserves this upstream behavior: it does not switch to a neutral directory or sandbox the config. Inspect repository config before launching.

The selected config's `work.then` and `break.then` lists execute programs with your user privileges when a session completes. They can modify files, run arbitrary programs or access the network. Lists are executable argv, not implicitly shell scripts, but can explicitly invoke a shell. Upstream applies one five-second timeout to the **whole list**, not each command. Starting the next session cancels commands **except with `onSessionEnd: start`**, when they continue in the background within that timeout. Quitting waits for running commands for up to that five-second timeout; a second `q` forces exit. Confirmation of the next session is **not** permission to run commands: completion actions already start before that prompt.

For a quiet short timer, put this in a trusted directory's `pomo.yaml` (no `then` commands):

```yaml
onSessionEnd: ask
asciiArt:
  enabled: false
work:
  duration: 10s
  notification:
    enabled: false
break:
  duration: 5s
  notification:
    enabled: false
longBreak:
  enabled: false
```

## Capabilities and limits

Qualification used the quiet 10-second work / 5-second break fixture above: Space paused and resumed, completion prompted for the break, `y` started it, and `q` returned to the exact origin. At 40 columns, pomo remains operable but its prompt border and control legend clip horizontally; widen the pane for the full text.

- Desktop notifications are enabled by default on session completion. On macOS, they run `osascript`, or `terminal-notifier` if it is on PATH. The `terminal-notifier` notification path writes temporary `.icns` files under `$TMPDIR`. Urgent notifications add a sound; a configured relative `icon` path is read from the current directory. Set **both** `work.notification.enabled` and `break.notification.enabled` to `false` to disable notifications. RexDeck does not change notification permissions.
- On every timer launch, even when no session is recorded, pomo creates **`~/.local/state/pomo`** and initializes the SQLite database schema in **`~/.local/state/pomo/pomo.db`** on macOS. It records sessions on completion, skip and timer quit, not just successfully finished work (sessions shorter than one second are ignored; short extensions update the in-memory summary). The path uses `HOME`, not `XDG_STATE_HOME`.
- pomo has no built-in network service; configured completion commands may use the network and inherit your privileges and environment.
- With `DEBUG` set to a nonempty value other than `0`, upstream writes `debug.log` in the current directory, **including during enable's `--version` probe**. It may contain config and completion-command arguments; do not put secrets in those values.
- The action starts the upstream timer, not the separate `pomo stats` interface. Upstream controls, display and session transitions remain unchanged.
- Config search, command execution, notification defaults and the database path are verified against [config/config.go](https://github.com/Bahaaio/pomo/blob/v1.2.1/config/config.go), [actions/actions.go](https://github.com/Bahaaio/pomo/blob/v1.2.1/actions/actions.go), [cmd/root.go](https://github.com/Bahaaio/pomo/blob/v1.2.1/cmd/root.go), [ui/handlers.go](https://github.com/Bahaaio/pomo/blob/v1.2.1/ui/handlers.go) and [db/connect.go](https://github.com/Bahaaio/pomo/blob/v1.2.1/db/connect.go).

## Disable

```sh
rexdeck disable focus
```

Only Focus's owned loader, module and receipt change. Other integrations and open pomo panes remain alive. pomo is MIT-licensed, unbundled and owner-managed. Upstream pomo and its configuration and history remain untouched.

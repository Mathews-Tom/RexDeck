# Processes

Run unmodified [dekit 0.10.3](https://github.com/pvolok/dekit/releases/tag/v0.10.3), the MIT-licensed mprocs successor, in a native Rex pane. Select a **trusted `dekit.yaml` explicitly**. Tasks run arbitrary commands with your permissions: this is not a sandbox. Quitting and acknowledging the runner report returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server.
- Owner-installed **dekit 0.10.3** on PATH. Check `brew info dekit` before using `brew install dekit`, or use the pinned upstream release. Verify `dekit --version` prints `dekit 0.10.3`. RexDeck never installs or upgrades tools. Missing and unqualified versions are refused with manual guidance and register nothing.

## Enable and open

```sh
rexdeck enable procs
rexdeck status procs
rexdeck plugins
# In the invoking Rex terminal:
rex do rexdeck_procs file="project/dekit.yaml"
```

Open **RexDeck Processes** from Rex’s command palette, or `rex do rexdeck_procs file="project/dekit.yaml"`.

Absolute paths, local file URIs and paths relative to that terminal's reported cwd are supported. No selection, a missing file, a directory, a wrong suffix, or a resolved basename other than exactly `dekit.yaml` is refused before the pane opens. A symlink is judged by its resolved target. Non-file integration contexts refuse a `file` argument rather than ignoring it.

The shared selected-file validator also refuses `*`, `?`, `[` and `\` anywhere in the resolved path. dekit itself does not glob; this integration deliberately retains that shared refusal.

Project/config and executable paths containing any non-printable character, including non-breaking space (NBSP), are refused so the displayed stop command cannot inject terminal controls or invisible formatting. Executable paths containing `=` are also refused because `env` would interpret them as assignments.

Upstream does **not** take a YAML filename as its positional argument (those are JavaScript scripts). RexDeck instead invokes the qualified binary with `-C` and the selected config's canonical parent directory. No upward project discovery or host-runner fallback is used. The selected config is checked again at launch. Keep the config and its directory under your control; this is a path-based launch, not protection against concurrent file replacement.

Project/user kernel overrides selecting a different executable and existing runners with a different binary/version are refused. Stop an old runner explicitly before relaunching; RexDeck never upgrades it. Existing runner tasks and saved task state can differ from the current config; review upstream's reload/write-back indications.

## Use

Select the trusted config, start or inspect tasks, then choose explicitly whether to detach or stop and acknowledge the runner report.

These are upstream defaults; your dekit user keymap can change them:

| Key | Action |
| --- | --- |
| `j` / `k`, Down / Up | Select a task |
| `s` | Start selected task |
| `r` / `R` | Restart / force-restart selected task |
| `x` / `X` | Stop / hard-kill selected task |
| `Ctrl-a` | Attach focus to task terminal / return to task list |
| `?` | Keymap help |
| `q` | Quit dialog: `d` detach, `x` stop without saving, `s` save and stop |

A project owns an independent **daemon runner**, not owned by RexDeck. On a new runner, `dekit` brings up saved/autostart tasks; attaching to an existing runner preserves its current task state. Detaching leaves tasks running even after the pane closes. Stopping a task may restart it if another task still depends on it; consult `dekit help targets`. A hard kill is different from a graceful stop.

The upstream UI remains operable at 40 columns, but its default sidebar leaves little room for task output; widen the pane for logs.

After the TUI exits, RexDeck performs bounded, explicitly project-scoped `runner status` and (only for a running runner) `ls` probes. It visibly shows task states or reports that the runner stopped, and prints the runner socket path when status publishes it (a stopped runner normally has no published socket). Probe failures say the remaining tasks are unknown, never falsely that they stopped. It always prints the exact shell-quoted command to stop that project, equivalent to:

```sh
dekit -C /absolute/project down
```

The printed command always starts with `env` and preserves both runtime variables' exact presence or absence. Set `XDG_RUNTIME_DIR` and `TMPDIR` values become shell-quoted assignments; unset variables become `-u XDG_RUNTIME_DIR` or `-u TMPDIR` options. Preserve the entire prefix when pasting into another terminal: it selects the same socket namespace even if that terminal exports extra routing variables.

Press Enter to acknowledge the report, then focus returns to the exact invoking terminal. RexDeck never silently kills tasks. `down` stops the runner and saves task/screens for the next `up`; `dekit runner stop -C /absolute/project` stops without saving. Closing/killing the pane itself can bypass visible reporting: use the explicit `down` command to check and stop tasks. Selecting a different runner through dekit's own UI does not change the report's scope: it always names the originally selected project.

In isolated 0.10.3 qualification, one detach after a narrow resize returned upstream's `Broken pipe` error even though tasks remained running. The launcher still reports those tasks and preserves the nonzero exit message before a single acknowledgement; an upstream error never means the tasks stopped.

## Configuration

### Pane layout

Use `rexdeck enable procs --placement right|left|below|above --size 20-80` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). Default: right, 60%.

### dekit settings

Edit the explicitly selected project YAML with `$EDITOR /absolute/project/dekit.yaml`; review task commands, dependencies, autostart, included JavaScript and hooks before opening it. The upstream console also edits tasks and can write them back. Edit user-wide UI/keymap YAML with `$EDITOR "${XDG_CONFIG_HOME:-$HOME/.config}/dekit/config.yaml"`. See the [upstream user configuration guide](https://dekit.run/docs/config/user) and [pinned project example](https://github.com/pvolok/dekit/blob/v0.10.3/README.md#quick-start).

RexDeck passes `-C /absolute/project` rather than the YAML filename, adds no config overlay and never redirects upstream homes. It removes `REX_SESSION`, `REX_BLOCK`, `REX_CLIENT` and all `DEKIT_*` variables for the TUI, version check and runner probes, while preserving upstream XDG/runtime settings.

RexDeck never edits upstream configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed so helpers cannot resolve from the working directory.



## Capabilities and limits

- Task commands, JavaScript config fragments/scripts and hooks execute arbitrary code. Tasks may read/write files, use the network, and inherit the pane environment. Console actions can edit/add/delete tasks; write-back changes `dekit.yaml`. Trust included files and saved task state too.
- User config/keymaps: `$XDG_CONFIG_HOME/dekit/config.yaml`, default `~/.config/dekit/config.yaml`. The conventional host config is `host/dekit.yaml` there. With dekit 0.10.3, explicitly selecting that file makes RexDeck pass `-C ~/.config/dekit/host`, which reports runner kind `project`, not the named host runner. Selecting that directory's config manages the tasks defined there.
- Persistent data: `$XDG_DATA_HOME/dekit`, default `~/.local/share/dekit`; saved task/screens are under `saved/`, and `default-kernel` selects a user-default kernel. Configured task log paths can write elsewhere.
- On macOS/Unix sockets, runner records, locks and runtime snapshots live under `$XDG_RUNTIME_DIR/dekit`; absent that variable, upstream uses the platform temporary directory's `dekit-<uid>` directory. Runner identities are project-root scoped.
- RexDeck preserves upstream homes/settings and does not create a daemon of its own. `dekit help config`, `dekit help start/runners` and the pinned upstream documentation describe the full execution and persistence model.
- The TUI, version check and runner probes omit `REX_SESSION`, `REX_BLOCK`, `REX_CLIENT` and every inherited `DEKIT_*` variable. New persistent tasks therefore inherit neither the pane's soon-stale identity nor an unrelated runner selection; explicit `-C` and the preserved upstream homes/runtime settings define this launch.

## Disable

```sh
rexdeck disable procs
```

Only Processes' owned registration/module/receipt are removed. Sibling integrations, open panes, dekit state, runners and tasks remain intact. Stop tasks explicitly with the displayed project-scoped `down` command. dekit is unbundled, owner-managed, and MIT-licensed.

# System

Open the [bottom 0.15.0](https://github.com/ClementTsang/bottom/releases/tag/0.15.0) system monitor (unmodified `btm`) in a native Rex pane, launched with `--read_only`. Quitting bottom returns focus to the exact terminal that invoked it.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server.
- Preinstalled bottom **0.15.0** with its `btm` executable on PATH. The Homebrew formula is `bottom`, not `btm`: check that `brew info bottom` selects 0.15.0 before `brew install bottom`, or use the upstream release. Verify with `btm --version`. RexDeck never installs, upgrades or removes bottom. A missing or different version makes `rexdeck enable system` print this guidance and register nothing.

## Enable and open

```sh
rexdeck setup   # once, if not already done
rexdeck enable system
rexdeck status system
rex do rexdeck_system
```

Open **RexDeck System** from Rex’s command palette, or `rex do rexdeck_system`.

bottom shows the whole machine. The invoking directory is only the pane's working directory.

## Use

Select a widget, sort or search processes, then quit.

These are bottom's own keys, unchanged:

| Key | Action |
| --- | --- |
| `H` `J` `K` `L`, Ctrl + arrows | Move between widgets |
| `e` / `Esc` | Expand the selected widget / go back |
| `c` / `m` / `p` / `n` (Processes) | Sort by CPU, memory, PID or name |
| `/` (Processes) | Search processes |
| `?` | Help |
| `q`, Ctrl + c | Quit and return to the invoking terminal |

## Configuration

### Pane layout

Use `rexdeck enable system --placement below --size 35` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80. Default: right, 50%.

### bottom settings

On macOS, bottom 0.15.0 first uses `~/.config/bottom/bottom.toml` if that file exists. Otherwise, when `XDG_CONFIG_HOME` is set, it uses `~/Library/Application Support/bottom/bottom.toml` if that file exists, or `$XDG_CONFIG_HOME/bottom/bottom.toml` otherwise. With `XDG_CONFIG_HOME` unset, it uses `~/Library/Application Support/bottom/bottom.toml`; this is also the first-run default location on macOS when neither an existing config nor an XDG override applies. See the [bottom configuration guide](https://bottom.pages.dev/stable/configuration/config-file/).

After the first run, edit the TOML file selected by that order. For the default macOS location, use `$EDITOR "$HOME/Library/Application Support/bottom/bottom.toml"`; if an existing `~/.config/bottom/bottom.toml` or an XDG-selected file takes precedence, edit that file instead. Widget layout, refresh rate, display options, themes and process-command display are upstream settings. RexDeck launches exactly `btm --read_only`, so signalling remains disabled regardless of saved preferences; it passes no config-path override or overlay.

RexDeck never edits upstream configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed so helpers cannot resolve from the working directory.

## Capabilities and limits

Qualification viewed the CPU, memory and process widgets, sorted processes by memory and then CPU, kept rendering after a resize to 44 columns, and quit back to the invoking block.

- `--read_only` disables stopping or signalling processes; `dd` did nothing in qualification. Navigation, sorting, search and quit stay available. RexDeck never passes `--disable_keys`, which would also disable quit.
- Process lists show every visible process's name and, with `--process_command` or its toggle, full command lines. Command lines can contain secrets.
- If its selected config file does not exist, bottom writes a default `bottom.toml` there on first run and reads it afterwards; see the macOS selection order above. No network access.

## Disable

```sh
rexdeck disable system
```

Only System's owned loader, module and receipt change. Other integrations and open bottom panes are left as they are. bottom and its configuration are left untouched; it is MIT-licensed, unbundled and owner-managed.

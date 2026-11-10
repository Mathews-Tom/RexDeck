# System

Open the [bottom 0.15.0](https://github.com/ClementTsang/bottom/releases/tag/0.15.0) system monitor (`btm`) in a native Rex pane, launched with `--read_only`. Quitting bottom returns focus to the exact terminal that invoked it.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Preinstalled bottom **0.15.0** with its `btm` executable on PATH. The Homebrew formula is `bottom`, not `btm`: check that `brew info bottom` selects 0.15.0 before `brew install bottom`, or use the upstream release. Verify with `btm --version`. RexDeck never installs, upgrades or removes bottom. A missing or different version makes `rexdeck enable system` print this guidance and register nothing.

```sh
rexdeck enable system
rex do rexdeck_system
```

bottom shows the whole machine. The invoking directory is only the pane's working directory.

## Controls

These are bottom's own keys, unchanged:

| Key | Action |
| --- | --- |
| `H` `J` `K` `L`, Ctrl + arrows | Move between widgets |
| `e` / `Esc` | Expand the selected widget / go back |
| `c` / `m` / `p` / `n` (Processes) | Sort by CPU, memory, PID or name |
| `/` (Processes) | Search processes |
| `?` | Help |
| `q`, Ctrl + c | Quit and return to the invoking terminal |

Qualification viewed the CPU, memory and process widgets, sorted processes by memory and then CPU, kept rendering after a resize to 44 columns, and quit back to the invoking block.

## Capabilities and limits

- `--read_only` disables stopping or signalling processes; `dd` did nothing in qualification. Navigation, sorting, search and quit stay available. RexDeck never passes `--disable_keys`, which would also disable quit.
- Process lists show every visible process's name and, with `--process_command` or its toggle, full command lines. Command lines can contain secrets.
- On first run bottom writes a default `bottom.toml` under the XDG config home (`~/.config/bottom/` by default) if none exists, and reads it afterwards. No network access.

## Disable

```sh
rexdeck disable system
```

Only System's owned loader, module and receipt change. Other integrations and open bottom panes are left as they are. bottom is MIT-licensed, unbundled and owner-managed.

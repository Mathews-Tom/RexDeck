# Disk

Explore the invoking terminal's directory in a native Rex pane with unmodified [dua-cli 3.0.1](https://github.com/Byron/dua-cli/releases/tag/v3.0.1) (`dua interactive`). Quit to return focus to the exact invoking terminal.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Owner-installed **dua 3.0.1** on a trusted absolute PATH. On macOS, install from source with an existing Rust toolchain: run `cargo install dua-cli --version 3.0.1 --locked` yourself, or build the tagged v3.0.1 source. The v3.0.1 release is a Linux-only build fix and provides no Apple binary download. Verify `dua --version` prints `dua 3.0.1`.
- The Homebrew formula is **`dua-cli`**, not `dua`. At qualification Homebrew provides **3.0.0**, which is **not qualified**. `brew info dua-cli` is informational; do not assume `brew install dua-cli` supplies 3.0.1. Use the pinned Cargo/tagged-source route instead unless Homebrew later selects exactly the qualified version.

RexDeck never installs, upgrades or removes dua. Missing or unqualified versions print owner-managed guidance and register nothing.

```sh
rexdeck enable disk
rexdeck status disk
# From a terminal in a directory you own inside Rex:
rex do rexdeck_disk
```

The terminal must report an existing working directory. RexDeck passes no paths, shell expressions or dynamic arguments: dua scans the entries in that directory. A missing, invalid or unavailable terminal directory is refused before a pane opens.

## Controls

Default upstream bindings (an existing dua config can change them):

| Key | Action |
| --- | --- |
| `j` / `k`, Down / Up | Select entries |
| `s` | Sort by size (repeat to reverse) |
| `Enter` / `l` / Right | Enter a directory |
| `h` / Left / Backspace | Leave a directory |
| `U` | Scan above the initial root: not confined to the initial directory |
| `]` | Hide/show right-side panes, useful at narrow widths |
| `?` | Show help |
| `q` | Quit (close an active auxiliary pane first if needed); press a second time when entries are marked or a costly scan is still running |

Qualification navigated nested owned fixtures of different sizes, sorted by size, entered and left directories, resized to 40 columns and quit without marking/deleting/trashing anything. A before/after path-and-content checksum manifest was unchanged.

## Capabilities and limits

- **Not read-only or sandboxed.** Interactive mode can mark entries (`Space`, `x`, `d`, `a`, and other mark commands), permanently delete marked entries **without another prompt** (`Ctrl+r`) or move them to Trash (`Ctrl+t`). Trash is included by the default `trash-move` feature in 3.0.1 and was visible in the installed build's help; custom builds may omit it. Do not press these commands unless you intend their effects. Qualification uses only disposable owned trees, never owner data, and does not exercise deletion or Trash.
- `O` delegates the selected path to an external application using the OS opener. That application has its own side effects. No external opener was exercised during qualification.
- dua reads optional **`~/Library/Application Support/dua-cli/config.toml` on macOS** (not `$XDG_CONFIG_HOME` there); on Linux/Unix it uses `$XDG_CONFIG_HOME/dua-cli/config.toml` or the platform fallback, and on Windows `%APPDATA%\dua-cli\config.toml`. It controls formatting, Git-ignore/cleanup heuristics, notifications and keybindings, including destructive bindings. Missing config uses defaults; unreadable/invalid config fails. `dua config show-default` displays the template without creating it. RexDeck does not modify this file. A lab-only config remapping quit to `z` verified both this macOS location and the remapped native key.
- The exact launch is `dua interactive`: **no `--once`**, whose optional value can replay configured keybindings or a compact character sequence after traversal, and **no snapshot `--export` or `--import`**. This is a live filesystem scan, not a read-only snapshot. Inherited upstream `DUA_*` settings still apply (including ignore patterns, traversal/display settings and optional log-file output). Scans can be expensive and sizes depend on filesystem accounting.
- Default completion notifications may emit terminal notification sequences. The upstream scanner itself needs no network service; external applications are not constrained by RexDeck.

The disclosures and default controls were checked against the [pinned configuration source](https://github.com/Byron/dua-cli/blob/v3.0.1/src/config.rs), [options](https://github.com/Byron/dua-cli/blob/v3.0.1/src/options.rs), [feature list](https://github.com/Byron/dua-cli/blob/v3.0.1/Cargo.toml) and the installed `dua interactive --help`.

## Disable

```sh
rexdeck disable disk
```

Only Disk's owned loader, module and receipt change. Other integrations and already open dua panes are left alive. dua-cli is MIT-licensed, unbundled and owner-managed.

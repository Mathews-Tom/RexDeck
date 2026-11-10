# JSON

Open one explicitly selected **`.json` file** in a native Rex pane with unmodified [jless 0.9.0](https://github.com/PaulJuliusMartinez/jless/releases/tag/v0.9.0), a read-only JSON viewer with collapsible nodes and search. Quitting jless returns focus to the exact terminal that invoked it.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server.
- Preinstalled jless **0.9.0** on PATH. Install it yourself, for example with `brew install jless` after checking that `brew info jless` selects 0.9.0, or from the upstream release. Verify with `jless --version`. RexDeck never installs, upgrades or removes jless. A missing or different version makes `rexdeck enable json` print this guidance and register nothing.

## Enable and open

Open **RexDeck JSON** from Rex's command palette, or invoke the native action with an explicit `file=` selection:

```sh
rexdeck enable json
rexdeck status json
# From a terminal inside Rex, with a deliberately selected file:
rex do rexdeck_json file="data/response.json"
```

The `file` argument is required. Invoking the action without it, or with a missing path, a directory, a file that does not end in `.json` (case-insensitive) or a path containing `*`, `?`, `[` or `\`, fails before a pane opens with a visible message. Absolute paths, local `file://` URIs and paths relative to the invoking terminal are accepted. A symlink is judged by its target's suffix. The resolved path reaches jless as one absolute argument after `--json`, so the file is always parsed as JSON, never as YAML. The pane opens in the file's directory.

## Use

These are jless's own keys, unchanged:

| Key | Action |
| --- | --- |
| `j` / `k`, Up / Down | Move between nodes |
| `h` / `l` (Left / Right), `Space` | Collapse / expand / toggle the focused node |
| `c` / `e` | Collapse / expand the focused node and all its siblings |
| `/` then text, Enter; `n` / `N` | Search forward; next / previous match (lowercase text matches case-insensitively) |
| `m` | Toggle data mode and line mode |
| `y` family | Copy a value, key or path to the system clipboard |
| `q` | Quit and return to the invoking terminal |

## Configuration

### Pane layout

`rexdeck enable json --placement right|left|below|above --size 20-80` sets placement and size; default: right, 50%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### jless settings

jless 0.9.0 has no configuration file or plugin system to edit; use its interactive commands to change the current display (for example `m` for data/line mode and `<` / `>` for indentation). See the [upstream user guide](https://jless.io/user-guide.html). RexDeck passes `--json` followed by the selected absolute file path, forcing JSON rather than YAML parsing.

RexDeck preserves the inherited environment, except that PATH contains only absolute entries (or the system default if none remain). It adds no tool-specific environment variables or configuration overlay and never edits upstream configuration.

## Capabilities and limits

Qualification opened a 5 MB fixture (28,000 objects), collapsed the 28,000-item array, found a single nested match with `/`, kept working after a resize to 38 columns, and quit back to the invoking block. Press `F1` or type `:help` inside jless for the full key list.

- Read-only: jless never writes the selected file. It holds the whole document in memory, so very large files need memory to match.
- The `y` commands write to the macOS clipboard. jless has no network access, configuration file or plugins.
- `F1` / `:help` pipes the key list into `less` found on the pane's PATH. Inside `less`, `!` and `|` run shell commands and `s` saves to a file; quit `less` with `q` to return to jless.
- No stdin, pipes, YAML or NDJSON through this integration.
- The file is checked when you invoke the action and again just before jless starts; jless then opens it by name. A process that can write to the file's directory could swap the file in that short window, so open untrusted files from directories only you control.

## Disable

```sh
rexdeck disable json
```

Only JSON's owned loader, module and receipt change. Other integrations and open jless panes are left as they are. jless is MIT-licensed, unbundled and owner-managed.

The upstream tool and its configuration remain untouched.

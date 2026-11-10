# Markdown

Read Markdown in a native Rex pane with unmodified [markdown-reader 1.35.4](https://github.com/leboiko/markdown-reader). Prose, tables and code render in the pane, and Mermaid diagrams are drawn as actual diagrams, not raw code fences. Quitting returns focus to the exact terminal you invoked it from.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and RexDeck set up as described in the [root README](../../README.md#getting-started).
- markdown-reader **1.35.4** on a trusted absolute `PATH` entry. Install it yourself from the [upstream release](https://github.com/leboiko/markdown-reader/releases/tag/v1.35.4): verify the archive against its `SHA256SUMS`, put `markdown-reader` on `PATH`, and check `markdown-reader --version`. RexDeck never installs, upgrades or removes it. Other versions are refused with manual guidance until they are qualified.

## Enable and open

```sh
rexdeck setup   # once, if not already done
rexdeck enable markdown
rexdeck status markdown
```

Enabling Markdown changes nothing for Files or Git; each integration owns its own loader, module and receipt. Pane placement and size follow the shared convention, for example `rexdeck enable markdown --placement below --size 60`. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80.

Choose **RexDeck Markdown** from Rex’s command palette, or `rex do rexdeck_markdown` to browse the invoking directory; pass `file=PATH` to select a Markdown file or directory as described below.

## Use

- **Browse the invoking directory:** choose **RexDeck Markdown** in the palette. markdown-reader opens with its file tree at the terminal's reported working directory. A working directory never stands in for a selected document.
- **Open a selected file:** pass the path as the action's `file` argument. It may be absolute, a local `file://` URI, or relative to the invoking terminal's directory. From a shell inside Rex:

  ```sh
  rex do rexdeck_markdown file="docs/design notes ü.md"
  ```

  A key binding can pass the same argument (`rex.bind(KEY, "rexdeck_markdown", {file = ...})`). Only files ending in `.md` or `.markdown` (any letter case), or directories, are accepted; the suffix is checked on the resolved file, so a symlink must point to Markdown. Missing files, other extensions and remote `file://` URIs fail visibly before any pane opens. The selected path reaches markdown-reader as one absolute structured argument, not a shell string.

Use markdown-reader's own keys. `Tab` switches between tree and viewer, `j`/`k`/`d`/`u` scroll, `Ctrl+f` searches the document, `?` shows help, and `q` quits back to the origin. When a diagram is wider than the pane, the reader says so in place ("Mermaid diagram too wide to display in place … Press Enter to open in fullscreen"). In the fullscreen diagram view, `h`/`l` pan, `j`/`k` scroll and `+`/`-` zoom. Resizing the pane while the fullscreen diagram is open can leave it showing `rendering… pending`; press `Esc` and reopen it with `Enter`. Browsing a directory restores the reader's own saved tabs for that location.

**Source view:** `i` (hybrid mode) shows the raw Markdown of the block under the cursor, and `I` opens the fullscreen editor. Both are editors and can save with `:w`. RexDeck does not make markdown-reader read-only.

## Configuration

### Pane layout

`rexdeck enable markdown --placement below --size 35` changes only this integration; the default is right/50%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### markdown-reader settings

Press `c` for the upstream settings modal (theme, line numbers, tree position and search preview), or edit TOML with `$EDITOR "$HOME/Library/Application Support/markdown-reader/config.toml"`. macOS sessions live alongside it in `state.toml`; the update cache is `~/Library/Caches/markdown-tui-explorer/last-version-check.json`. To stop daily update checks, add `[updates]` with `check_for_updates = false`. See [qualified upstream configuration and state documentation](https://github.com/leboiko/markdown-reader/blob/v1.35.4/README.md#configuration-and-state-files).

RexDeck passes the validated absolute file/directory as the only argument, with cwd set to that directory or the selected file's parent. There are no config overlays, Mermaid-mode overrides, image-protocol spoofing or `--check-external` flag. RexDeck never edits upstream config. The launcher inherits upstream configuration/authentication environment, but removes relative and empty `PATH` entries so helpers are not found in the invoking directory.

## Capabilities and limits

### Mermaid behavior in Rex

markdown-reader renders Mermaid in-process. Rex terminals do not advertise an image protocol that markdown-reader uses, so in a Rex pane it automatically draws **Unicode text diagrams**, footed `[mermaid — text mode, text-mode diagram]`, with boxes, labels, arrows and edge labels. RexDeck does not change its `mermaid_mode` setting or spoof terminal capabilities.

Qualified in a private Rex pane with markdown-reader 1.35.4:

| Corpus document | Diagrams | Result |
| --- | --- | --- |
| RexDeck development plan | 1 flowchart (14 nodes, fan-out) | Rendered. In a narrow pane it shows the explicit "too wide … press Enter" notice, and fullscreen shows the full graph with pan/zoom |
| Mixed prose document | flowchart with edge labels, sequence diagram | Both rendered inline at 170 columns, with every node and edge present |
| Root README, Files and Git READMEs | none | Prose and tables render |

Outside that corpus, state, class and ER diagrams also rendered. Unsupported types such as `zenuml`, and malformed blocks such as unknown diagram types or broken sequences, show their source with an explicit `[mermaid — figurehead render failed, showing source]` footer. A flowchart with unbalanced brackets can still be drawn literally, with the broken text as node labels, so the reader does not always validate Mermaid syntax. RexDeck does not claim full Mermaid-language coverage.

### Filesystem access and privacy

- **File writes:** the edit modes save Markdown in place.
- **Reads:** the browsed directory tree. The reader stores its own `config.toml`, session state (open tabs, scroll positions and the full paths of opened documents) and update-check cache under `~/Library/Application Support/markdown-reader` and `~/Library/Caches/markdown-tui-explorer`.
- **Runs Git:** to colour its file tree, markdown-reader runs `git status` in the browsed root when it starts and when files change. It uses the `git` found on `PATH` and honours the inherited `GIT_*` environment and repository configuration, including configured commands such as `core.fsmonitor`. Unlike RexDeck Git, RexDeck does not refuse inherited Git overrides here. `git status` can refresh the repository's index.
- **Network:** at most once a day the reader checks crates.io for a newer version. In the private lab, a fresh launch recorded `{"latest_version":"1.35.4"}` in its cache. The upstream upgrade notice prints on exit and is not visible after a Rex pane closes. Disable the check in markdown-reader's own `config.toml` with `[updates]` / `check_for_updates = false`. RexDeck never passes `--check-external`. Repeated socket sampling of a lab launch saw no open connections beyond that short check, and upstream renders documents and diagrams locally. RexDeck does not sandbox the tool.
- Your markdown-reader configuration, theme and keys stay unchanged.

## Disable

```sh
rexdeck disable markdown
```

This removes only the Markdown loader and module. Open reader panes, Files/Git integrations, markdown-reader itself and its settings remain.

markdown-reader is an unbundled [MIT-licensed upstream tool](https://github.com/leboiko/markdown-reader/blob/master/LICENSE).

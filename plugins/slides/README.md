# Slides

Present one explicitly selected **`.md` deck** in a native Rex pane with unmodified [presenterm 0.16.1](https://github.com/mfontanini/presenterm/releases/tag/v0.16.1). Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Preinstalled presenterm **0.16.1** on PATH. Install it yourself from the upstream release, or inspect `brew info presenterm` and use `brew install presenterm` only if it selects this qualified version. Verify with `presenterm --version`. RexDeck never installs, upgrades or removes it. Missing or different versions produce actionable guidance and register nothing.

```sh
rexdeck enable slides
rexdeck status slides
# From a terminal inside Rex:
rex do rexdeck_slides file="talk/deck.md"
```

`file` is required: an existing `.md` file, case-insensitive. Missing files, directories, `.markdown` and other suffixes are refused visibly before opening a pane. Absolute paths, local `file://` URIs and paths relative to the invoking terminal work. Symlinks are judged by their resolved target's suffix. The resolved absolute file is the final argv element, with no static flags; the pane starts in its parent directory. This is presentation, not an editor or a PDF/HTML export action.

## Controls

These are presenterm's default controls; user configuration can change them:

| Key | Action |
| --- | --- |
| Right / `l` | Next slide or pause |
| Left / `h` | Previous slide or pause |
| `q` | Quit and return to the invoking terminal |

Qualification navigated forward/back through a four-slide deck, displayed a Python code block, visited a local PNG image slide, resized to 40 columns, continued navigating and quit to the exact origin. A sibling Files pane and an open Slides pane both remained alive after disabling Slides.

**Images:** in the isolated Rex server, auto-detection selected **Kitty local**: the recorded terminal stream contained Kitty capability probes followed by a `t=f,a=T` image transmission for the 120×80 PNG. There was no ASCII-block fallback. Text/VT capture showed the slide heading/footer but omitted the raster; no graphical client was attached, so this does **not** qualify visual fidelity in the Rex app. Upstream also supports iTerm2, Sixel and ASCII blocks; RexDeck does not force a protocol. User config can override auto-detection.

## Capabilities and limits

- Reads the selected deck, local images, referenced theme files and other Markdown included with `<!-- include: … -->`. A fenced `file` snippet can read and display any accessible file, including absolute paths: an untrusted deck can expose credentials or other private files in the pane without executing code. **Open only trusted decks.** It does not edit the deck. Image paths resolve relative to the deck; remote images are unsupported.
- **Not a sandbox.** RexDeck passes none of `-x`, `--enable-snippet-execution`, `-X`, `--enable-snippet-execution-replace` or `--validate-snippets`. Nevertheless, **user config `snippet.validate: true` automatically runs `+validate` snippets as soon as a deck loads, without a keypress, even when `snippet.exec.enable` is false**—the same execution behavior as the omitted `--validate-snippets` flag. User config `snippet.exec.enable: true` enables `Ctrl+E` execution of `+exec` blocks and runs `+auto_exec` blocks at load without a keypress. Because this integration passes no `-p`, presenterm uses development mode and reruns `+auto_exec` blocks on every file reload. `snippet.exec_replace.enable: true` enables automatic execution of both `+exec_replace` and `+image` blocks. The lab observed no marker creation with default config, then marker creation after `Ctrl+E` with `snippet.exec.enable` enabled; the other execution paths are established by the pinned upstream source. Never enable execution or validation for untrusted decks.
- Rendered Typst, LaTeX, Mermaid and D2 blocks can run external `typst`, `pandoc`, `mmdc` and `d2` helpers **independently of snippet execution**. They write temporary files; Typst's temporary directory is inside the deck directory. Helpers and enabled snippets can read/write files and access the network with your account's permissions.
- For 0.16.1 the default config is `$XDG_CONFIG_HOME/presenterm/config.yaml` when XDG is set; otherwise on macOS it is `~/Library/Application Support/presenterm/config.yaml`. `PRESENTERM_CONFIG_FILE` overrides that config file. The lab confirmed both default locations using deliberately invalid isolated config files.
- At startup it also reads presentation themes from `themes/*.yaml` and highlighting themes from `themes/highlighting/` beneath that **default configuration directory**, even when the config file is overridden. Deck front matter may reference another theme file via `theme.path`. Existing config and themes are respected, not rewritten by this integration. No project-local `config.yaml` is implicitly selected.
- User config `speaker_notes.always_publish` enables UDP speaker-note events to the configured publish address (default `127.0.0.1:59418`); the integration passes no speaker-note flags. Ordinary local text/image decks need no network; enabled snippets, renderers and configuration can change that.
- Selection is checked on invocation and just before launch, but presenterm then opens the path by name. Use directories you control to avoid another process swapping a selected file between checks and open.

These paths and execution/rendering disclosures are grounded in the [0.16.1 configuration documentation](https://github.com/mfontanini/presenterm/blob/v0.16.1/docs/src/configuration/introduction.md), [execution documentation](https://github.com/mfontanini/presenterm/blob/v0.16.1/docs/src/features/code/execution.md) and pinned source (`src/main.rs`, `src/third_party.rs`, `src/tools.rs`).

## Disable

```sh
rexdeck disable slides
```

Only Slides' owned loader, module and receipt change. Other integrations and open presenterm panes stay alive. presenterm is BSD-2-Clause-licensed, unbundled and owner-managed.

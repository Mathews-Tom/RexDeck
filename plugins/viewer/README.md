# Viewer

Preview an explicitly selected **PDF, JPG, PNG or SVG** inside a native Rex pane with unmodified [Yazi 26.9.1](https://github.com/sxyazi/yazi/releases/tag/v26.9.1). This independently selectable integration reuses Files' upstream tool, not a RexDeck graphics renderer, and does not open a native GUI automatically.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)



Quitting returns focus to the exact invoking terminal.

## Requirements

- macOS arm64 and the qualified Rex server with Kitty graphics support. The private qualification server identified itself as `0.0.1793981048-g01d6c4`; an API revision alone does not establish graphics support. No image-protocol environment variables are spoofed.
- Preinstalled Yazi **26.9.1**, [`pdftoppm`](https://poppler.freedesktop.org/) (Poppler; exercised at **26.10.0**) and [`resvg`](https://github.com/linebender/resvg/releases/tag/v0.49.0) (exercised at **0.49.0**). Enable validates Yazi's version and helper presence; helper versions are not gated. Helpers must also be on the **Rex server's PATH**, which can differ from the enabling shell. RexDeck does not install anything or rewrite Yazi configuration.
- Use trusted upstream packages, verify release checksums, and check `yazi --version`, `pdftoppm -v` and `resvg --version` yourself. `rsvg-convert` is **not** a replacement for the `resvg` executable expected by Yazi's default SVG previewer.

## Enable and open

```sh
rexdeck enable viewer
rexdeck status viewer
# Optional larger preview pane:
rexdeck enable viewer --size 80
rexdeck status viewer
# From a terminal inside Rex, with a deliberately selected path:
rex do rexdeck_viewer file="docs/diagram.svg"
```

The `file` argument is required: palette invocation without a selection fails visibly instead of treating the working directory as a file. Absolute paths, local `file://` URIs and paths relative to the invoking terminal are accepted. Only regular files with resolved suffix `.pdf`, `.jpg`, `.jpeg`, `.png` or `.svg` (case-insensitive) are accepted. Symlink targets, not misleading link suffixes, determine eligibility. The target reaches Yazi as one absolute argv element. Missing files, remote URIs, directories, unsupported suffixes and obvious signature/type mismatches fail before a pane opens. Header/XML-root validation is not full decoding, sanitization, an authenticity check or a sandbox.

Open **RexDeck Viewer** from Rex’s command palette with a selected file argument, or use the native action above.

## Use

### Each viewing route

| Selected format | Default route | Controls and qualified content |
| --- | --- | --- |
| JPG / JPEG | Yazi decodes JPEG and sends Kitty graphics to the Rex pane | Distinct green JPG with readable label; fit-to-preview resizing exercised |
| PNG | Yazi decodes PNG and sends Kitty graphics to the Rex pane | Distinct blue PNG with readable label; fit-to-preview resizing exercised |
| SVG | Yazi invokes `resvg` to rasterize static SVG, then sends Kitty graphics | Purple triangle and text; script and HTTP image reference did not execute/fetch in the exercised fixture |
| PDF | Yazi invokes `pdftoppm` for the selected page, then sends Kitty graphics | Readable first, next and last page markers in a three-page document; **Shift+J** advances one PDF page, **Shift+K** goes back |

Yazi's normal preview column fits the image within the available pane. Give it enough space with the shared `--placement`/`--size` options or resize the pane; small lettering can become illegible in narrow previews. Qualification observed actual transmitted pixels at 232×155 for a small image preview and 528×352 after resizing to 180 columns / 50 rows (PDF 528×440). These are fixture sizes, not fixed product limits. `q` quits Yazi; the shared launcher returns the **Rex session focus** to the exact invoking block. All four quits were inspected. Other integrations and existing viewer panes are independent.

**Evidence boundary:** private Rex replied `OK` to Kitty direct/shared-memory queries. Yazi's actual shared-memory raster payloads and placement commands were intercepted transparently, decoded and visually inspected, including all three PDF page markers. Text/HTML `rex capture` omits the graphics plane. No private GUI client was available, so the owner-app on-screen composition/clipping remains an owner-only visual check; neither blank text capture nor a successful process exit proves it. Sixel was not advertised by primary device attributes; iTerm2 inline-image display was not qualified. Unsupported graphics is not a successful preview: if the column is blank, verify the Rex client/server graphics path and Yazi's [image-preview requirements](https://yazi-rs.github.io/docs/image-preview/). RexDeck does not silently launch a browser, install Chafa, or substitute source text for a rendered image.

### Source and explicit external opening

For SVG source, press `:` in Yazi, type `less -L -+F -- %h` and press Enter **only in that shell-command prompt**. Yazi 26.9.1 substitutes the hovered file using its `%h` template; the attempted legacy `"$0"` variant instead tried to read `sh` in qualification. `-L` ignores `LESSOPEN` preprocessors and `-+F` keeps short files visible. Avoid `LESS=-r`/`LESS=-R`: allowing raw terminal escape sequences is not appropriate for untrusted source. `q` returns from less to Yazi. This exact `%h` route displayed the SVG source in private qualification (`source-percent-less-screen.txt`, with prompt/command captures alongside it); no external opener was invoked. Source access and helper commands are not a sandbox. PDF text extraction is a separate owner-run `pdftotext /absolute/path/to/selected.pdf -` operation, not the graphical PDF viewer and not a required source mode for binary JPG/PNG. No custom source editor is installed.

**Enter / `o` is not the preview control:** Yazi follows the owner's opener rules (on macOS, the default application). That application may fetch external resources and run SVG scripts. RexDeck never invokes it automatically. During qualification an extra Enter while experimenting with the source pager accidentally opened the lab SVG in Gapplin; the local canary observed both the external-image fetch and the script's request. Only that lab document was closed, without saving. A subsequent isolated in-pane SVG/PDF test made no such requests. Do not assume static-preview restrictions carry over to native apps or browsers. External window appearance and OS application-focus restoration were not visually observed; `q` restores Rex block focus, not global macOS application focus.

## Configuration

### Pane layout

`rexdeck enable viewer --placement right|left|below|above --size 20-80` changes only this integration; the default is right/50%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### Yazi settings

Edit TOML settings with `$EDITOR ~/.config/yazi/yazi.toml`, keys in `~/.config/yazi/keymap.toml`, and colours in `~/.config/yazi/theme.toml`; Lua customization uses `init.lua`. `YAZI_CONFIG_HOME` selects an alternative directory. Copy only settings you want to override, such as `[mgr] show_hidden = true`, previewers/preloaders and opener rules. See [upstream configuration](https://yazi-rs.github.io/docs/configuration/overview/). RexDeck never edits upstream config or overrides keys, themes, openers or previewers.

RexDeck passes the selected absolute file as the only Yazi argument and starts in its parent directory. It supplies no graphics flags, protocol spoofing or config overlay. The launcher inherits upstream configuration/authentication environment, but removes relative and empty `PATH` entries so helpers are not found in the invoking directory.

## Capabilities and limits

- Missing Yazi / helpers fail enable with manual-install guidance. If a helper is missing only from the server PATH, the upstream preview column reports e.g. `Failed to start resvg ... No such file or directory`. Header-valid decoder-failure fixtures showed `unexpected end of file` (PNG), `Format error decoding Jpeg`, `Failed to convert PDF to image` and `resvg exited with error code` (SVG). These are errors, not successful views. The initial check does not prove full validity. Owner-customized previewers can change these behaviors.
- **Static SVG:** resvg supports the [static subset, not scripting/events/animations](https://github.com/linebender/resvg/tree/v0.49.0#svg-support). The exercised `<script>` plus HTTP `<image>` fixture produced no canary requests. This is not a network sandbox: local referenced image/font files may be read, embedded images are decoded, and custom Yazi previewers/openers can execute arbitrary programs or use the network.
- **PDF:** `pdftoppm` rasterizes pages, not an interactive PDF browser: hyperlinks, JavaScript/actions, forms, embedded-file launching and multimedia are not activated by preview navigation. A fixture containing a JavaScript OpenAction and HTTP link annotation rendered all three pages without canary requests. Linked documents are not followed automatically. Malformed/encrypted/unsupported PDFs can fail; parsing still exposes Poppler's attack surface. Use current trusted helpers and do not mistake rendering for sanitization.
- **Sibling-file parsing:** opening a selected file also opens its parent folder. Yazi type-checks visible sibling files and can preload them with `file(1)`, `pdftoppm`, `resvg` and ImageMagick, even when you selected only a PNG. Use a trusted or isolated folder for untrusted inputs; selecting one file does not limit parsing to that file. The shared launcher strips relative/empty PATH entries, but absolute configured helpers, previewers and local resources remain trusted.
- **Normal Yazi authority:** filesystem browsing, writes, rename/delete, shell commands, configured previewers/editors/openers and cache writes remain available as your OS account. A selected file is a starting point, not a directory jail. No credentials, telemetry, renderer service or background daemon is added by RexDeck. Your own configuration can change capabilities.

## Disable

```sh
rexdeck disable viewer
```

Only Viewer's owned loader/module/receipt changes; Files, other integrations and open Yazi panes survive. Upstream tools and their settings/cache remain. Yazi is MIT-licensed; resvg is MIT/Apache-2.0; Poppler carries GPL licensing. All are unbundled, owner-managed upstream tools.

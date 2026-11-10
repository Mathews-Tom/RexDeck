# HTTP

Send HTTP requests and inspect responses in a native Rex pane with unmodified [Slumber 5.4.4](https://github.com/LucasPickering/slumber/releases/tag/v5.4.4). Quitting returns focus to the exact terminal that opened it.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and [RexDeck setup](../../README.md#getting-started).
- Install **Slumber 5.4.4** yourself from the [upstream release](https://github.com/LucasPickering/slumber/releases/tag/v5.4.4), on a trusted absolute `PATH` entry. Check `slumber --version`. RexDeck never installs, upgrades or removes Slumber; missing and other versions are refused with manual guidance.
- A trusted Slumber request collection. RexDeck launches plain `slumber`, with **no arguments**, in the invoking terminal's reported directory. Slumber searches that directory, then its parents, checking `slumber.yml`, `slumber.yaml`, `.slumber.yml`, `.slumber.yaml` in that order in each directory. No `--file` override is needed. A parent collection can therefore be selected; this is not project isolation or a sandbox. See the [5.4.4 collection loading rules](https://github.com/LucasPickering/slumber/blob/v5.4.4/docs/src/api/request_collection/index.md#format--loading).

## Enable and open

```sh
rexdeck enable http
rexdeck status http
```

Open **RexDeck HTTP** from Rex’s command palette, or `rex do rexdeck_http`.

Choose **RexDeck HTTP** in the palette from a terminal in your collection directory. Placement follows the [shared convention](../../docs/configuration.md#pane-placement-and-size), initially right / 50%.

## Use

Review the collection and profile, select a recipe, send it only when intended, inspect the response, then quit.

| Key | Action |
| --- | --- |
| `Enter` | Send selected recipe |
| `r` / `j` | Focus recipes / select next recipe |
| `s` | Hide recipe sidebar |
| `2` / `f` | Focus Request / Response / fullscreen |
| `q` | Quit and restore invoking-terminal focus |

Controls exercised with the default bindings:

- `Enter` sends the selected recipe. Inspect status, response headers and body in Slumber's lower Request / Response pane.
- `r` focuses the recipe list; `j` selects the next recipe, then `Enter` sends it.
- `s` hides the recipe sidebar; `2` focuses Request / Response; `f` makes that pane fullscreen. This made the response body readable after resizing the terminal to **40 columns**; the normal multi-pane layout clips heavily at that width.
- `q` quits Slumber and restores Rex focus to the exact origin block.

Private qualification used an installed wheel outside the checkout, an isolated Rex endpoint/HOME and a disposable local HTTP server bound to `127.0.0.1` on a random port. One GET and one POST returned `200 OK`; both JSON response markers and the echoed POST body were inspected in the pane. No production endpoint or real credential was used. Disabling HTTP with HTTP and Files panes open left both panes alive and Files enabled.

## Configuration

### Pane layout

Use `rexdeck enable http --placement right|left|below|above --size 20-80` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). Default: right, 50%.

### Slumber settings

Run `slumber config --path` to locate the global YAML, then `slumber config --edit` to open it in your configured editor, or use `$EDITOR "$HOME/Library/Application Support/slumber/config.yml"` for the default macOS path. `SLUMBER_CONFIG_PATH` overrides that path. Review `preview_templates: false` to disable background execution previews, `persist: false` to prevent new request-history persistence, and editor/pager/query commands. See the [pinned configuration reference](https://github.com/LucasPickering/slumber/blob/v5.4.4/docs/src/api/configuration/index.md).

Edit your trusted collection YAML in your editor (for example `$EDITOR ./slumber.yml`) to change recipes, profiles and templates; review referenced `$ref` files too. RexDeck launches plain `slumber` with no arguments, environment overrides or config overlay; upstream collection discovery and inherited settings remain effective.

RexDeck never edits upstream configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed so helpers cannot resolve from the working directory.

## Capabilities and limits

Slumber is an HTTP client, **not read-only**. Requests can mutate remote services. Collections can chain additional requests through templates, and redirects are followed by default. RexDeck does not restrict destinations or isolate credentials. **Review the collection/profile outside Slumber before opening it:** background template previews are enabled by default (`preview_templates: true`), so opening Slumber or selecting a recipe can execute commands and read local files/environment variables before you send a request. Set `preview_templates: false` in Slumber's global configuration to disable these previews; this does not sandbox templates when a request is sent.

**Never put secrets in launch arguments or committed collection literals.** Slumber resolves runtime values through templates such as `env('API_TOKEN')`, `command(['your-secret-helper'])` and `prompt(message='Token', sensitive=true)`. Mark environment/command results with `| sensitive()` to mask previews. A helper must not put secrets in its own argv either: use its secure input mechanism/stdin, not a secret-valued command argument. RexDeck passes no Slumber arguments and enables no remote-control feature.

Only open collections you trust: **`command(...)` templates execute subprocesses as your OS account, including during background previews**, with their working directory relative to the collection file. Templates can also read local files and environment variables during previews. Collections can load other YAML files by path through `$ref`; review those referenced files too. Configured editors/pagers, response query/export commands and file editing/export retain upstream execution and filesystem-write authority. These are upstream capabilities, not a RexDeck sandbox. See the [5.4.4 template function documentation source](https://github.com/LucasPickering/slumber/blob/v5.4.4/crates/core/src/render/functions.rs) and [configuration reference](https://github.com/LucasPickering/slumber/blob/v5.4.4/docs/src/api/configuration/index.md).

Slumber writes local state:

- **Database:** on macOS, normally `~/Library/Application Support/slumber/state.sqlite` (plus SQLite WAL/shared-memory sidecars). `slumber db --path` reports the actual path. All collections share this database, which stores UI state and, by default, **request and response history including potentially sensitive data**. Its `commands` table also persists query and export commands per collection; those commands may contain secrets. Preview masking is not storage redaction. Upstream `persist: false` on a recipe or in global configuration prevents new request-history persistence but still writes UI state and does not delete old history. See [5.4.4 persistence documentation](https://github.com/LucasPickering/slumber/blob/v5.4.4/docs/src/user_guide/database.md).
- **Configuration:** normally `~/Library/Application Support/slumber/config.yml`; `SLUMBER_CONFIG_PATH` can override it. `slumber config --path` reports the actual location. Slumber creates its config directory/file on first TUI use. RexDeck does not rewrite settings, disable history or remove existing data.
- **Logs:** each session writes a temporary `slumber-<UUID>.log` (at least warning-level logging). `?` shows its path; `slumber --print-log-path` reports a CLI session's path. Treat logs as potentially sensitive. The lab observed the config, SQLite database/sidecars and temporary log files only under its disposable root.

## Disable

```sh
rexdeck disable http
```

Only HTTP's owned loader/module/receipt are removed. Other integrations, open panes, Slumber, collections, configuration, logs and history remain. Slumber is an unbundled [MIT-licensed tool](https://github.com/LucasPickering/slumber/blob/v5.4.4/LICENSE).

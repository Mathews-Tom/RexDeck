# Dashboard

Open an explicitly selected **trusted `.json` board** with unmodified [Dashbrew 1.2.0](https://github.com/rasjonell/dashbrew/releases/tag/v1.2.0) in a native Rex pane. Dashbrew renders script and API data; quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server.
- Preinstalled [Go](https://go.dev/dl/) on PATH, including during enable: Dashbrew has no `--version` flag, so Go inspects its embedded module metadata without running Dashbrew. RexDeck runs this probe from `/` with `GOTOOLCHAIN=local`, preventing checkout `go.mod`/`go.work` files from selecting or downloading another Go toolchain.
- Owner-installed Dashbrew **1.2.0** on PATH. Dashbrew is not a Homebrew formula; use the pinned upstream Go install route below, not the generic Homebrew suggestion in dependency guidance. Installing downloads and builds Go modules and writes the executable to `GOBIN` (normally `$(go env GOPATH)/bin`); RexDeck never runs this command or installs, upgrades or removes tools.

```sh
go install github.com/rasjonell/dashbrew/cmd/dashbrew@v1.2.0
GOTOOLCHAIN=local go version -m "$(command -v dashbrew)"
# Require: mod github.com/rasjonell/dashbrew v1.2.0 h1:...
```

Missing Go, missing Dashbrew or an unqualified module version refuses enable and registers nothing. Keep the executable and Go in trusted absolute PATH directories. `rexdeck plugins --json` lists the integration's capabilities.

## Enable and open

```sh
rexdeck enable dashboard
rexdeck status dashboard
```

Open **RexDeck Dashboard** from Rex’s command palette, or `rex do rexdeck_dashboard file="boards/trusted.json"`.

The `file` argument is mandatory. Missing paths, directories and non-JSON suffixes are refused before a pane opens. Absolute paths, local `file://` URIs and paths relative to the invoking terminal are accepted; the resolved target must end in `.json` (case-insensitive), including for symlinks. The pane opens in the selected file's directory and runs `dashbrew -c /absolute/selected.json`, with the path as one argument after `-c`.

**RexDeck never auto-loads a checkout's `dashboard.json`.** Running upstream Dashbrew yourself without `-c` does load `dashboard.json` from its current directory. An omitted Rex action selection is an error, not that upstream default. RexDeck checks the path, not the JSON schema or its safety; upstream parses and validates the board. A directory writer can swap the file after the path checks, so use directories you control.

## Use

Review the selected board, inspect components, refresh if enabled, then exit with Ctrl + C.

### A small trusted board

This example executes a local shell command and sends an unauthenticated public GET. Read it before opening it; do not put secrets in URLs or JSON.

```json
{
  "layout": {
    "type": "container",
    "direction": "column",
    "children": [
      {
        "type": "component",
        "flex": 1,
        "component": {
          "type": "text",
          "title": "Local clock",
          "data": {"source": "script", "command": "date -u", "refresh_interval": 3600}
        }
      },
      {
        "type": "component",
        "flex": 1,
        "component": {
          "type": "text",
          "title": "Public GitHub zen",
          "data": {"source": "api", "url": "https://api.github.com/zen", "refresh_interval": 3600}
        }
      }
    ]
  }
}
```

### Controls

| Key | Upstream action |
| --- | --- |
| `H` / `J` / `K` / `L` or Shift + arrow | Focus another board component |
| `r` / `R` | Refresh the focused component when its `refresh_interval` is positive |
| Ctrl + K | Open the command palette |
| Ctrl + C | Quit and restore exact-origin focus (`q` is not the quit key) |

Positive `refresh_interval` values in a component's data enable both manual refresh and repeated refreshes in seconds. The example uses 3600 seconds so `r` works without frequent background requests. Without a positive interval, upstream disables ordinary manual refresh too. Board-configured action bindings execute their own commands.

Native-lab qualification rendered a local clock and public GitHub GET, refreshed the clock, remained usable at 40 columns, and restored the invoking block on Ctrl + C. A separate loopback GET probe recorded no Authorization or Cookie header and timed out at about five seconds. Disabling Dashboard preserved its open pane and a sibling Files pane.

## Configuration

### Pane layout

Use `rexdeck enable dashboard --placement right|left|below|above --size 20-80` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). Default: right, 50%.

### Dashbrew settings

Edit your selected JSON board with `$EDITOR /absolute/boards/trusted.json`, then reopen it with the same `file=` selection. Layout, components, script/API sources, refresh intervals, theme and action bindings belong to that board; the example above is a starting point, not a RexDeck-generated file. See the [pinned upstream guide](https://github.com/rasjonell/dashbrew/blob/v1.2.0/README.md) and [CLI implementation](https://github.com/rasjonell/dashbrew/blob/v1.2.0/cmd/dashbrew/main.go).

For custom themes, edit JSON files in `DASHBREW_THEMES_DIR` or `${XDG_CONFIG_HOME:-$HOME/.config}/dashbrew/themes`; create the directory yourself if needed. See [theme loading](https://github.com/rasjonell/dashbrew/blob/v1.2.0/internal/config/themes.go). RexDeck passes only `-c /absolute/selected.json`, with cwd set to that file’s parent, no theme flag, environment override or configuration overlay.

RexDeck never edits upstream configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed so helpers cannot resolve from the working directory.



## Capabilities and limits

- **A board is code, not passive JSON. Only open trusted boards.** Script sources run immediately through `sh -c`, with the pane's permissions, inherited environment and selected-file directory. Script refreshes have no execution timeout. Action bindings also use `sh -c`; their configurable timeout defaults to 30 seconds. Scripts/actions can read or write files, launch programs and use the network. RexDeck does not sandbox them or filter their commands.
- API sources use Go's HTTP GET client with a **5-second timeout** covering the request, redirects and body read. The client configures no authentication headers or cookie jar. It still sends standard HTTP headers, follows redirects and uses Go's default transport (including proxy environment settings). URL credentials can cause Basic authentication, and scripts can send arbitrary headers: keep credentials, tokens and private URLs out of boards. Public endpoint availability and rate limits are external dependencies.
- Dashbrew reads but does not save the board JSON. **Todo components rewrite their configured `data.source` file** when items change (creating it with mode `0644` if needed); scripts/actions can make arbitrary additional writes. Text script/API boards have no built-in persistent output cache or log file.
- Custom theme JSON is also read at startup from `DASHBREW_THEMES_DIR` and `$XDG_CONFIG_HOME/dashbrew/themes` (falling back to `~/.config/dashbrew/themes`). This is separate from board selection; no theme directory is created by Dashbrew.
- No RexDeck data pipeline, authentication UI or automatic board discovery is added. Upstream JSON parsing and dashboard behavior are unchanged.

These disclosures are verified against the pinned [CLI flags](https://github.com/rasjonell/dashbrew/blob/v1.2.0/cmd/dashbrew/main.go), [script/GET/todo implementation](https://github.com/rasjonell/dashbrew/blob/v1.2.0/internal/data/data.go), [actions](https://github.com/rasjonell/dashbrew/blob/v1.2.0/internal/tui/actions.go), [refresh scheduler](https://github.com/rasjonell/dashbrew/blob/v1.2.0/internal/tui/scheduler.go) and [theme loading](https://github.com/rasjonell/dashbrew/blob/v1.2.0/internal/config/themes.go).

## Disable

```sh
rexdeck disable dashboard
```

Only Dashboard's owned loader, module and receipt change. Other integrations and already-open Dashbrew panes remain alive; exit those with Ctrl + C. Dashbrew is MIT-licensed, unbundled and owner-managed.

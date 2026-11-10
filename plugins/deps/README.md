# Dependencies

Browse package dependencies, licenses and known advisories with a RexDeck-authored Textual view over the public deps.dev and OSV APIs. It opens in a native Rex pane; quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server; invoke from a Rex terminal reporting its directory through OSC7.
- The view is packaged with RexDeck (MIT); no additional tool, pinned upstream binary or account is required. An upstream `--version` check does not apply. RexDeck never installs external tools.
- Internet access to the public deps.dev and OSV APIs.

## Enable and open

```sh
rexdeck setup   # once, if not already done
rexdeck enable deps
rexdeck status deps
```

Open **RexDeck Dependencies** from Rex's command palette, or run `rex do rexdeck_deps`. No `file=`, account or remote argument is required: the invoking directory is launch context only; choose package coordinates inside the view.

## Use

Enter a system(`npm`, `pypi`, `go`, `maven`, `cargo`, `nuget`, `rubygems`), package name, and optional version. Enter or Fetch loads it. A blank version selects the version marked default by deps.dev, never a guessed lexical latest. If no default is available, enter a version. Maven names are `group:artifact`; scoped npm names and Go paths can include slashes. PyPI and NuGet normalization follows deps.dev.

Escape focuses the report; scroll with arrows/PageDown. `n`/`p` page dependency lists (50 per page), `r` explicitly refreshes, `e` edits the package, `o` opens the exact displayed version on deps.dev via validated `/usr/bin/open` argv, and `q` quits back to the invoking terminal. While editing, use Escape before shortcuts. Refresh is also available as a button. Changing fields does not change the loaded selection until Enter/Fetch. At 40 columns the fields stack and report text wraps. 

## Configuration

### Pane layout

`rexdeck enable deps --placement below --size 35` sets placement and size; default: right, 60%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80.

### Dependencies view settings

Enter the system, package name and optional version in the view; `e` returns to those fields and Enter/Fetch applies the selection. There is no persistent selection/config file, credential environment variable or upstream tool configuration to edit. Successful snapshots live only in memory; no repository, manifest, lockfile or SBOM is inspected.

RexDeck launches its distribution's interpreter with `-I -m rexdeck.deps` in the invoking directory. It preserves the inherited environment except for restricting PATH to absolute entries (or the system default if none remain), adds no view-specific environment override or configuration overlay, and never edits upstream configuration. Pane layout is the only persistent setting described here.

## Capabilities and limits

**Package coordinates leave the machine**, sent to public deps.dev and OSV APIs. The OSV `POST /v1/query` is a non-mutating metadata query. The working directory is only launch context: no repository, manifest, lockfile or SBOM is read or uploaded. Nothing is installed, repaired or written to a registry. No credentials are requested. No polling occurs; successful snapshots are held in memory only.

Licenses and advisory IDs are available for all seven systems. Resolved direct/ indirect dependency graphs are provided by deps.dev for npm, Cargo, Maven and PyPI only. Go, NuGet and RubyGems show an explicit graph-not-provided notice. A graph-only 404 shows the same notice without hiding licenses or advisories. Graph-resolution warnings are displayed, never interpreted as an empty graph.

deps.dev advisory keys are listed as IDs only; there are no per-advisory network requests. Summaries and severity come from OSV when supplied; otherwise severity is "Not provided", never an invented zero. The same advisory may appear once for each source. Advisories apply to the entered **package version**, not every transitive dependency. OSV CVSS vectors are shown as supplied, not converted into invented numeric scores. Superseding a fetch or quitting stops further requests after any already-running request completes. Fetched-at is UTC retrieval time, not a promise that upstream records are current. No advisories means **none known to these sources at fetch time**, not a safety verdict or proof of absence. Missing licenses and incomplete graphs are explicit.

Loading, not-found/empty, permission, rate-limit, network and malformed-response states are visible. Press `r` to retry after fixing connectivity or waiting for a rate limit. A failed refresh keeps the last successful report marked STALE and retains its original fetched-at time. A different package never inherits that snapshot. No automatic retries or background requests. Each API string is sanitized and rendered without markup. API-provided links are never opened.

Sources: [deps.dev v3](https://docs.deps.dev/api/v3/) and [OSV query API](https://google.github.io/osv.dev/post-v1-query/). RexDeck view code is MIT; metadata remains attributable to these upstream sources.

## Disable

```sh
rexdeck disable deps
```

Disabling Dependencies removes its owned loader/module/receipt only. Already-open panes and other integrations remain usable; upstream services, tools and configuration remain untouched.

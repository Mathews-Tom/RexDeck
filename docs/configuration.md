# Configuration

RexDeck keeps its own settings separate from Rex and from every upstream tool. It never edits an upstream tool's configuration: you edit those files yourself, and each integration guide says where they live and what RexDeck passes at launch.

[Back to RexDeck](../README.md) · [Usage](usage.md) · [Integrations](integrations.md)

## Where RexDeck keeps its files

| What | Location | How to change it |
| --- | --- | --- |
| Saved Rex target and approved preferences | `$XDG_CONFIG_HOME/rexdeck/config.json` (default `~/.config/rexdeck/config.json`) | `rexdeck setup` saves it. Setup refuses to retarget existing saved ownership. To retarget a different Rex configuration or server, follow the steps in [Retarget a saved setup](#retarget-a-saved-setup) |
| Deployed integration modules | `$XDG_DATA_HOME/rexdeck` (default `~/.local/share/rexdeck`), frozen at setup: `plugins/<id>/rexdeck_<id>.lua` | Managed by `enable`/`disable`; do not edit |
| Rex loader | One independently owned, tracked loader per integration in Rex's `init.lua` | Managed by `enable`/`disable`; an edited or unowned loader reports `conflict` |
| Receipts, locks and attention data | The saved workspace, by default `$XDG_STATE_HOME/rexdeck/files` (`~/.local/state/rexdeck/files`). Files uses it directly; every other integration uses its `<id>/` subdirectory, and attention uses `attention/` | Managed by RexDeck |

Later `XDG_DATA_HOME` changes do not retarget the saved installation. The uv-managed package remains the runtime source.

## Pane placement and size

Each integration opens its pane split from the invoking terminal. By default the pane goes to the **right** and takes **50%** of that terminal's space; Dependencies, Images and Processes default to 60%. Choose a different side or share when enabling:

```sh
rexdeck enable git --placement below --size 35
rexdeck enable files --size 40          # keeps the saved placement
rexdeck status git                       # PANE column shows "below 35%"
```

`--placement` is `right`, `left`, `below` or `above`. `--size` is the tool pane's whole-percentage share, from 20 to 80.

- **Validation:** invalid values are rejected before anything changes.
- **Omitted options:** an omitted option keeps the integration's saved value, including across `disable`/`enable`. Re-running `enable` without options only refreshes.
- **Storage:** each integration's layout is stored in its own RexDeck ownership record, separate from Rex and the upstream tool's configuration. Changing one integration's layout rewrites only that integration's module and leaves siblings and already open panes untouched.
- **Size reference:** the share is relative to the invoking terminal, not the whole window. A pane too small for the tool's interface is the tool's own rendering concern, so pick a larger size or a different placement for small terminals.

## Tool and view settings

Each guide's **Configuration** section names the exact files, the environment variables honoured, and what RexDeck passes at launch.

| Integration | Where its settings live |
| --- | --- |
| [Files](../plugins/files/README.md#configuration) | Yazi: `$YAZI_CONFIG_HOME` if set, otherwise `$XDG_CONFIG_HOME/yazi` if set, otherwise `~/.config/yazi/` |
| [Git](../plugins/git/README.md#configuration) | lazygit: `~/Library/Application Support/lazygit/config.yml`, per-repository overrides, XDG relocation |
| [GitHub](../plugins/github/README.md#configuration) | gh-dash: `~/.config/gh-dash/config.yml` (or `GH_DASH_CONFIG`); RexDeck adds a temporary repository-scoped overlay at launch |
| [Actions](../plugins/actions/README.md#configuration) | GitHub CLI configuration and authentication; filters are pane-local |
| [Markdown](../plugins/markdown/README.md#configuration) | markdown-reader: `~/Library/Application Support/markdown-reader/config.toml` and its in-app settings |
| [Viewer](../plugins/viewer/README.md#configuration) | Yazi: `$YAZI_CONFIG_HOME` if set, otherwise `$XDG_CONFIG_HOME/yazi` if set, otherwise `~/.config/yazi/`; previewer and opener rules |
| [Slides](../plugins/slides/README.md#configuration) | presenterm: `$XDG_CONFIG_HOME/presenterm/config.yaml`, otherwise Application Support (or `PRESENTERM_CONFIG_FILE`) |
| [JSON](../plugins/json/README.md#configuration) | jless: no configuration file; display controls are interactive |
| [Logs](../plugins/logs/README.md#configuration) | lnav: JSON under `~/.config/lnav` or `~/.lnav`; edit files or use `:config` |
| [SQL](../plugins/sql/README.md#configuration) | Harlequin: TOML in the database directory, `~/Library/Application Support/harlequin/` or home; SQLite initialization in `~/.sqliterc` |
| [Tables](../plugins/tables/README.md#configuration) | VisiData: `~/Library/Preferences/visidata/config.py` or `~/.visidatarc`, or the Options sheet |
| [Dependencies](../plugins/deps/README.md#configuration) | Chosen in the pane; nothing persisted, no credentials |
| [Images](../plugins/images/README.md#configuration) | Chosen in the pane; nothing persisted; Docker configuration and credentials are not read |
| [System](../plugins/system/README.md#configuration) | bottom on macOS: `~/.config/bottom/bottom.toml` if it exists. Otherwise an existing `~/Library/Application Support/bottom/bottom.toml`, then `$XDG_CONFIG_HOME/bottom/bottom.toml` when set, otherwise the Application Support path |
| [Disk](../plugins/disk/README.md#configuration) | dua: `~/Library/Application Support/dua-cli/config.toml` |
| [Containers](../plugins/containers/README.md#configuration) | lazydocker: `~/Library/Application Support/lazydocker/config.yml` (or `CONFIG_DIR`); the current Docker context |
| [Redis](../plugins/redis/README.md#configuration) | redis-tui: `~/.config/redis-tui/config.json` (connections, TLS, preferences, keybindings) |
| [Processes](../plugins/procs/README.md#configuration) | The selected `dekit.yaml`; dekit's XDG config, data and runtime directories |
| [HTTP](../plugins/http/README.md#configuration) | Slumber: the collection YAML plus `~/Library/Application Support/slumber/config.yml` (or `SLUMBER_CONFIG_PATH`) |
| [Dashboard](../plugins/dashboard/README.md#configuration) | The selected board JSON; Dashbrew themes in `DASHBREW_THEMES_DIR` or `dashbrew/themes` |
| [ClickUp](../plugins/clickup/README.md#configuration) | RexDeck: `$XDG_CONFIG_HOME/rexdeck/clickup.json` (default `~/.config/rexdeck/clickup.json`), written by `rexdeck clickup select`; token from a named environment variable (default `CLICKUP_API_TOKEN`) |
| [Inbox](../plugins/inbox/README.md#configuration) | GitHub CLI: `GH_CONFIG_DIR`, `$XDG_CONFIG_HOME/gh` or `~/.config/gh`; filters are pane-local |
| [Usage](../plugins/usage/README.md#configuration) | tokscale: `~/.config/tokscale/settings.json` (or `TOKSCALE_CONFIG_DIR`) |
| [Focus](../plugins/focus/README.md#configuration) | pomo: a trusted local `pomo.yaml` overrides `~/.config/pomo/pomo.yaml` |
| [Music](../plugins/music/README.md#configuration) | spotify_player: `~/.config/spotify-player/` (`app.toml`, `keymap.toml`, `theme.toml`) |
| [Attention](../plugins/attention/README.md#configuration) | RexDeck: `<saved workspace>/attention/`; optional provider hooks live in your Claude Code or Codex settings |

## Credentials

RexDeck never stores tokens. Purpose-built views read credentials only from the sources each guide names:

- **GitHub views:** existing GitHub CLI authentication, either gh's stored login or the `GH_TOKEN`/`GITHUB_TOKEN` environment variables gh documents, checked against the `account=` you pass. Inbox was qualified with `GITHUB_TOKEN`. In that lab, the stored gh/macOS-keyring route returned HTTP 401, so prefer the environment route for Inbox; see the [Inbox guide](../plugins/inbox/README.md).
- **ClickUp:** a `CLICKUP_[A-Z0-9_]+` environment variable (default `CLICKUP_API_TOKEN`) that you export from your shell profile.

Hosted tools keep their own credential stores.

## Retarget a saved setup

1. Run `rexdeck disable <id>` for every applied integration, including any reported as disabled after a failed enable.
2. Keep the old workspace directory. Remove the saved `$XDG_CONFIG_HOME/rexdeck/config.json` deliberately.
3. Run `rexdeck setup --workspace <fresh-directory>`, pointing at a new, empty directory.
4. Enable the integrations you want again.

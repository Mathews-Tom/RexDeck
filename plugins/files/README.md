# Files

Launch unmodified [Yazi](https://yazi-rs.github.io/) from Rex's native palette in the invoking terminal's working directory. Files requires no LazyRex hub, SDK, renderer patch, Go build, or background service.

[Back to RexDeck](../../README.md)

## Requirements

- macOS arm64 and an existing Rex CLI/server using API revision 10.
- Python 3.12+, `uv`, and Yazi available on `PATH`. Yazi 26.9.1 is the verified local navigation baseline.
- Terminal/font support required by your Yazi configuration. RexDeck does not install tools/fonts or change their settings.

Other platforms, remote hosts, and multi-device use are not qualified. RexDeck is not published on PyPI; install from the source checkout.

## Install and set up once

From the RexDeck checkout:

```sh
uv tool install .
```

Ensure the tool executable directory reported by `uv` is on `PATH`. The installed package includes the Files action and launcher; subsequent commands work from any directory and do not require the checkout.

With your Rex application already running:

```sh
rexdeck setup
rexdeck enable files
rexdeck status
```

`setup` discovers the current Rex endpoint and uses `REX_CONFIG`, or the standard `$XDG_CONFIG_HOME/rex/init.lua` / `~/.config/rex/init.lua` configuration. It displays the target and requests approval before remembering a personal-server selection. It does not install an action, change Rex configuration, or start a missing server. `enable files` performs the owned installation and enables the native action; no separate apply/start commands or shell wrapper are needed.

For a custom target or noninteractive setup, select and approve it explicitly once:

```sh
rexdeck setup \
  --config "$HOME/.config/rex/init.lua" \
  --server "unix://$HOME/Library/Application%20Support/rex/server.sock" \
  --real-server
rexdeck enable files
```

The `--real-server` flag authorizes the personal target for subsequent explicit enable/disable commands. For an isolated server, pass its existing config and Unix socket with `--config` and `--server`, omit `--real-server`, and isolate RexDeck's XDG directories. Isolated sockets must resolve under `/tmp` or `/private/tmp`; they are not interchangeable with your application's endpoint.

Settings are stored in `$XDG_CONFIG_HOME/rexdeck/config.json` (default `~/.config/rexdeck/config.json`). Files ownership records use `$XDG_STATE_HOME/rexdeck/files` (default `~/.local/state/rexdeck/files`), remembered at setup time. There is no required `-native` suffix. Later commands use the saved target, not ambient `REX_SERVER` or changing shell variables. Malformed settings, changed config symlink targets, and replacing an existing saved target are refused rather than silently adopted. To select a different target, disable Files first, deliberately remove the saved RexDeck `config.json`, then run setup for the new target with `--workspace` pointing to a fresh directory. Preserve the old ownership workspace; its receipt remains bound to the old target even after disable.

The generated `rexdeck_files.lua` sits beside the canonical target of the selected config, including when that config is a symlink. A tracked `require` loader preserves module search path/cache state and lets Rex detect action changes. The target directory cannot contain `;` or `?`, which have special module-search meanings. Conflicting files, changed owned bytes, and old/unknown receipts are refused rather than overwritten or adopted.

### Existing native Files setup

If Files is already applied using the earlier manual commands, select its **existing** workspace during one-time setup:

```sh
rexdeck setup \
  --workspace "$HOME/.local/state/rexdeck/files-native" \
  --config "$HOME/.config/rex/init.lua" \
  --server "unix://$HOME/Library/Application%20Support/rex/server.sock" \
  --real-server
rexdeck enable files
```

Use the actual original workspace if it differs. Do not rename or discard an applied ownership directory. Setup remembers that location; subsequent commands still need no workspace argument. Package reinstall or relocation does not transfer ownership to a different config or server.

### Previous LazyRex-based Files

Do not reuse its workspace with native Files. Close the old Files/previews and use the **previous implementation's** disable command or the frozen cleanup helper supplied for that installation, with its original target and required approval. Preserve its old workspace/runtime files. If cleanup already returned `"state": "disabled"` with `"conflicts": []`, do not repeat it. Continue with a fresh native setup.

The native CLI refuses legacy receipts; it does not stop or adopt an old hub automatically.

## Use Files

From a project terminal, open Rex's native palette and choose **RexDeck Files**. A native terminal split opens Yazi in that terminal's reported working directory. Missing or invalid context fails explicitly; the installer directory, home, and later focus are never substitutes.

Use Yazi's normal navigation, previews, opening, configuration, and file operations. Up/Down already wrap from first to last entry and back in the verified upstream version. RexDeck does not replace Yazi's UI, keys, theme, plugins, previewers, or configuration. The initial directory is not a jail, and Yazi is **write-capable**, not read-only.

Quit Yazi with its normal `q` binding to return focus to the exact captured project terminal. Each deliberate launch has its own origin; no replacement-pane registry or process sweeper is involved. A missing origin, failed launch, nonzero Yazi exit, or focus failure stays visible for acknowledgement instead of silently selecting another pane. User-defined Yazi bindings retain their own behavior.

Tools run as your OS account, not in a sandbox. Tool-native opening/previewing can invoke programs configured by the user. RexDeck supplies structured argv and cwd, not a shell-interpolated command.

## Disable and update

```sh
rexdeck disable files
```

Disable removes only unchanged owned loader/module state and reloads the selected config. It preserves config symlinks/modes, unrelated edits, user tool configuration, existing Yazi panes, and data. Changed artifacts produce conflicts for judgment. Failed operations recover owned state without overwriting concurrent edits. Disable never enables the historical Files POC or touches unrelated LazyRex instances. Saved target settings remain available for the next `rexdeck enable files`.

After updating the source checkout, reinstall and refresh the integration before launching Files again:

```sh
uv tool install --reinstall .
rexdeck enable files
rexdeck status
```

Re-enabling verifies the recorded ownership before refreshing the packaged action, launcher, and interpreter paths. To remove RexDeck itself, run `rexdeck disable files` before `uv tool uninstall rexdeck`; uninstalling the Python tool alone does not remove Rex configuration.

## Development verification

Run the shared checks from the [root README](../../README.md#development), plus this plugin's Lua syntax screen:

```sh
luajit -b src/rexdeck/plugins/files/main.lua /tmp/rexdeck-files-syntax.out
```

Lua compilation is not runtime proof. Build and install the wheel outside the checkout, then exercise setup/enable/status, native action/Yazi navigation and quit focus, reinstall/refresh, and disable on a private Rex server. Owner-app palette, glyphs, and visual behavior require owner-led checking rather than a CLI-only GUI claim.

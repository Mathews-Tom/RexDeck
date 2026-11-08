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

Settings are stored in `$XDG_CONFIG_HOME/rexdeck/config.json` (default `~/.config/rexdeck/config.json`). Files ownership records use `$XDG_STATE_HOME/rexdeck/files` (default `~/.local/state/rexdeck/files`), remembered at setup time. There is no required `-native` suffix. Later commands use the saved target, not ambient `REX_SERVER` or changing shell variables. Malformed settings, changed config symlink targets, and replacing an existing saved target are refused rather than silently adopted. To select a different target, disable Files first, deliberately remove the saved RexDeck `config.json`, then run setup for the new target with a fresh ownership location. Keep the old receipt until removal and rollback safety are verified; never reuse it for another target. XDG settings here do not relocate `uv` or Rex's own configuration.

The generated module is `$XDG_DATA_HOME/rexdeck/plugins/files/rexdeck_files.lua` (default `~/.local/share/rexdeck/plugins/files/rexdeck_files.lua`). Setup resolves, validates, and saves the app data root (`…/rexdeck`) once; later commands reuse it even if ambient `XDG_DATA_HOME` changes. The uv-managed package remains the action/launcher source. Only the owned tracked `require` loader is added to the canonical Rex config target, including when the selected config is a symlink; it restores module search path/cache state and lets Rex detect action changes. The module data directory—not the config parent—cannot contain `;` or `?`, which have special module-search meanings. Symlinked or redirecting deployment subdirectories, conflicting files, and changed owned bytes are refused rather than overwritten or silently redirected to config-adjacent storage.

Setup freezes the resolved Unix socket as well as the config target; redirecting an input socket alias cannot retarget later commands. The generated module embeds the applied Lua action and Python launcher together. Updating package files does not change an already applied launcher; `rexdeck enable files` explicitly refreshes both.

The selected config must resolve to the running server's startup config, as reported by its read-only `client.keymap` API. Setting the CLI's `REX_CONFIG` alone cannot change what that server reloads. Setup and later lifecycle commands reject a mismatch or missing server config identity before owned writes; restart the intended server with the correct config before selecting it.

### Existing native Files setup

This pre-release cutover uses settings schema **2** and receipt schema **3**, binding ownership to the saved app data root. Old or unknown settings/receipts and foreign formats are refused, not automatically adopted. Reinstalling the package alone does not migrate an existing config-adjacent module.

For an existing installation, retain the old working package or rollback wheel and verify its original workspace, config, server, and unchanged owned artifacts. Use that **old implementation's** disable against its verified saved target before installing the new layout. Preserve the old settings/receipt and exact baseline as rollback evidence; deliberately reset the saved selection only after owned disable succeeds, then use the ordinary new setup/enable/status commands. Keep the same approved target and establish readiness and rollback safety before removing any verified obsolete owned state. Do not blindly delete, rename, reuse, or adopt applied ownership. This is an explicit owner operation, not a migration command or framework.

### Previous LazyRex-based Files

Do not reuse its workspace with native Files. Close the identified old Files/previews and use the **previous implementation's** disable command or approved frozen cleanup helper with its original target and required approval. If cleanup already returned `"state": "disabled"` with `"conflicts": []`, do not repeat it. Keep ownership evidence until disabled state and rollback safety are verified; obsolete owned workspace/runtime artifacts may then be removed under explicit cleanup authority. Continue with a fresh native setup, not an adopted legacy receipt.

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

Ctrl-C, SIGTERM, and SIGHUP during a lifecycle mutation run the same rollback path; further cancellation is deferred until recovery finishes. SIGKILL, power loss, and OS crashes cannot run Python recovery, and there is no durable crash-recovery journal. Retain ownership records and inspect artifacts before manual recovery after an uncatchable interruption.

After updating the source checkout, reinstall and refresh the integration before launching Files again:

```sh
uv tool install --reinstall .
rexdeck enable files
rexdeck status
```

This refresh sequence applies to the current schemas. For a pre-cutover installation, follow **Existing native Files setup** before replacing the old package.

Re-enabling verifies the recorded ownership before refreshing the packaged action, launcher, and interpreter paths. To remove RexDeck itself, run `rexdeck disable files` before `uv tool uninstall rexdeck`; uninstalling the Python tool alone does not remove Rex configuration.

## Development verification

Run the shared checks from the [root README](../../README.md#development), plus this plugin's Lua syntax screen:

```sh
luajit -b src/rexdeck/plugins/files/main.lua /tmp/rexdeck-files-syntax.out
```

Lua compilation is not runtime proof. Build and install the wheel outside the checkout, then exercise setup/enable/status, native action/Yazi navigation and quit focus, reinstall/refresh, and disable on a private Rex server. Owner-app palette, glyphs, and visual behavior require owner-led checking rather than a CLI-only GUI claim.

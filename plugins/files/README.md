# Files

Launch unmodified [Yazi](https://yazi-rs.github.io/) from Rex's native palette in the invoking terminal's working directory. Files requires no LazyRex hub, SDK, renderer patch, Go build, or background service.

[Back to RexDeck](../../README.md)

## Requirements

- macOS arm64 and an existing Rex CLI/server; API revision 10 is the established baseline, not an exact-version compatibility gate.
- Python 3.12+, `uv`, and Yazi on `PATH` at qualified version **26.9.1**. Other installed versions require manual resolution, not an implicit upgrade.
- Terminal/font support required by your Yazi configuration. RexDeck does not install fonts or change tool settings.

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

Setup/status never install tools. If Yazi is missing, `enable files` offers installation through existing qualified Homebrew **7.0.12** on macOS arm64 only (`/opt/homebrew` or a validated existing prefix at most 13 bytes), displays the resolved `brew install --formula --force-bottle homebrew/core/yazi` argv/effects, and defaults to **No**. Only a qualified core bottle without runtime dependencies or install hooks is allowed; source builds are refused. Approval permits that selected package only; progress/errors stay visible, then qualified executable identity/version and the saved target are revalidated before registration. No configuration lock is held during download. Existing tools are reused, never silently upgraded. See the root README for frozen-cache and owned-helper cancellation boundaries.

For manual/noninteractive installation, run `brew install yazi` with your existing supported Homebrew (or follow [upstream installation](https://yazi-rs.github.io/docs/installation/)), then rerun `rexdeck enable files`. Missing managers/unsupported environments receive manual guidance; RexDeck never installs Rex, Homebrew, Python or uv, uses sudo, or installs an unrelated suite. Decline/failure/cancellation leaves no partial action registration. Package-manager effects may remain after failure/cancellation; disable/uninstall never removes Yazi.

For a custom target or noninteractive setup, select and approve it explicitly once:

```sh
rexdeck setup \
  --config "$HOME/.config/rex/init.lua" \
  --server "unix://$HOME/Library/Application%20Support/rex/server.sock" \
  --real-server
rexdeck enable files
```

The `--real-server` flag authorizes the personal target for subsequent explicit enable/disable commands. For an isolated server, pass its existing config and Unix socket with `--config` and `--server`, omit `--real-server`, and isolate RexDeck's XDG directories. Isolated sockets must resolve under `/tmp` or `/private/tmp`; they are not interchangeable with your application's endpoint.

Settings are stored in `$XDG_CONFIG_HOME/rexdeck/config.json` (default `~/.config/rexdeck/config.json`). Files ownership records use `$XDG_STATE_HOME/rexdeck/files` (default `~/.local/state/rexdeck/files`), remembered at setup time. Git uses a separate `git/` subdirectory of that saved workspace. There is no required `-native` suffix. Later commands use the saved target, not ambient `REX_SERVER` or changing shell variables. Malformed settings, changed config symlink targets, and replacing an existing saved target are refused rather than silently adopted. To select a different target, disable every integration first, deliberately remove the saved RexDeck `config.json`, then run setup for the new target with a fresh ownership location. Keep old receipts until removal and rollback safety are verified; never reuse them for another target. XDG settings here do not relocate `uv` or Rex's own configuration.

The generated module is `$XDG_DATA_HOME/rexdeck/plugins/files/rexdeck_files.lua` (default `~/.local/share/rexdeck/plugins/files/rexdeck_files.lua`). Setup resolves, validates, and saves the app data root (`…/rexdeck`) once; later commands reuse it even if ambient `XDG_DATA_HOME` changes. The uv-managed package remains the action/launcher source. Only the owned tracked `require` loader is added to the canonical Rex config target, including when the selected config is a symlink; it restores module search path/cache state and lets Rex detect action changes. The module data directory—not the config parent—cannot contain `;` or `?`, which have special module-search meanings. Symlinked or redirecting deployment subdirectories, conflicting files, and changed owned bytes are refused rather than overwritten or silently redirected to config-adjacent storage.

Setup freezes the resolved Unix socket as well as the config target; redirecting an input socket alias cannot retarget later commands. The generated module embeds the applied Lua action and Python launcher together. Updating package files does not change an already applied launcher; `rexdeck enable files` explicitly refreshes both.

The selected config must resolve to the running server's startup config, as reported by its read-only `client.keymap` API. Setting the CLI's `REX_CONFIG` alone cannot change what that server reloads. Setup and later lifecycle commands reject a mismatch or missing server config identity before owned writes; restart the intended server with the correct config before selecting it.

### Existing native Files setup

Current settings schema **2** and Files receipt schema **3** remain compatible when adding Git. Retain the saved target and explicitly run `rexdeck enable files` after reinstalling to refresh packaged source/interpreter paths; no disable/reset is required for this upgrade. Git enable/disable preserves Files and open Yazi panes. Unqualified `status` reports both integrations; `status files` reports Files alone.

Old/unknown schemas, foreign receipts, config-adjacent deployments, and previous LazyRex installations are not automatically adopted. For those older layouts only, retain the old package and ownership evidence, use that implementation's disable against its verified target, and deliberately establish a fresh supported setup after removal. Do not blindly delete, rename, or reuse applied ownership.

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

Re-enabling verifies recorded ownership before refreshing the packaged action, launcher, and interpreter paths. Before `uv tool uninstall rexdeck`, run `disable` for every applied integration (`rexdeck disable files` and, when selected, `rexdeck disable git`), including integrations reported as disabled after a failed enable. A disabled action can still have an applied loader and module. Uninstalling the Python tool alone does not remove Rex configuration.

## Development verification

Run the shared checks from the [root README](../../README.md#development), plus this plugin's Lua syntax screen:

```sh
luajit -b src/rexdeck/plugins/files/main.lua /tmp/rexdeck-files-syntax.out
```

Lua compilation is not runtime proof. Build and install the wheel outside the checkout, then exercise setup/enable/status, native action/Yazi navigation and quit focus, reinstall/refresh, and disable on a private Rex server. Owner-app palette, glyphs, and visual behavior require owner-led checking rather than a CLI-only GUI claim.

Yazi is an unbundled [MIT-licensed upstream tool](https://github.com/sxyazi/yazi/blob/main/LICENSE); Rex remains an external, separately licensed prerequisite. See the root [release owner gate](../../README.md#release-preparation-and-owner-gate) for pinned source installation and publication/evidence limits.

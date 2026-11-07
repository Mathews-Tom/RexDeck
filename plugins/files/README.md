# Files

Launch unmodified [Yazi](https://yazi-rs.github.io/) from Rex's native palette in the invoking terminal's working directory. Files requires no LazyRex hub, SDK, renderer patch, Go build, or background service.

[Back to RexDeck](../../README.md)

## Requirements

- macOS arm64 and an existing Rex CLI/server using API revision 10.
- Python 3.12+, `uv`, and Yazi available on `PATH`. Yazi 26.9.1 is the verified local navigation baseline.
- Terminal/font support required by your Yazi configuration. RexDeck does not install tools/fonts or change their settings.

This is a repository-local integration, not a released installer or published package. Other platforms, remote hosts, and multi-device use are not qualified.

## Set up in your existing Rex app

Run from the RexDeck repository root. The following uses the standard macOS Rex config/socket and a persistent Files workspace in your home directory. If Rex uses a custom config or socket, set those two variables to its actual locations instead.

```sh
uv sync --locked

export RD_WORKSPACE="$HOME/.local/state/rexdeck/files-native"
export RD_CONFIG="$HOME/.config/rex/init.lua"
export RD_SERVER="unix://$HOME/Library/Application%20Support/rex/server.sock"

rd_files() {
  uv run rexdeck "$@" \
    --workspace "$RD_WORKSPACE" \
    --config "$RD_CONFIG" \
    --server "$RD_SERVER" \
    --real-server
}

rd_files inspect
```

`inspect` is read-only. Review its resolved config target, tool paths, and conflicts before continuing. The selected config and Rex server must already exist; `apply` creates the workspace. The `--real-server` flag explicitly selects your existing application, so retain it for every command against that endpoint. These are live configuration changes only when you execute the next commands:

```sh
rd_files apply && rd_files start && rd_files status
```

`apply` installs a disabled integration; `start` enables it and verifies the real native action. Reapply preserves enabled/stopped selection. Python, Rex, and Yazi paths are explicit in the owned module. No command downloads, builds, upgrades, or auto-installs a runtime.

The generated `rexdeck_files.lua` sits beside the canonical target of the selected config, including when that config is a symlink. A tracked `require` loader preserves module search path/cache state and lets Rex detect action changes. The target directory cannot contain `;` or `?`, which have special module-search meanings. Conflicting files, changed owned bytes, and old/unknown receipts are refused rather than overwritten or adopted.

For an isolated server, use its existing config/socket and a separate workspace, and omit `--real-server` from the helper. Isolated sockets must resolve under `/tmp` or `/private/tmp`. A lab socket is not interchangeable with your application's socket; RexDeck does not start a missing server.

### Fixing the old `/absolute` example

The previous README used illustrative paths such as `/absolute/rex/init.lua` and a lab socket. Copying those literally caused `No such file or directory: '/absolute'` before setup could apply changes. Re-run the variable assignments and helper definition above to replace those values, then inspect again. Do not create `/absolute` or repeat a successful legacy disable to resolve this error.

### Switching from the previous LazyRex-based Files

Do not reuse its workspace with the native helper. Close the old Files/previews, then use the **previous implementation's** disable command or the frozen cleanup helper supplied for that installation, with its original workspace/config/server and live opt-in where required. Preserve the old workspace and runtime files. If cleanup already returned `"state": "disabled"` with `"conflicts": []`, continue with the fresh native workspace above; do not run cleanup again merely because the new setup had invalid paths.

The native CLI intentionally refuses legacy receipts; it does not stop or adopt an old hub automatically.

## Use Files

From a project terminal, open Rex's native palette and choose **RexDeck Files**. A native terminal split opens Yazi in that terminal's reported working directory. Missing or invalid context fails explicitly; the installer directory, home, and later focus are never substitutes.

Use Yazi's normal navigation, previews, opening, configuration, and file operations. Up/Down already wrap from first to last entry and back in the verified upstream version. RexDeck does not replace Yazi's UI, keys, theme, plugins, previewers, or configuration. The initial directory is not a jail, and Yazi is **write-capable**, not read-only.

Quit Yazi with its normal `q` binding to return focus to the exact captured project terminal. Each deliberate launch has its own origin; no replacement-pane registry or process sweeper is involved. A missing origin, failed launch, nonzero Yazi exit, or focus failure stays visible for acknowledgement instead of silently selecting another pane. User-defined Yazi bindings retain their own behavior.

Tools run as your OS account, not in a sandbox. Tool-native opening/previewing can invoke programs configured by the user. RexDeck supplies structured argv and cwd, not a shell-interpolated command.

## Stop and remove

Using the same variables and helper:

```sh
rd_files stop
```

`stop` disables the native action and reloads configuration. It does not terminate open Yazi panes or unrelated processes. `start` explicitly re-enables it. Expected disabled status is not an error; readiness requires matching owned source/configuration and actual native registration.

To remove the integration:

```sh
rd_files disable
```

`disable` removes only unchanged owned loader/module state and reloads the selected config. It preserves config symlinks/modes, unrelated edits, user tool configuration, existing panes, and data. Changed artifacts produce conflicts for judgment. Failed operations recover owned state without overwriting concurrent edits; interrupted recovery retains the original error and conflict diagnostics. Disable never enables the historical Files POC or touches unrelated LazyRex instances.

## Development verification

Run the shared checks from the [root README](../../README.md#development), plus this plugin's Lua syntax screen:

```sh
luajit -b plugins/files/main.lua /tmp/rexdeck-files-syntax.out
```

Lua compilation is not runtime proof. Native action/Yazi behavior must also be exercised on a private Rex server; owner-app palette, glyphs, and visual behavior require owner-led checking rather than a CLI-only GUI claim.

# RexDeck

Independently selectable tools for Rex, using unchanged [LazyRex](https://github.com/lazy-rex/LazyRex). The local Files slice uses upstream home and Plugins management; it does not install the default plugin suite, agent hooks, profiles or autostart.

## Local Files

Prerequisites: macOS arm64, Rex CLI/server API revision 10, Python 3.12+, `uv`, Go 1.27.3, `bat` and `less`. The source lock pins LazyRex commit `8b9e30b6b6b63f957a2696e9027e0bbe5c6fea0f`, including matching hub, renderer, SDK and home. Other clients, platforms, remote hosts and multi-device use are not qualified. SDK `0` alone is not compatibility.

Run from this checkout. Choose an explicit workspace, Rex config and server; the helper has no implicit live configuration target:

```sh
uv sync --locked
uv run rexdeck prepare-runtime --workspace /absolute/owned/rexdeck-workspace
uv run rexdeck verify-runtime --workspace /absolute/owned/rexdeck-workspace
uv run rexdeck inspect --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
```

`prepare-runtime` explicitly verifies/downloads the locked source archive and builds the bundle. It records a build receipt only after verification. Other commands never download, upgrade or repair mismatching binaries by regenerating checksums. Keep the workspace private and outside the repository. Preserve upstream's MIT license in the prepared source.

Review `inspect` before applying. Applying to an existing live Rex configuration is an explicit user operation; campaign automation requires separate owner approval. The selected server must already exist:

Isolated runs require a Unix socket under `/tmp` or `/private/tmp`, including its resolved target. Use, for example, `--server unix:///tmp/rexdeck-lab.sock`; an arbitrary `/var/folders` socket is not supported by the unchanged renderer. For an explicitly owner-approved live application only, pass `--real-server` on every setup/lifecycle command; this propagates upstream's own live-server guard and never grants campaign approval by itself. Do not use it to bypass an isolated endpoint error.

```sh
uv run rexdeck apply --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
uv run rexdeck start --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
uv run rexdeck status --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
```

Setup seeds one managed absolute local Files source and upstream `lazyrex` home. Reapply/start preserves manager choices; `apply --reseed` explicitly restores the Files declaration. Existing/ambiguous foreign hubs are not adopted. A ready host is distinct from a registered Files instance or a deliberately stopped/removed feature. Expected stopped/disabled status is not an error.

## Interaction

From a project terminal, open the native palette and choose **LazyRex: Rexdeck Files**. **LazyRex: Plugins…** opens the unchanged manager. Tab/Shift+Tab selects; Enter opens; Esc closes the browser. Arrows scroll the upstream terminal renderer; they do not select entries.

The browser captures the invoking pane and directory. Directory names end in `/`; Parent, Refresh, project-root recovery and Return retain explicit navigation. File paths remain structured identities even when display text is clipped or control characters are escaped. Binary/non-UTF-8 content and text above 2 MiB are refused. UTF-8 code and Markdown open as source in independent `bat`/`less` previews. Large directories receive a bounded refusal rather than a partial listing; reduce the directory or navigate back.

Pager `q` returns to the captured origin. Preview errors and an unavailable origin remain visible until Enter acknowledges them. Closing the browser leaves previews running. Different origins retain independent previews. Replacement closes only a still-matching owned child-process generation; uncertain/replaced panes are left alone. Orderly restart resets browser navigation to its captured root and does not sweep independent previews.

Plugins execute as your OS user, not in a sandbox. Root confinement is a trusted-local read boundary, not protection against hostile concurrent path replacement. No file editor or file-management operation is provided.

## Stop and remove

Use **LazyRex: Plugins…** to stop/remove Files without stopping home. Local manager re-add is not supported by the pinned upstream; use explicit `apply --reseed` to restore the local declaration, then upstream Start if deliberately stopped.

```sh
uv run rexdeck stop --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
uv run rexdeck disable --workspace /absolute/owned/rexdeck-workspace --config /absolute/rex/init.lua --server unix:///tmp/rexdeck-lab.sock
```

Stop terminates only the verified owned hub. A crashed hub whose PID was reused is reported as stopped; the unrelated process is never signalled. Disable stops the owned palette writer, removes only unchanged owned integration and reloads the selected config. It preserves configuration symlinks/modes, unrelated edits, selection/state and independent previews. Changed or stale owned artifacts are reported for judgment, not overwritten. It never restores the historical Files POC.

## Validation

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
```

LuaJIT compilation screens Lua syntax only; actual Rex interaction is separate required acceptance evidence. Native GUI proof cannot be replaced by CLI invocation. Public-source installation, version-tag manager add/update/re-add, rich media, Git, agents and task providers remain later campaign outcomes; no source tag, release or package publication is authorized by this local slice.
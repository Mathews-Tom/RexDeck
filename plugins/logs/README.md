# Logs

Explore explicitly selected local logs in a native Rex pane with unmodified [lnav 0.14.1](https://github.com/tstack/lnav/releases/tag/v0.14.1): merged chronological logs, filters, SQL and time navigation. Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Preinstalled **lnav 0.14.1** on PATH. Install it yourself from the upstream release, or inspect `brew info lnav` and use `brew install lnav` only if it selects 0.14.1 and you accept Homebrew's dependency/build/upgrade effects. Verify with `lnav --version`. RexDeck never installs, upgrades or removes lnav. A missing or unqualified version prints actionable guidance and registers nothing.

```sh
rexdeck enable logs
rexdeck status logs
# Inside Rex, explicitly select a log or a directory containing plain logs:
rex do rexdeck_logs file="logs/application.log"
rex do rexdeck_logs file="logs"
```

Selection is required: an existing `.log`, `.txt` or `.out` file (case-insensitive), or a directory. Absolute paths, local `file://` URIs and paths relative to the invoking terminal are accepted. Symlinks are judged by their resolved targets. Missing paths and other suffixes are refused visibly before opening a pane. Select the containing directory for rotated names such as `application.log.0`, whose last suffix is not accepted individually. lnav opens and monitors all files in that directory, not just the three suffixes; RexDeck does not pass recursive `-r` or rotated-sibling `-R` flags.

Selected paths containing `*`, `?`, `[` or `\` are refused before launch because lnav would expand them as wildcards (a backslash acts as a glob escape: `a\b.log` opened a sibling `ab.log`) and could open other files. This applies to the entire resolved path, including parent directories; choose a path without these characters.

The resolved local path is the final, single absolute argument to lnav, with no static flags. The pane cwd is the selected directory or the selected file's parent. RexDeck provides no stdin/pipe capture, remote SSH target, URL, startup command or script argument. An input such as `user@host:/var/log/app.log` is treated as a local selection, never forwarded as a remote target. This is a path-selection policy, not content validation or a sandbox.

## Controls

These are upstream lnav controls, unchanged:

| Key / command | Action |
| --- | --- |
| `j` / `k`, Up / Down | Move through messages |
| `:filter-in ERROR`, Enter | Show messages matching the regex |
| `:disable-filter ERROR`, Enter | Disable that filter |
| `;SELECT log_level, count(*) AS messages FROM all_logs GROUP BY log_level;`, Enter | Query the currently filtered logs |
| `:goto 2026-11-10 10:04:00`, Enter | Jump to a timestamp |
| `?` | Show / leave help |
| `q` | Leave the current view; repeat as needed to quit and restore origin focus |

Qualification selected a directory containing `application.log` and `worker.log.0`, filtered their two ERROR messages, obtained `log_level=error; messages=2` through SQL, disabled the filter and jumped to 10:04. The viewer stayed usable at 40 columns and quit to the exact origin. Opening a single `.log` was also observed.

## Capabilities and limits

- **Logs can hold secrets**, including tokens, personal data and request bodies. Directory selection reads every file lnav discovers there. Select directories containing only plain logs that you intend to inspect.
- lnav follows files and persists searches, filters, positions, bookmarks, command history and configuration. With a fresh home containing `.config`, 0.14.1 uses `~/.config/lnav`; legacy `~/.lnav` is also supported. The lab observed `view-info-*.json`, `textinput-history.db` and `log_metadata.db`. Existing system/user formats, configuration and installed SQL scripts are loaded from lnav's own locations, not isolated by RexDeck.
- Captures, shell output, remote copies and extracted archives live under **`$TMPDIR/lnav-user-${UID}-work`**, including `piper/` and `archives/`. These can outlive the pane. Normal local file viewing does not capture stdin, but lnav's own commands can produce retained output.
- **No strict archive exclusion:** direct `.gz`, `.zip`, `.tar`, `.bz2`, `.xz`, `.zst` and other unlisted suffixes are refused by the suffix allowlist. However, lnav detects archives by content and automatically extracts an archive renamed to `.log`/`.txt`/`.out`, or an archive found inside a selected directory, into its work directory. Qualification observed a gzip-compressed tar renamed `disguised.log` being extracted to `archives/…/inside.log`. Do not select mixed/untrusted directories when extraction is unwanted.
- lnav is not read-only or execution-restricted: `:sh` and pipe commands execute shell commands; `|` runs lnav scripts; SQL extensions and export/configuration commands offer additional effects. Qualification ran a harmless `:sh printf …` and observed retained output. RexDeck does not invoke those features automatically.
- Network features remain available in upstream lnav: `:open` can read URLs and SSH paths; remote mode transfers a **tailer executable** to the remote machine. `-u` explicitly updates git-installed formats. These features were not used in qualification, and RexDeck passes neither remote targets nor update flags. The pinned startup source gates git-format updates on `-u`; this integration does not add an update check. Existing scripts/configuration can introduce their own network or execution behavior.
- The path is validated at invocation and again immediately before launch; lnav subsequently opens it by name. A writer controlling that directory could replace the file in between. Use directories you control.

Behavior references at the pinned tag: [input, archives and remote helper](https://github.com/tstack/lnav/blob/v0.14.1/docs/source/usage.rst), [configuration](https://github.com/tstack/lnav/blob/v0.14.1/docs/source/config.rst), [sessions](https://github.com/tstack/lnav/blob/v0.14.1/docs/source/sessions.rst), [scripts](https://github.com/tstack/lnav/blob/v0.14.1/docs/source/scripts.rst), and [startup/update flags](https://github.com/tstack/lnav/blob/v0.14.1/src/lnav.cc). The 0.14.1 help output identifies both active storage paths.

## Disable

```sh
rexdeck disable logs
```

Only Logs' owned loader, module and receipt are removed. Other integrations and existing panes stay alive; qualification kept both Files and lnav panes open while disabling Logs. lnav is BSD-2-Clause-licensed, unbundled and owner-managed.

# Tables

Explore one explicitly selected **CSV, TSV, JSON or JSONL file** in a native Rex pane using unmodified [VisiData 3.5](https://github.com/saulpw/visidata/releases/tag/v3.5). Sort, select rows and open frequency tables, then quit back to the exact invoking terminal. This is an editable, code-capable application, **not a read-only viewer or sandbox**.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Preinstalled `vd` **3.5** on PATH. The Homebrew formula is **visidata**, not `vd`: inspect `brew info visidata` and run `brew install visidata` yourself only if it selects 3.5 and you accept its dependency/build effects. Otherwise use the pinned upstream release. `vd --version` must report `saul.pw/VisiData v3.5`. RexDeck never installs, upgrades or removes VisiData. Missing or unqualified versions print installation/version-check guidance and register nothing.

```sh
rexdeck enable tables
rexdeck status tables
# From the invoking Rex terminal, explicitly select a file:
rex do rexdeck_tables file="data/report.csv"
```

`file` is required. Missing paths, directories and suffixes other than `.csv`, `.tsv`, `.json`, `.jsonl` fail before launch. Suffix matching is case-insensitive; a symlink is judged by its resolved target. Absolute paths, local `file://` URIs and paths relative to the invoking terminal are accepted. The resolved absolute file is the final argument after `--motd-url=`; cwd is its parent. JSON/JSONL shape determines the upstream sheet representation; no conversion is done by RexDeck.

## Controls

| Key | VisiData action |
| --- | --- |
| `h` / `j` / `k` / `l`, arrow keys | Move between columns/rows |
| `[` / `]` | Sort current column ascending/descending |
| `,` | Select rows matching the current cell's displayed value |
| `s` / `u` | Select/unselect current row |
| `F` (Shift+F) | Open frequency table grouped by current column |
| `q` | Close current sheet; repeat to exit all sheets |
| `Ctrl+S` | Save current sheet to a chosen path; can overwrite a file |
| `=` | Create a column from a Python expression |
| `D` / `gD` | Open current/global command log |
| `Ctrl+D` | Save command log to a replayable `.vdj` file |
| `Y` / `gY` | Copy current/selected rows to the system clipboard |

Sorting/selecting/frequency analysis does not itself save the source. Quit without issuing save commands to discard changes; VisiData does not automatically save on quit. Its default `quitguard` is false, so do not expect a confirmation. Qualification uses a 360-row categorical CSV, sorts and selects rows, opens its frequency table, resizes to 40 columns, quits and compares the file bytes unchanged.

A harmless `FileExistsError` can appear on first launch if startup threads race to create the absent VisiData data directory (`stored_list.py:16–19`); the qualified interaction remained usable and subsequent launch was clean.

## Capabilities, trust and state

- **Writes:** VisiData can edit cells/rows, save/export with `Ctrl+S`, overwrite with `save-source`/`save-sheet-really`, and run shell commands. Open only trusted data from directories you control. RexDeck rechecks the selected path before launch but the tool opens it by name; another writer could replace it in that interval.
- **System clipboard:** `Y` / `gY` copy current/selected rows through the configurable `clipboard_copy_cmd` (`pbcopy` by default on macOS), exposing copied data to the system clipboard.
- **Python/config/plugins:** `=` expression columns, `exec-python`, Python object commands and plugins execute code with your user's permissions. In 3.5 the default config is `config.py` in the VisiData config directory if it exists, otherwise `~/.visidatarc`. This file is executed as Python. Installed `visidata.plugins` entry points autoload, and the config directory plus its `plugins-deps` directory are added to Python's import path; `plugins` is imported by default. `VD_CONFIG`/`--config` and `VD_DIR`/`--visidata-dir` can override those locations. **There is no automatic cwd `.visidatarc` loading in 3.5**; a configured relative config path or user/plugin code can still load cwd files. The pane's cwd is the selected file's directory, not necessarily the invoking directory. RexDeck does not disable these upstream mechanisms.
- **Network:** network/URL/database/cloud loaders and connectors remain available (some need optional dependencies), as do network-capable config and plugin code. This action accepts a local file, never a connector URL, and passes `--motd-url=` to disable VisiData's otherwise automatic startup MOTD fetch. No network connector is used in qualification. This flag is not a network sandbox.
- **Command log/history:** current/global command logs contain replayable operations and inputs in memory. `Ctrl+D` saves them to an owner-selected path, and macros/replay can execute commands; do not open untrusted replay files. Input prompts persist values in `input_history.jsonl` in the VisiData data directory, potentially including sensitive expressions/paths. Macro metadata uses `macros.jsonl` there; saved macro command files use `visidata_dir`. Do not type secrets into prompts.
- **macOS paths in 3.5:** config/plugins default to `~/Library/Preferences/visidata`; data (including input history) to `~/Library/Application Support/visidata`; cache to `~/Library/Caches/visidata`. `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, when set, substitute their corresponding base directories, each with `/visidata` appended. `VD_DIR` changes the plugin/macro directory, not the separate data/cache directories; `VD_CONFIG` changes the executed config path. These are upstream state, not RexDeck-owned files, and disable leaves them alone.

Verified against pinned [license metadata](https://github.com/saulpw/visidata/blob/v3.5/setup.py), [config/plugin loading](https://github.com/saulpw/visidata/blob/v3.5/visidata/settings.py), [macOS/XDG path rules](https://github.com/saulpw/visidata/blob/v3.5/visidata/vendor/appdirs.py), [input history](https://github.com/saulpw/visidata/blob/v3.5/visidata/input_history.py), [persistent lists](https://github.com/saulpw/visidata/blob/v3.5/visidata/stored_list.py), [command log](https://github.com/saulpw/visidata/blob/v3.5/visidata/cmdlog.py), [Python execution](https://github.com/saulpw/visidata/blob/v3.5/visidata/pyobj.py), [saving](https://github.com/saulpw/visidata/blob/v3.5/visidata/save.py) and [MOTD](https://github.com/saulpw/visidata/blob/v3.5/visidata/motd.py).

## Disable

```sh
rexdeck disable tables
```

Only Tables' owned loader/module/receipt change. Sibling integrations and already open VisiData panes remain alive. VisiData is **GPL-3.0-only** (the pinned tag's `setup.py` declares this SPDX identifier), owner-managed and executed separately; no upstream code is vendored or bundled.

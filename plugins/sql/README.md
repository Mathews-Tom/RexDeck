# SQL

Query one explicitly selected **SQLite** database in [Harlequin 2.17.0](https://github.com/tconbeer/harlequin/releases/tag/v2.17.0), opened read-only. Quitting Harlequin returns focus to the exact terminal that invoked it.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64 and the qualified Rex server.
- Preinstalled Harlequin **2.17.0** with its bundled `sqlite` adapter (`harlequin --version` lists installed adapters). Install it yourself, for example with `brew install harlequin` after `brew info harlequin` shows 2.17.0, or `uv tool install harlequin==2.17.0`. RexDeck never installs, upgrades or removes Harlequin. A missing or different version makes `rexdeck enable sql` print this guidance and register nothing.

```sh
rexdeck enable sql
rex do rexdeck_sql file="data/app.db"
```

The `file` argument is required and must name an existing `.sqlite`, `.sqlite3` or `.db` file (case-insensitive; symlinks are judged by their target). A missing path is refused before launch, so Harlequin never creates an empty database. DuckDB and other engines are not offered by this integration. Harlequin runs as `harlequin --adapter sqlite --read-only <file>` in the file's directory.

## Controls

These are Harlequin's own keys:

| Key | Action |
| --- | --- |
| `F6` / `F2` / `F5` | Focus the data catalog / query editor / results |
| `Space` / `Enter` (catalog) | Expand or collapse a schema node |
| `Ctrl+J` or `Ctrl+Enter` | Run the query |
| `PgUp` / `PgDn` (results) | Page through results |
| `F7` / `F1` | Show the keys panel / help |
| `Ctrl+Q` | Quit and return to the invoking terminal |

Qualification browsed the schema of a fixture database (two tables, 2,500 rows), ran a joined `SELECT` returning 503 rows, paged the results, saw `delete from orders` fail with a SQLite error and the file's hash unchanged, kept working after a resize to 40 columns, and quit back to the invoking block.

## Capabilities and limits

- Only the selected file is opened read-only: writes to it fail. SQL you type (or an init script) can still `ATTACH` and create or write other local SQLite files, and `VACUUM INTO` can copy the selected database anywhere you can write.
- The sqlite adapter runs its initialization script (`~/.sqliterc`) on connect unless a Harlequin profile sets `no_init`; RexDeck's fixed arguments do not disable it.
- Harlequin reads configuration from `harlequin.toml`, `.harlequin.toml` and `pyproject.toml` (`[tool.harlequin]`) in the file's directory, then `~/Library/Application Support/harlequin/` and your home directory. Configuration can set adapter extensions and init scripts, so treat a database folder you did not create like an untrusted repository configuration.
- It keeps query history in `history.db` under its state directory (`$XDG_STATE_HOME/harlequin` when set, otherwise `~/Library/Application Support/harlequin/`) and editor buffers and catalog cache in `~/Library/Caches/harlequin/`. Queries you type are stored there.
- Results can be exported to files you choose and copied to the clipboard.

## Disable

```sh
rexdeck disable sql
```

Only SQL's owned loader, module and receipt change. Other integrations and open Harlequin panes are left as they are. Harlequin is MIT-licensed, unbundled and owner-managed.

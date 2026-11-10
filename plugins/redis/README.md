# Redis

Browse Redis keys, TTLs, values and server metrics in a native Rex pane with unmodified [redis-tui 1.0.48](https://github.com/davidbudnick/redis-tui/releases/tag/v1.0.48). Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md)

## Requirements and enable

- macOS arm64, a running Rex server, and RexDeck [set up](../../README.md#getting-started).
- Owner-installed **redis-tui 1.0.48** on a trusted absolute `PATH` entry. Download the matching binary from the [qualified release](https://github.com/davidbudnick/redis-tui/releases/tag/v1.0.48), verify its checksum and check `redis-tui --version`. RexDeck never installs, updates or removes it. Missing or other versions are refused with manual guidance before registration.
- A Redis server you are authorized to access. RexDeck does not install or start Redis.

```sh
rexdeck enable redis
rexdeck status redis
```

Placement follows the shared convention, for example `rexdeck enable redis --placement below --size 60`.

## Select a connection and use

Choose **RexDeck Redis** in the palette. RexDeck runs `redis-tui` without arguments in the invoking directory. The directory is only the pane's cwd: it does **not** select a Redis server, database or project-local configuration. The upstream connection manager opens first; nothing automatically connects to localhost.

Select a saved connection with `j`/`k` and **Enter**. The manager also offers `a` to add and `e` to edit a connection, including host, port, username, password, database, Vault and cluster choices. Configure only servers you intend to access. RexDeck does not pass passwords or other connection parameters in argv.

Controls exercised against a disposable, password-free loopback Redis server:

- `j`/`k` browses string, hash and list fixtures; the list shows types and TTLs and the preview shows the selected value.
- **Enter** opens key detail; **Esc** returns. A string detail showed its fixture value and remaining TTL in seconds.
- `i` opens server info (version, mode, uptime, memory, clients and key count); **Esc** returns.
- `m` opens the live metrics dashboard, with operations, memory, CPU, clients and network counters; **Esc** returns.
- `q` from the keys screen returns to connections; `q` from connections exits to the exact Rex origin block.
- At **40 columns**, upstream shows `Terminal too small. Resize to at least 50x15.` rather than a usable browser. Resizing back restores the interface. Keep at least 50 columns and 15 rows; a wider pane is needed for the side preview and dashboard.

## Authority, storage and limitations

This is **not read-only** and RexDeck adds no sandbox or server allowlist. Upstream can create/edit/rename/delete keys, change TTLs, bulk-delete, flush a database, import/export JSON files, execute **server-side Lua**, edit Redis runtime **CONFIG**, publish/subscribe and subscribe to keyspace events. Qualification inspected delete confirmation, Lua entry, Pub/Sub and runtime CONFIG controls without executing destructive actions. Redis ACLs, not RexDeck, constrain server authority. File and clipboard controls run with your OS account's authority.

**Automatic update check:** on every launch redis-tui sends an HTTPS GET to `https://api.github.com/repos/davidbudnick/redis-tui/releases/latest` for an update notice, with no opt-out. It may suggest `redis-tui --update`; running that command would move the installation off the qualified **1.0.48** pin. RexDeck never runs `--update`.

Connections and settings live at **`~/.config/redis-tui/config.json`**, using `HOME` rather than `XDG_CONFIG_HOME`. The same file can hold favorites, recent key names, templates and key bindings. Version 1.0.48's [config implementation](https://github.com/davidbudnick/redis-tui/blob/v1.0.48/internal/db/config.go) writes JSON with mode 0600 and **strips Redis passwords, SSH passwords and SSH passphrases when saving**; form passwords remain in process memory, not a persistent password/keychain store. A password you manually put in the JSON is plaintext and can be read on load; do not assume saving sanitizes an existing file immediately. Existing file permissions are not necessarily tightened by a write. Value history is in-memory, while recent/favorite key names can persist. RexDeck does not read, copy, sanitize or isolate this configuration or credentials.

**Legacy migration is not a sanitized save:** upstream copies `~/.redis/config.json` as-is to `~/.config/redis-tui/config.json`, including any plaintext `password` and `ssh_config.passphrase` fields. Treat both files as credential-bearing.

Vault-configured connections may fetch credentials from HashiCorp Vault at `VAULT_ADDR`, using credentials from `VAULT_TOKEN`, then `~/.vault-token`, or the external `token_helper` program configured in `~/.vault`. This can execute that helper with your OS account's authority. No Vault account or credentials were used in qualification. Never put Redis passwords in shell arguments; use the upstream password form or an appropriately secured upstream credential setup.

**TLS works through upstream CLI flags or JSON configuration only**, not the add/edit connection form. Because this integration supplies no flags, configure `use_tls` and `tls_config` (CA/client certificate and key paths) in upstream's JSON before selecting that saved connection. Do not disable certificate verification. TLS and authenticated/cluster/Vault connections were not exercised in the password-free qualification.

**SSH tunneling is incomplete upstream:** `use_ssh`/`ssh_config` are reserved fields, not an implemented SSH transport. Do not rely on them to secure a remote connection. **Watch mode is also incomplete:** `w` in key detail reports watching, but does not automatically refresh the value. In qualification a changed list stayed stale until `r` refreshed it. These limitations are documented in the [pinned upstream README](https://github.com/davidbudnick/redis-tui/blob/v1.0.48/README.md#coming-soon--roadmap).

## Disable

```sh
rexdeck disable redis
```

Only Redis's owned loader/module/receipt is removed. Qualification left both an open Redis pane and a sibling Files pane alive. The upstream executable, configuration, credentials, server data and other plugins remain unchanged.

redis-tui is an unbundled, owner-managed MIT-licensed tool. The only Redis endpoint used in qualification was a disposable loopback server with persistence disabled. This does not mean the application is network-isolated: the automatic GitHub update check described above also runs on launch.

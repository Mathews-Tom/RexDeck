# ClickUp local registry format

The vendored MIT engine retains its YAML registry grammar. RexDeck removes the source skill's committed workspace registry: each owner selects account/workspace/list explicitly, then invokes `sync` in the pane to discover metadata. No registry or credentials belong in the package or repository. Sync is never automatic.

The frontend's registry is `$XDG_CONFIG_HOME/rexdeck/clickup/IDENTITY_SCOPE/list-LIST_ID.yaml` (the configuration root defaults to `~/.config`). `IDENTITY_SCOPE` is derived from account/workspace IDs. The rebuildable SQLite metadata cache is independently scoped by account/workspace/list beneath `$XDG_CACHE_HOME/rexdeck/clickup/` (default cache root `~/.cache`). Registry writes are atomic mode-0600 replacements. Selection switches never reuse another identity's registry/cache.

## Schema version 1

This seed-only example is synthetic; it is not a usable workspace configuration:

```yaml
schema_version: 1
digest: null
synced_at: null
workspace:
  team_id: '200001'
seeds:
  space:
    id: '300001'
    name: Sample space
  list:
    id: '400001'
    name: Sample list
  me:
    id: '100001'
discovered:
  spaces: []
  folders: []
  lists: []
  members: []
  custom_fields: {}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Supported document version; unsupported versions fail |
| `digest` | SHA-256 of canonical normalized metadata, excluding `digest` and `synced_at`; null before first write |
| `synced_at` | UTC timestamp when topology last changed or first authenticated sync completed; null means unsynced |
| `workspace.team_id` | Explicit workspace identity |
| `seeds.space`, `seeds.list` | Explicit discovery entry points (`id`, `name`) |
| `seeds.me.id` | Explicit authenticated account; resolves `me` |
| `discovered` | Space-scoped hierarchy, workspace members and list-specific field definitions from explicit sync |

Discovered records:

- `spaces`: `{id, name}`.
- `folders`: `{id, name, space_id}`.
- `lists`: `{id, name, space_id, folder_id}`; `folder_id` may be null.
- `members`: `{id, username}`; no email addresses.
- `custom_fields`: list-ID mapping to `{id, name, type, options}` records. Each option has `{id, name, orderindex}`; the latter may be null and supports API responses that encode dropdown values as positions rather than UUIDs.

Empty collections are valid. Tasks, task descriptions, comments, attachments, emails and credentials are excluded. Registry data is still private metadata: it may contain hierarchy names/usernames and should not be published.

## Digest and write rule

Normalize collection order by stable IDs: spaces/folders/lists/members by `id`, field mappings by list ID, each field and option by `id`. Duplicate IDs fail. Remove `digest` and `synced_at`; serialize with `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; SHA-256 the UTF-8 bytes. Names are not stable identifiers and never decide collection order.

Sync writes when the new digest differs **or** the previous `synced_at` is null. Either path stamps UTC time. An unchanged authenticated topology does not rewrite the file merely to update a clock. A first sync with legitimately empty discovered metadata still writes: empty success must not remain indistinguishable from “never synced”.

`registry/unsynced` means the sync timestamp is absent. `registry/stale` means recomputed metadata does not match its stamped digest; it is not an age-based TTL. `registry/unresolved` and `registry/ambiguous` require an explicit ID or fresh explicit sync. No other read/write verb silently repairs or syncs a registry.

## Programmatic seed setup

`registry.seed_registry(account_id=..., team_id=..., space_id=..., space_name=..., list_id=..., list_name=...)` constructs unsynced metadata without writing. `registry.bootstrap(..., path=...)` explicitly persists unsynced seeds and refuses an existing path. `registry.registry_path(account_id=..., team_id=...)` produces the scoped default. A `runtime.Session` may inject the selected registry, registry/cache paths and credential environment name; injected topology remains authoritative rather than silently discovering another selection.

The pane validates authenticated account/workspace/list before discovery, and checks the loaded registry's account/workspace/seed-list before engine reads or writes. Direct engine CLI users must choose their own explicit `--registry-path`; no workspace defaults are shipped.

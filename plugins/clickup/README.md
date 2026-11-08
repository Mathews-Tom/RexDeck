# ClickUp

New in 0.2.0. Python 3.12+, Rex, httpx and PyYAML are required; the Python dependencies are installed with RexDeck. This integration owns its frontend and vendors the owner's MIT ClickUp engine. Other integrations still host unmodified upstream tools. It is **write-capable**, not a read-only wrapper or a sandbox.

## Select and enable

Use an authorized personal-token environment reference inherited by the **Rex server and pane**, not merely the setup shell. Configure that environment through an existing secret-management/launch mechanism. Never paste a token into argv, a file, terminal history or source. RexDeck neither obtains nor revokes credentials.

```sh
rexdeck clickup select --account USER_ID --team WORKSPACE_ID --list LIST_ID
rexdeck clickup status
rexdeck setup
rexdeck enable clickup --placement right --size 50
```

`--credential-env NAME` changes the variable **name**, default `CLICKUP_API_TOKEN`; names must match `CLICKUP_[A-Z0-9_]+`. Selection stores only numeric account/workspace/list IDs and this reference, atomically in mode-0600 `$XDG_CONFIG_HOME/rexdeck/clickup.json`. Selection makes no API request. Authenticated identity/list are checked before rows appear. Browser openers omit ClickUp credential references.

Invoke **RexDeck ClickUp** from the native palette. The shared host runs its installed interpreter as `python -I -m rexdeck.clickup`, preserves placement/size, and returns to the exact invoking terminal on quit. No external ClickUp executable or upstream-version probe is needed. The invoking directory is not an account selection.

## Reads and navigation

- `next` / `prev`: display pages of 20 loaded rows. Task API pages include closed tasks, subtasks and tasks in multiple lists; duplicate IDs are merged. Missing `last_page` metadata triggers another-page probing until an empty result. A 50-page safety ceiling is an explicit error, never a silently complete prefix.
- `filter TEXT`: case-insensitive **full-list** filtering over task names, native statuses, internal/custom IDs. `filter` clears it. This is not workspace search.
- `select INTERNAL_ID` / `select CUSTOM_ID`: inspect a loaded-list member. Custom lookups include `custom_task_ids=true` and selected `team_id`; canonical identity must match. Description, native status name/type/color, home list and update time are retained without inventing generic status mappings.
- Comments load through all older pages with paired `start`/`start_id` cursors, deduplication and loop detection; threaded replies are displayed indented.
- `open`: send the selected task's canonical `https://app.clickup.com/t/INTERNAL_ID` link as one argv element to the OS browser opener, without a shell. Never automatic. `BROWSER` may name one absolute executable, not a command template. Loading/stale views cannot open links.
- `refresh`: reload the complete list and selected detail/comment history. Old data is never presented as fresh after failure.
- `sync`: explicitly discover hierarchy, members and per-list custom-field definitions in the selected space; write an identity/workspace/list-scoped local registry. This changes local metadata only, not ClickUp. It is required before engine projections or writes; it never happens silently.
- `tasks`, `task`, `sprint`, `search TEXT`: expose the engine's task table, selected-task projection, native-status rollup and task-only workspace search. Search fetches the registry's space-scoped task pages and matches names locally; it does not search documents, dashboards or chats. Engine diagnostics remain visible. See [verb reference](references/verbs.md).
- `switch ACCOUNT WORKSPACE LIST [ENV_NAME]`: pane-local selection, without rewriting saved settings or globally switching accounts. Clears rows, detail, comments, link, proposal and terminal scrollback. Generation tags discard late results. Different accounts use separately inherited environment references.
- `quit` / `q`: leave the pane and return to its exact captured Rex origin.

## Deliberate writes

Select a fresh task and run `sync` first. The following commands **propose**, but do not apply, one engine mutation:

- `status EXACT-NATIVE-STATUS`: use an exact status from the task's home list, not an invented workflow category.
- `assignee ID/USERNAME/me`: replace the task's assignee set with the resolved member, as the engine's touch contract specifies.
- `due YYYY-MM-DD`: set the due date.
- `description MARKDOWN`: replace the whole task description with one line of Markdown. The proposal shows the current Markdown it replaces.
- `points NUMBER`: set native Sprint Points (a finite number ≥ 0). Requires the workspace's Sprint Points ClickApp, and ClickUp only accepts values on the workspace's configured point scale: a value outside it is refused (`REJECTED`, HTTP 400 `ITEM_223`) and nothing is applied. The task detail shows `points=` after refresh.
- `field NAME=VALUE`: set a supported list-specific custom field. Type/option validation comes from the engine; unsupported field types are refused.
- `comment TEXT`: append one plain-text comment; `notify_all` is false.

The pane displays exact selection/task/diff, field concurrency digest and a random-nonce-bound proposal code. Apply once using `confirm CANONICAL-TASK-ID PROPOSAL-CODE`. The code hashes selection, task, action, value, full diff, field digest and nonce: replacing a proposal invalidates older approvals. The engine independently rechecks field/thread concurrency. Selection/refresh/cancel/errors invalidate proposals. Every write rechecks approval after pacing, immediately before send. Refresh is refused while writing so its outcome remains visible.

After a write, the list, task and complete comments are refreshed. There are no hidden write retries. A transport failure may occur after the server applied a write: the consumed confirmation is not replayable, and the pane requires an explicit refresh before a new proposal. An already dispatched request cannot be recalled by switching or quitting. No create/delete/move/time-tracking/webhook UI is included.

## Storage, errors and privacy

The [local registry](references/registry-format.md) contains hierarchy/member/custom-field metadata, not task content or credentials. The frontend scopes it by account, workspace and selected list under `$XDG_CONFIG_HOME/rexdeck/clickup/`; the rebuildable SQLite metadata cache is separately scoped under `$XDG_CACHE_HOME/rexdeck/clickup/`. Neither belongs in source control. Sync is explicit; an unsynced or digest-mismatched registry is refused.

States distinguish loading/writing/ready/empty/stale, missing credentials, authentication, permission, not-found, rate-limited, network, malformed responses, refused requests (HTTP 400, shown as `REJECTED` with only ClickUp's short `ECODE`), account mismatch and registry/write diagnostics. Refresh failures retain data only as stale; identity/auth/access failures clear it. HTTP 429 exposes a numeric `Retry-After` hint when supplied. Requests use a fixed HTTPS API-v2 origin, reject arbitrary origins/path traversal/redirects and are paced across workers. No response/error body or credential is logged; credential echoes are redacted and remote terminal controls stripped.

Proposal/engine-read errors preserve a fresh list as ready/empty; failed list refreshes remain stale. Empty/relative XDG cache roots are refused; cache directories are 0700 and files 0600. Labels/IDs are single-line; pane and engine CLI text remove terminal commands, bidi overrides and zero-width formatting.

`rexdeck disable clickup` removes only owned registration/deployment. It does not close panes, remove local selection/registry/cache, revoke credentials or mutate tasks. Re-enable after package changes that alter descriptor/host identity. Explicitly remove unwanted local metadata yourself.

## Verification

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest -q
```

Mocked contract tests cover engine reads/writes, all mutation paths, dry-run/digest guards, paired comment pages/replies, exact confirmation, selection expiry, stale-write rejection, no retries, HTTP errors, safe openers and native interpreter qualification. Live proof requires the installed wheel in private Rex and explicit owner authorization for any disposable-task mutations. Fixtures do not prove a second real account, and no real custom-field write is authorized by the tests.

API references: [authentication](https://developer.clickup.com/docs/authentication), [tasks](https://developer.clickup.com/reference/gettasks), [task/custom IDs](https://developer.clickup.com/reference/gettask), [comments](https://developer.clickup.com/reference/gettaskcomments), [list metadata](https://developer.clickup.com/reference/getlist).

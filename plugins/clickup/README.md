# ClickUp

Python 3.12+, Rex, Textual, httpx and PyYAML are required; the Python dependencies are installed with RexDeck. This integration owns its terminal frontend and vendors the owner's MIT ClickUp engine. Other integrations still host unmodified upstream tools. It is **write-capable**, not a read-only wrapper or a sandbox.

## Select and enable

Export an authorized personal token from your shell profile, for example `export CLICKUP_API_TOKEN=<token>` in `~/.zshrc`. A Rex server started by the app does not run your shell's startup files, so when the pane's environment lacks the token, the pane asks your interactive login shell (`$SHELL -i -l`) for that one exported variable; the value travels over a pipe, never argv or disk. If your shell does not export it either, the pane asks you to paste the token (hidden input) and uses it only in that pane; nothing is saved. `rexdeck enable clickup` reminds you when the token is not exported in the enabling shell. Never paste a token into argv, a file, terminal history or source. RexDeck neither obtains nor revokes credentials.

```sh
rexdeck setup
rexdeck enable clickup --placement right --size 50
```

Then open **RexDeck ClickUp** from the palette. On first run, with no saved selection, the pane walks you through it: your account is read from the token, then you choose a workspace, a space and a list from numbered menus (type a number, or text to filter; `q` cancels). A workspace, space or list that is the only one available is chosen automatically and shown. The pane saves the choice after you confirm it and opens the list.

Run `rexdeck clickup select` in a terminal to choose again later, or pass every ID for scripts:

```sh
rexdeck clickup select                                   # guided, same menus
rexdeck clickup select --account USER_ID --team WORKSPACE_ID --list LIST_ID
rexdeck clickup select --space SPACE_ID                   # use saved account/team/list
rexdeck clickup select --account USER_ID --team WORKSPACE_ID --list LIST_ID --space SPACE_ID
rexdeck clickup status
```

`--credential-env NAME` changes the variable **name**, default `CLICKUP_API_TOKEN`; names must match `CLICKUP_[A-Z0-9_]+`. Selection stores only numeric account/workspace/list IDs and this reference, atomically in mode-0600 `$XDG_CONFIG_HOME/rexdeck/clickup.json`. Guided selection makes read-only requests (user, workspaces, unarchived spaces, folders and lists) and saves only after confirmation; ending input or `Ctrl-C` saves nothing. Passing all three IDs makes no API request; passing only some is refused. Without a terminal, guided selection is refused rather than guessed. Authenticated identity/list are checked before rows appear. Browser openers omit ClickUp credential references.

Invoke **RexDeck ClickUp** from the native palette. The shared host runs its installed interpreter as `python -I -m rexdeck.clickup`, preserves placement/size, and returns to the exact invoking terminal on quit. No external ClickUp executable or upstream-version probe is needed. The invoking directory is not an account selection.

## Task desk

The pane is an interactive task browser, not a scrolling command transcript. A selectable queue sits beside a Markdown task reader when there is enough width. Narrow panes show the queue or the selected task; `Esc` returns to the queue. Descriptions and threaded comments scroll inside the reader. Resizing keeps the current task and filter.

### Space workspace view

Save a default space with `clickup select --space SPACE_ID` (after selecting the account/workspace/list), or use the **Space** toolbar/action-menu dialog. A saved space opens the aggregate view on the next launch. The dialog switches between the unchanged list desk and one selected space. Only a successful Space load updates the saved default space, and only when the pane still uses the saved account/workspace/list/credential reference; advanced pane-local selections never overwrite that saved scope. Filters and pane-local mode are not persisted. Use `clickup select --space ''` to clear the default and launch the list desk next time. Old selection files without `space` remain valid; unknown selection keys are refused.

Space mode starts with **assignee `me` AND updated within `14d`**. The account is verified through `/user` before its ID is sent as the assignee. The Space dialog accepts `me`, `any` or a numeric member ID and lookbacks `1d`, `7d`, `14d`, `30d`, `any`. These server filters reset every launch. The existing text/Mine/Closed controls further narrow the returned rows locally. Only Space mode adds a List column; list-mode columns and narrow-width allocation are unchanged.

Reads use the team task endpoint with one `space_ids[]`, paged with the existing 50-page ceiling and one-second request spacing. Updated cutoffs are epoch milliseconds. Refresh is explicit; HTTP 429 shows Retry-After without retrying, and refresh recovers after access/network/credential problems are resolved.

Writes still use only the existing proposal/review workflow. Select a task, then **Sync registry** to sync that task's own list before proposing a write; engine reports likewise use the selected task's list (or the saved list without a selection). Membership checks require the home list to belong to the selected space and workspace, status choices come from that list, and space-mode approval digests additionally bind the home-list ID. A detected move refuses the proposal/apply and requires refresh. List-mode approval digests retain their previous shape.

If a successful write removes a task from the current server-filtered queue (for example by reassigning it), the desk reads the task back by ID and checks its home space before reporting **applied; no longer matches the current filters**. An individual task outside the selected space is a recoverable scope error, not a revocation of access to the whole queue.

| Key | Action |
| --- | --- |
| `↑` / `↓`, `j` / `k` | Move through tasks |
| `Enter` | Open the highlighted task |
| `/` | Focus the current-list filter |
| `l` | Browse workspaces, spaces, folders and lists |
| `a` | Open task actions, engine reads, metadata sync and advanced selection |
| `r` | Refresh the complete list and selected task |
| `o` | Open the selected task's validated ClickUp link |
| `m` | Toggle tasks assigned to the authenticated account |
| `c` | Toggle closed tasks |
| `Esc` | Cancel a dialog or return to the queue |
| `?` | Show help |
| `q` | Quit and return to the exact captured Rex origin |

Typing in a filter or editor does not invoke single-letter shortcuts. The footer shows contextual essentials; help and the action menu expose less frequent operations.

### Reads and scope

- The filter matches the complete loaded list, not just visible rows, using task names, native statuses and internal/custom IDs. This is not workspace search.
- Task API pages include closed tasks, subtasks and tasks in multiple lists; duplicate IDs are merged. Missing `last_page` metadata triggers another-page probing until an empty result. The 50-page safety ceiling is an explicit error, never a silently complete prefix.
- Task details retain native status names and colours, home list, update time, Sprint Points, assignees, due date and priority when present. Missing metadata stays unset. Comments load all older pages with paired `start`/`start_id` cursors, deduplication and loop detection; authors, dates and indented replies stay with the conversation.
- The hierarchy picker browses every accessible space in the chosen workspace and includes foldered and folderless lists. Same-named folders remain separate. Selecting a list saves that choice for the next pane. Cancel keeps the current selection and stops subsequent discovery reads; an already dispatched request may finish. Use the Space dialog for aggregation instead of the per-list picker.
- Advanced selection retains pane-local account/workspace/list/environment-reference switching without globally switching accounts. A selection change clears previous rows, detail, comments, opener and proposal; generation tags discard late results.
- Browser opening uses only the selected task's canonical `https://app.clickup.com/t/INTERNAL_ID` link, passed as one argv element to the OS opener without a shell. It is never automatic. `BROWSER` may name one absolute executable, not a command template. Loading/stale views cannot open links, and rendered Markdown does not bypass this checked opener.
- Refresh reloads the complete list and selected detail/comment history, retaining the selected identity when it remains present. Failures never present retained data as fresh.

The action menu exposes **Sync registry**, **Task report**, **Selected task report**, **Sprint report** and **Search** in scrollable result views. Sync explicitly discovers hierarchy, members and per-list custom-field definitions in the selected space and writes an identity/workspace/list-scoped local registry. It changes local metadata only, not ClickUp; it is required before engine projections or writes and never runs silently. Engine search fetches the registry's space-scoped task pages and matches names locally; it does not search documents, dashboards or chats. See the [engine verb reference](references/verbs.md).

## Deliberate writes

Select a fresh task and explicitly sync metadata first. The action menu offers:

- **Status**: choose an exact native status from the task's home list, not a generic workflow category.
- **Assignee**: replace the task's assignee set using a member ID, username or `me`, matching the engine's touch contract.
- **Due date**: enter `YYYY-MM-DD` as a local calendar day. Date filters and date custom fields use the same process-local timezone; displayed timestamps include its zone abbreviation.
- **Description**: edit replacement Markdown; the review includes the current Markdown it replaces.
- **Sprint Points**: enter a finite number ≥ 0. The workspace must have the Sprint Points ClickApp, and ClickUp accepts only its configured point scale. Values outside that scale are refused (`REJECTED`, HTTP 400 `ITEM_223`) without applying a change.
- **Custom field**: enter `NAME=VALUE`. The engine validates supported list-specific types/options and refuses unsupported types.
- **Comment**: append plain text; `notify_all` is false.

Submitting an editor **proposes** a change; it does not apply it. The review dialog shows the exact account/workspace/list/task and field diff. **Cancel** is initially focused. **Apply** submits only the exact proposal displayed, including its random-nonce-bound approval code and field concurrency digest. The code binds selection, task, action, value, full diff, digest and nonce: replacing a proposal invalidates old approvals. The engine independently rechecks field/thread concurrency. Selection, refresh, cancellation and errors invalidate proposals. Every write rechecks approval after pacing, immediately before send.

**Existing engine limitation:** custom-field approval binds the field name and current value, not the field UUID. If a task moves lists between review and apply, a same-named destination field with the same current value can pass the check and receive the update. This interface change does not repair that pre-existing race.

Loading, stale and writing states disable inappropriate actions. Refresh is refused while a write is dispatched so its outcome remains visible. After a write, the list, task and complete comments are refreshed. There are no hidden write retries. A transport failure may occur after the server applied a write: the consumed confirmation is not replayable, and the pane requires an explicit refresh before a new proposal. An already dispatched request cannot be recalled by quitting. No create/delete/move/time-tracking/webhook UI is included.

## Storage, errors and privacy

The [local registry](references/registry-format.md) contains hierarchy/member/custom-field metadata, not task content or credentials. The frontend scopes it by account, workspace and selected list under `$XDG_CONFIG_HOME/rexdeck/clickup/`; the rebuildable SQLite metadata cache is separately scoped under `$XDG_CACHE_HOME/rexdeck/clickup/`. Neither belongs in source control. Sync is explicit; an unsynced or digest-mismatched registry is refused.

States distinguish loading/writing/ready/empty/stale, missing credentials, authentication, permission, not-found, rate-limited, network, malformed responses, refused requests (HTTP 400, shown as `REJECTED` with only ClickUp's short `ECODE`), account mismatch and registry/write diagnostics. Refresh failures retain data only as stale; identity, authentication and current-scope access failures clear it. An access failure during account preflight also clears retained task data; a permission failure in a different workspace leaves the current list intact. HTTP 429 exposes a numeric `Retry-After` hint when supplied. Requests use a fixed HTTPS API-v2 origin, reject arbitrary origins/path traversal/redirects and are paced across workers. No response/error body or credential is logged; credential echoes are redacted and remote terminal controls stripped.

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

# ClickUp engine verbs

The Rex pane uses these vendored MIT engine operations directly; it does not parse CLI presentation text to recover tasks or comments. The pane's `sync`, `tasks`, `task`, `sprint`, `search`, `status`, `assignee`, `due`, `field` and `comment` commands are described in the [integration guide](../README.md). Token-budget analytics from the source skill are not part of RexDeck.

The lower-level engine also retains its module interface:

```sh
python -I -m rexdeck.clickup.engine VERB [FLAGS]
```

This interface is write-capable. Direct writes require `--expect-digest` unless `--dry-run` is selected; unpinned writes are refused before reads or mutation. Unlike the pane, it has no interactive confirmation screen. Stage first and apply the exact reviewed digest. Authentication is inherited `CLICKUP_API_TOKEN`, never a token flag/file.

## Global options

| Option | Meaning |
| --- | --- |
| `--registry-path PATH` | **Required for every verb.** Explicit local registry from pane sync or engine bootstrap, never committed workspace data. Sync reads/writes this same path. |
| `--json` | Receipt only on stdout; human text/diagnostics on stderr |
| `--quiet` | Suppress human stderr output |
| `--no-cache` | Resolve CLI metadata live rather than through SQLite; injected pane sessions remain selection-bound |
| `--max-rows N` | Printed-row ceiling, default 50; truncation is explicitly diagnosed |

Task pagination has a separate 50-page safety ceiling. Reaching it without completion is an error, not a successful truncated listing. Top-level comment history uses paired date/ID cursors with deduplication and loop detection; task detail also fetches threaded replies.

## Reads

| Verb | Options and scope |
| --- | --- |
| `sync` | Walk hierarchy from explicit seeds, workspace members and per-list custom fields. `--write-registry` writes local metadata; otherwise report only. No remote mutation. |
| `tasks` | Task table; `--list ID/NAME`, `--assignee ID/USERNAME/me`, repeatable `--status NAME`, `--due-before YYYY-MM-DD`. Defaults to seed list. |
| `task ID` | One task plus full comment/reply tree; `--no-comments` omits history. |
| `sprint LIST` | Native-status counts, blocked and unassigned items. “Blocked” means status/tag text contains `block`, not dependency inspection. |
| `search TEXT` | Fetch all task pages scoped to registry spaces and match task names locally. Repeatable `--type`; only `task` matches. Documents, whiteboards, dashboards, attachments and chats are not searched; `search/tasks-only` makes this explicit. |

Names resolve through the selected registry. Ambiguity requires an explicit ID. `me` resolves to the seed account ID. Engine projections may be concise; the pane's raw task/detail presentation remains the inspection surface.

## Writes

Write receipts contain canonical `target`, `field_digest` and field-level `diff`. `--dry-run` sends nothing. Otherwise `--expect-digest HEX` is required and refuses changed remote values before applying. No automatic write retries.

- `touch ID --status NAME`: set native status.
- `touch ID --assignee ID/USERNAME/me`: replace the assignee set; displaced members are explicitly removed.
- `touch ID --due YYYY-MM-DD`: set due date.
- `touch ID --field NAME=VALUE`: repeatable custom-field sets. Supported types are checkbox, currency, date, drop_down, email, number, phone, short_text, text and url. Unsupported types, ambiguous names and unknown dropdown options are refused.
- `comment ID --text TEXT`: append one comment with `notify_all=false`. The digest covers the complete top-level conversation membership, not only its first page.

```sh
python -I -m rexdeck.clickup.engine touch TASK_ID --status 'native status' --dry-run --registry-path PATH --json
# Review mutation.target, mutation.diff and mutation.field_digest before applying:
python -I -m rexdeck.clickup.engine touch TASK_ID --status 'native status' --expect-digest REVIEWED_DIGEST --registry-path PATH --json
```

The TUI consumes `confirm TASK_ID PROPOSAL-CODE` once. Its nonce-bound code covers selection, task, action, value, full diff and field digest; approval is rechecked after pacing immediately before send. Results refresh afterward; refresh is refused while writing. A network error can leave the result uncertain: do not replay blindly. Multi-field touch is not a server transaction. Pane/CLI share terminal/Unicode sanitization.

## Verdicts

Exit 0 means no error-severity finding, 1 means blocking/operational failure, and 2 means usage failure. Receipts retain registry/argument digests, row/truncation counts, diagnostics and optional mutation details. Fixed-origin transport rejects path traversal and redirects, redacts credential echoes, and paces request starts across clients. Neither the pane nor engine is an authorization sandbox.

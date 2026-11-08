# Native attention

[Back to RexDeck](../../README.md)

An on-demand **native Rex extension**, not an upstream TUI integration. After `rexdeck setup`, use the installed CLI from any terminal. No `enable attention` command, descriptor, palette pane, dependency installation, hooks or background service is needed.

```sh
rex -S unix:///your/rex.sock ls --json
rex -S unix:///your/rex.sock session inspect session:EXACT-ID --json
rexdeck attention observe --session session:EXACT-ID --block block:EXACT-ID --project my-project
rexdeck attention list --project my-project
rexdeck attention list --session session:EXACT-ID --block block:EXACT-ID
rexdeck attention observe OBSERVATION-ID   # explicitly refresh a retained row
rexdeck attention jump OBSERVATION-ID
```

`observe` reads only the explicitly selected session/block on the setup-saved endpoint. Session labels, titles, executable names, cwd and similar text are never used to choose a target. `--project` is an explicit operator label, not discovered repository identity; omit it to carry the label from the newest successful earlier observation of that exact endpoint/session/block, even across server incarnations. Filters are exact matches. The opaque observation ID comes from `observe` or `list`, not a worker name or OSC record ID. Use **list → observe ID → jump ID** to inspect retained evidence, refresh it on demand and then navigate. Always jump with the ID that `observe` returns: if the server restarted and restored the same session/block, the refresh creates a new ID and the old one is reported as `replaced`. An ID cannot be combined with `--session`/`--block`. Refresh never searches for another block if the captured one is absent.

## Evidence and distinctions

- `records` retains bounded fields from the native `com.superlogical.terminal.program_status` result: OSC 7501 `id`, `app`, `state`, `kind`, plain-text message/title, owner and server-clock `updated_at` when present. These are **program reports**, not verified provider session identities. Unknown/native future states remain visible (subject to the limits below). An empty record list means no reported state, not idle or completed.
- `last_exit` comes from `com.superlogical.terminal.process`: null does not establish an agent is running, and an exit code describes the terminal's own program, not every worker it launched. A reported `done` and an actual terminal exit remain separate evidence.
- `observed_at` is the local successful-read time. `list` never contacts Rex; after 60 seconds it reports `freshness: stale`, even if the terminal still exists. A recent read is not proof that an old OSC report is fresh: inspect its separate `updated_at` too. Observations are a bounded last-seen snapshot, not an event log; transitions between reads are not captured.
- `binding` is the last observed placement: `bound` for a placed block, `unbound` for a detached one. Closure/disconnection/replacement does not rewrite that placement evidence. `target_state: present` records that existence observation. A subsequently absent block/session becomes `connection: closed` and `target_state: closed` on explicit re-observation, retaining the last records, placement, exit result and successful timestamp. Failed reads are `unknown`, not inferred closure. Server connection failures are `disconnected` but preserve the last `target_state`, so known closure is not lost. Observing any new server incarnation marks **every** retained row on that endpoint from older incarnations `replaced` and clears obsolete errors, even when the requested block is absent; it never adopts them.
- `jump` checks the saved endpoint, captured server instance, exact session/block presence, placement and captured block host before calling `session.focus_block`. Closed, detached, disconnected and replaced targets fail without another-pane fallback. Stale local evidence does not prevent a jump if the live exact binding revalidates. Focus changes that session's active window/block; it does not switch a desktop application's selected server/session, inject input or resume an agent.

## Identity boundary

`agent_identity` is always `unknown`: an OSC record ID is scoped to a terminal's current reports and is reusable. Rex exposes no durable provider conversation/session identifier or provider lifecycle history through these APIs. This view identifies **observed terminal bindings**, not Claude/Codex/omp conversations. Provider-specific identity and parentage require separately qualified provider emitters/APIs in D11. No provider inference, transcript indexing, cancellation or message injection is included. Tokscale is usage data, not an identity source; abtop is excluded.

The native API supplies real reported state and exact binding directly. [LazyRex](https://github.com/lazy-rex/LazyRex#platform-services-for-plugins) offers agent identity through provider hooks and an always-running hub, plus worker control and messaging; that broader service is unnecessary for this bounded native view and is not installed.

## Storage and capabilities

Observations live under the setup-owned workspace at `<saved workspace>/attention/attention.json` (normally `$XDG_STATE_HOME/rexdeck/files/attention/attention.json`), with lock and temporary files in that same `attention/` directory. Workspace identity is validated even for local `list`. Storage and locks must be regular non-symlink files; nonblocking, no-follow opens reject FIFOs and redirected files. Writes are locked, atomically replaced, fsynced (file and directory) and mode 0600. Limits are 256 observed terminal bindings and 2 MiB; reaching either fails without silently pruning observations. Each row is limited to 16 KiB: untrusted strings are capped (`msg` 1024 characters, `title`/record ID 256, app names/owner names 128, `kind`/timestamp 64, state 32), error text to 1024 characters, and excess records are omitted to meet the serialized byte limit. `truncated: true` discloses any such clipping/omission; unknown future record fields are omitted with that marker. One hostile terminal therefore cannot monopolize the whole store. Only the latest explicit snapshot per endpoint instance/session/block is retained. To deliberately reset retained attention data, remove that exact `attention.json` when no attention command is running; this does not affect integration receipts.

The CLI reads native status messages (which may contain sensitive task names), IDs, report owner PID/name, block host and terminal exit codes; it does not capture terminal screens, read transcripts or inspect unrelated OS processes. Only `jump` changes focus. No paid calls, network provider requests, hooks, watcher, autostart or upstream tool configuration changes are made. Disabling a hosted integration does not erase attention observations; uninstalling RexDeck leaves this explicitly retained local evidence in place.

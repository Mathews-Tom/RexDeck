# Native attention and provider subagents

[Back to RexDeck](../../README.md)

An on-demand **native Rex extension**, not an upstream TUI integration. After `rexdeck setup`, use the installed CLI from any terminal. Native observations need no `enable attention` command, descriptor, palette pane, dependency installation, hooks or background service. Optional, owner-installed hooks below add provider-reported subagent metadata.

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

Native rows keep `agent_identity: unknown`: an OSC record ID is scoped to a terminal's current reports and is reusable. Rex exposes no durable provider conversation/session identifier or provider lifecycle history through these APIs. Native rows identify **observed terminal bindings**, not Claude/Codex/omp conversations. The optional provider hooks below retain separately sourced parent/child identifiers, never infer them from native records, process trees, names, cwd or titles. No transcript indexing, cancellation, messaging or input injection is included. Tokscale is usage data, not an identity source; abtop is excluded.

The native API supplies real reported state and exact binding directly. [LazyRex](https://github.com/lazy-rex/LazyRex#platform-services-for-plugins) offers agent identity through provider hooks and an always-running hub, plus worker control and messaging; that broader service is unnecessary for this bounded native view and is not installed.

## Storage and capabilities

Native observations live under the setup-owned workspace at `<saved workspace>/attention/attention.json` (normally `$XDG_STATE_HOME/rexdeck/files/attention/attention.json`), with lock and temporary files in that same `attention/` directory. The native schema stays **version 1**, with its original independent limits of **256 terminal bindings / 2 MiB / 16 KiB per row**. Provider receipts use a separate `providers.json` and `providers.lock`, independently bounded to **64 provider rows / 256 KiB / 16 KiB per row**; they never occupy native row or byte capacity. Both stores validate workspace identity even for local `list`, require regular non-symlink files with nonblocking/no-follow opens, and use mode 0600, locks, atomic replacement and file/directory fsync. Native report strings remain capped (`msg` 1024, title/record ID 256, app/owner names 128, kind/timestamp 64, state 32, error 1024 characters), and excess records are omitted with `truncated: true`; unknown native fields are omitted with that marker. Only the latest explicit native snapshot per endpoint instance/session/block is retained. To deliberately reset native evidence, remove only `attention.json` when no native attention writer is running; this does not remove provider evidence or integration receipts.

The native observation commands read status messages (which may contain sensitive task names), IDs, report owner PID/name, block host and terminal exit codes; they do not capture terminal screens, read transcripts or inspect unrelated OS processes. Only `jump` changes focus. No paid calls, network provider requests, hook installation, watcher, autostart or upstream tool configuration changes are made. Disabling a hosted integration does not erase attention observations; uninstalling RexDeck leaves this explicitly retained local evidence in place.

## Optional provider hooks (D11: implement and hold for owner)

**Script-qualified, not provider-event-qualified.** Piped documented-shape payloads exercise the installed receiver and native navigation in an isolated Rex. No live provider hook installation, model call or real subagent event is claimed. The owner must install/review these entries and run one real subagent per provider before live qualification.

Install a RexDeck build containing `attention hook` and complete `rexdeck setup` first. Resolve its stable absolute executable path with `command -v rexdeck`. In the JSON below, replace `/absolute/path/to/rexdeck` with that path (keep the escaped quotes). Merge only these entries into existing `hooks` arrays; do not replace unrelated settings/hooks. Hook processes must inherit the same `XDG_CONFIG_HOME` as setup, or its default, to find the approved RexDeck target. Start the parent provider inside the intended Rex terminal: the qualified private Rex supplied `REX_SESSION` and `REX_BLOCK`, but **not `REX_SERVER`**. No manual endpoint export is required: RexDeck verifies those exact IDs on the setup-saved endpoint before binding; a supplied, different `REX_SERVER` vetoes binding. With no inherited IDs, the row is explicitly unbound.

### Claude Code

Owner-only change: add these two groups under `hooks` in `~/.claude/settings.json` for user-wide scope, or `.claude/settings.local.json` for local-project scope. Use one scope, not both.

```json
{
  "hooks": {
    "SubagentStart": [
      {
        "matcher": ".+",
        "hooks": [
          {
            "type": "command",
            "command": "\"/absolute/path/to/rexdeck\" attention hook claude-code",
            "async": true,
            "timeout": 2
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": ".+",
        "hooks": [
          {
            "type": "command",
            "command": "\"/absolute/path/to/rexdeck\" attention hook claude-code",
            "async": true,
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

Review the commands in Claude Code's `/hooks` UI and restart the parent session if needed to load the settings. For an isolated owner trial, use `CLAUDE_CONFIG_DIR` pointing to a fresh directory, or a dedicated `--settings` file; RexDeck never changes either. Start also fires on resume. **SubagentStop also fires for internal agents, including prompt suggestions and `/btw`**, not just explicit user-created subagents. The shown `.+` matcher excludes empty `agent_type` values, but internal events can still have a nonempty type (for example a parent session's `--agent` name) and therefore still match. Omit that matcher only if empty-type events are intentionally wanted; the receiver preserves them without inferring identity. An unmatched/absent start is not invented. These are reported events, not proof of an independent process or permanent termination.

**First real-run owner check:** confirm that Claude's emitted `session_id` is the intended parent conversation and that start/stop `agent_id` formats and values match for the same real child. The documentation examples use differing-looking IDs, so piped samples do not establish that cross-event correlation. Until this live check is recorded, provider-event parentage remains held for owner, even though the receiver preserves the supplied identifiers exactly.

The [official hook reference](https://code.claude.com/docs/en/hooks.md#subagentstart) documents stdin JSON and exit/output controls. Silent exit 0 makes no decision or context injection; async execution prevents waiting on telemetry. RexDeck never emits a block/continuation decision.

Claude does **not** enforce `timeout` for async hooks. Its two-second setting is not a safety guarantee: the receiver's own one-second deadline is the guard, and async configuration prevents waiting on it. Codex documents a timeout for background command hooks; the Codex snippet's two-second timeout is an additional outer guard.

Rollback: remove only the two handlers whose command ends in `attention hook claude-code` (and any now-empty groups), reload/restart the provider, allow in-flight hooks to finish, then run `rexdeck attention clear --provider claude-code`. This deletes only Claude rows, preserving native observations and Codex rows. Do not delete unrelated settings or transcripts.

### Codex CLI

Owner-only change: merge this into `~/.codex/hooks.json` for user-wide scope, or `<repo>/.codex/hooks.json` for a trusted project layer. Prefer one representation per layer; do not duplicate it in inline `config.toml` hooks.

```json
{
  "hooks": {
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"/absolute/path/to/rexdeck\" attention hook codex",
            "async": true,
            "timeout": 2
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"/absolute/path/to/rexdeck\" attention hook codex",
            "async": true,
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

The [official hook reference](https://developers.openai.com/codex/hooks.md) currently says hooks are enabled by default. If explicitly disabled, owner-enable the existing feature key in the corresponding `config.toml` (merge into an existing `[features]` table, never duplicate it):

```toml
[features]
hooks = true
```

Open **`/hooks` and review/trust the exact definitions**; changed hashes require fresh trust and untrusted hooks are skipped. Project-local config must also be trusted. Do not bypass hook trust. Managed policy can disable hooks and must not be overridden. For an isolated owner trial use a fresh `CODEX_HOME`; no live config or credentials are copied by RexDeck.

Codex explicitly defines `session_id` on these events as the **parent** session ID; `agent_id`/`agent_type` identify the reported child. Nullable `last_assistant_message`/transcript paths become presence booleans only. `turn_id`, model, permission mode and all other fields are discarded. The reference's common-output section defines exit 0 with no output as success (despite the event section requiring JSON when returning output). Async execution continues without waiting, and RexDeck returns no informational output or continuation control.

Rollback: remove only the two `attention hook codex` handlers, reload/restart and review `/hooks`, let in-flight hooks finish, then run `rexdeck attention clear --provider codex`. Restore the prior `features.hooks` value only if this change introduced it and no other hooks require it. Preserve other hook definitions/trust and native/Claude rows.

### Retained parentage, filters and navigation

```sh
rexdeck attention list --provider claude-code
rexdeck attention list --provider codex --parent EXACT-PARENT-SESSION-ID
rexdeck attention list --agent EXACT-AGENT-ID
rexdeck attention list --session session:EXACT-ID --block block:EXACT-ID
rexdeck attention jump hook:EXACT-OBSERVATION-HASH
```

`rows` includes native and provider rows by default. Provider rows preserve exact `parent_session_id`, `agent_id`, and `agent_type`; IDs are namespaced by provider and parent. `parents` groups matching children using only the provider-supplied parent ID: it makes **no claim about the parent's lifecycle**. Filters are exact; `--project` is a native operator label and does not infer provider projects from cwd.

Each provider/parent/child has one latest-received snapshot, not a durable event log. `event` is `SubagentStart`/`SubagentStop`; `state` is `started`/`stopped` with `outcome: unknown`. Neither provider supplies a failure field here, so stopped does not mean success/failure, permanent completion, or a dead process. Async delivery can be delayed/reordered; no provider timestamp is invented. `observed_at` is the local receipt time; the same 60-second freshness rule is about receipt age, not liveness. An older receipt finishing its binding lookup cannot overwrite a newer receipt. No heartbeat/watcher runs.

The hook records inherited `rex_server` (null when absent), Rex `session_id`/`block_id`, and only captures server `instance_id`/block `host_id` after a receipt-time, 350 ms bounded lookup proves that the **exact inherited session contains the exact placed block on the setup-saved endpoint**. This is exact-ID verification, never label/name/cwd matching or discovery of a replacement. When `REX_SERVER` is present it must match setup; absence is not a reason to require an owner export. `binding_reason` is `verified`, `missing-identifiers`, `endpoint-mismatch`, `verification-failed`, `verification-timeout`, `exact-block-absent` or `detached`. Failed/timed-out checks retain an unbound row and reason; no target is fabricated. The bound block is the **parent's terminal inherited by the hook**, not an independent child pane. `jump` uses D10's exact endpoint/incarnation/session/block/host checks and never falls back to another pane. Provider rows cannot be refreshed with native `observe ID`: only a new provider hook receipt updates their lifecycle.

An **explicitly empty `REX_SERVER` intentionally records `endpoint-mismatch`** and stays unbound: presence is a veto unless it matches the approved endpoint. This differs from an absent variable, which permits exact session/block verification on the saved endpoint. Empty `REX_SESSION` or `REX_BLOCK` records `missing-identifiers`.

### Privacy, bounded failures and schema

Provider rows retain only provider/event, parent/child IDs, agent type, cwd, local receipt time, three presence flags (`last_message_present`, `transcript_present`, `agent_transcript_present`), inherited Rex IDs and verified endpoint/incarnation/host binding with a bounded reason code, plus derived state/outcome. Presence means a string was supplied, including an empty string; null/missing is false. **No message content or transcript path is retained and no transcript is opened.** No session-directory scan, process discovery, provider request, credential copying or live provider-config modification occurs. IDs and cwd can still be sensitive; both stores remain mode 0600.

The receiver accepts up to **2 MiB** of stdin JSON, so a long `last_assistant_message` need not discard an otherwise valid stop receipt. If the parse cap is exceeded it drains remaining stdin toward EOF in bounded chunks within the overall deadline, then drops that oversized receipt. IDs/type are capped at 1024 characters and cwd/binding fields at 4096, without identifier truncation. Binding verification is limited to 350 ms; lock acquisition uses the **remaining one-second receiver deadline minus a 50 ms save margin**, rather than a fixed short contention timeout. Malformed/oversized input, missing setup, exhausted lock budget, storage limits, bad files and overall timeout fail silently with exit 0, no stdout/stderr or provider control decisions. Binding failures/timeouts normally retain an unbound reason, including empty inherited REX values; other dropped receipts are not recovered. Provider storage has its own **64-row / 256 KiB / 16 KiB-per-row** limits, separate from native **256-row / 2 MiB** capacity. Full provider stores refuse new receipts without eviction or pruning, though existing IDs can still update if the byte bound permits; `attention clear --provider claude-code|codex` frees that provider's retained space without affecting native/sibling rows. Claude async timeout is not enforced; the receiver deadline is its guard. Codex's two-second async timeout is an additional outer guard.

Native `attention.json` stays on its original **schema 1** with no migration, extra stored row-type field or native capacity reduction. Provider `providers.json` is a distinct **version-1 `kind: provider-subagents` schema**, separately validated and bounded; `row_type: provider` identifies its rows. `list` derives `row_type: native` for native output without changing native bytes. Native/provider locks are **never held simultaneously**: listing reads native then provider storage, releasing each lock before the next; jump chooses exactly one store by the authoritative ID namespace, clear locks only provider storage, and native observe never opens provider storage. Thus a full or malformed provider store cannot block native observe. Removing hooks and clearing provider rows needs no native downgrade or backup restore; older D10 builds can still read the unchanged native file.

For a `hook:` jump, the row is read into a private in-memory snapshot under `providers.lock`, then that lock is released **before all Rex revalidation/focus calls**. Hook receipts can therefore update while navigation is in progress. The captured binding is still checked against the live endpoint, incarnation, exact session/block and host before focus.

### Provider-store faults and explicit recovery

If `providers.json` or its lock is malformed, oversized, redirected or unreadable, `attention list` still returns healthy native rows and reports `state: fault`, `provider_state: fault` and a bounded `provider_error`; the CLI exits nonzero rather than hiding the problem. `attention clear --provider …` reports the same structured provider fault and does not silently reset or claim rows were removed. Native-store faults still fail normally.

To deliberately recover, stop all provider hooks and wait for in-flight hooks and attention commands to finish, then remove **only `<saved workspace>/attention/providers.json`**. The next hook/clear recreates the separate store. This discards evidence for **all providers**, not just the one requested by a failed clear, but preserves native `attention.json` and integration receipts. If the fault is specifically a redirected/invalid `providers.lock`, inspect that exact entry and remove it only after **all attention readers/writers are stopped**; removing a lock while another process holds it is unsafe. Remove a symlink entry itself, never its target. Do not delete `attention.json`, the shared attention directory or unrelated files.

### omp: missing interface

No omp hook entry or fabricated provider row is installed. A public, stable CLI hook carrying subagent parent/child IDs and start/stop events is missing; RPC-mode `subagent_lifecycle` is not an equivalent interface for normal interactive sessions. omp remains eligible only for the native, provider-identity-unknown view until that interface is available and separately qualified.

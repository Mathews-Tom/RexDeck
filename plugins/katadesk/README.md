# Kata Desk

Read the issues of the [kata](https://github.com/kenn-io/kata) project bound to the invoking directory, and the attention requests addressed to you across every project, in a RexDeck-owned view in a native Rex pane. Start an [omp](https://omp.sh) agent on an open issue in a split, a new window or a background window, watch its state live, jump to it when it waits for you and stop it. The list updates live while the view is open. Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and [RexDeck setup](../../README.md#getting-started).
- Owner-installed **kata** on a trusted absolute `PATH` entry when you run `rexdeck enable katadesk`; enable refuses without it. Kata Desk reads kata's JSON output and was qualified with [kata 0.23.0](https://github.com/kenn-io/kata/releases/tag/v0.23.0). Enable records the absolute path, and the pane starts with that directory first on `PATH`, so the app-started Rex server's minimal `PATH` does not hide it. Re-run `rexdeck enable katadesk` after moving kata.
- kata's daemon must already be running (`kata daemon start`). Kata Desk never starts it.
- Owner-installed **omp** on a trusted absolute `PATH` entry when you run `rexdeck enable katadesk`; enable refuses without it and prints where to get it. Enable records its path the same way as kata's. Agents use your own omp configuration: its default model, provider credentials and approval mode.
- A kata project bound to the invoking directory (`kata init`) for the Issues list. Without one, Kata Desk opens on Attention.

## Enable and open

```sh
rexdeck setup   # once, if not already done
rexdeck enable katadesk
rexdeck status katadesk
```

Open **RexDeck Kata Desk** from Rex's command palette, or `rex do rexdeck_katadesk` in a Rex terminal inside the kata-bound directory.

Kata Desk is independently selectable. Default placement is right, 50%; the shared `--placement` and `--size` options apply.

## Use

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move; the selected issue's detail loads beside the list |
| `Enter` | Load the selected issue's detail again |
| `1` | Issues: the bound project's issues, children nested under their parents |
| `2` | Attention: requests addressed to you, across every project on the daemon |
| `s` | Switch Issues between open and closed |
| `a` | Start an omp agent on the selected open issue; then `s` split beside the desk, `w` new window or `b` background window |
| `g` | Go to the selected issue's agent block |
| `n` | Go to the agent that most recently began waiting for you |
| `x` | Stop the selected issue's agent after confirmation |
| `r` | Read everything again |
| `q` | Quit and restore invoking-terminal focus; asks first while agents run |

The header names the project, the daemon kind and whose attention requests are shown. The status line shows the state (`READY`, `LOADING`, `STOPPED`, `VERSION`, `MISSING`, `FAILED`), the view, and `live` while kata's event stream runs without errors, otherwise the time of the last successful read.

- **Issues** lists ID, priority, status (with `blocked` when an open issue blocks it), title, owner and last update. The detail shows the body as Markdown, links (parent, children, blocking), children and comments.
- **Attention** lists the requests kata holds for the recipient: `KATA_INBOX_USER` from the Rex server's environment when set, otherwise the actor `kata whoami` reports. kata itself supplies no default recipient. The detail of a request shows the issue it was raised on, in whichever project.
- **Live updates:** while the view is open it follows `kata events --tail` for the bound project and reads again shortly after each event. Issues created, commented on or closed from another terminal or by an agent appear without pressing `r`.

### Agents

`a` starts omp in a new Rex block, in the desk's invoking directory, with `KATA_REF` set to the issue and a first message describing kata's agent loop for it:

1. claim it with `kata claim <ref> --ttl 2h` and renew the claim while working;
2. comment findings and how they were verified;
3. close it with `kata close <ref> --done -m … --test "<command>"` (or `--commit <sha>`) only once verified, since kata requires evidence for a done close;
4. otherwise set `work.attention=needs-human` with `work.attention_msg`, and stop.

The agent asks you in its block whenever a decision is needed. omp is started without a shell: `python -I -m rexdeck.katadesk.agent` validates the issue ref, drops inherited `KATA_REF`, `KATA_INBOX_USER` and `KATA_TEAMMATE` so the agent writes to kata as your own actor, and then replaces itself with `omp @<prompt file>`. The agent's block keeps kata's and omp's directories first on `PATH`.

- **Placement:** a split beside the desk, a new window labelled `kata <ref>`, or the background: one unfocused window labelled `Kata agents`, which later background agents join. Each appears in Rex's window list.
- **Badge:** the Agent column shows the state omp reports to Rex (OSC 7501 through Rex's program status): `working`, `waiting for you`, `done`, `idle`, `error`, or `exited <code>` once omp exits. The block stays open after omp exits until you stop it. Without any report the badge reads `state unknown`, for example with omp's `terminal.programStatus` setting off. Badges update from one `rex events --session` stream while the desk is open; `r` reads them again.
- **Identity:** an agent is the Rex server instance, session, block and host IDs recorded when its block was placed. Titles, labels and cwd never identify one. `g`, `n` and `x` revalidate all four first and refuse, without touching Rex, when the server was replaced or the block is gone or changed host.
- **Refusals:** `a` refuses a closed issue (checked again in kata just before starting), an issue that already has a running agent, and starts nothing when the desk cannot verify its own Rex block on RexDeck's saved endpoint. The header then reads `agents off` and `a` shows why.
- **Quit and reopen:** quitting while agents run lists each one (issue, placement, state) and asks first; `n` keeps the desk open. Agents keep running after the desk quits. Reopening the desk lists them again; agents whose block was closed elsewhere are dropped.

Controls exercised in the private Rex qualification lab, against a disposable project with a parent, a child, a blocking issue, a comment and one attention request:

- The list showed the parent as `P1` and `open · blocked`, its child nested under it, and the blocker. The parent's detail showed its Markdown body, links, child and comment.
- `2` listed the request with its sender and message; its detail showed the parent issue.
- An issue created and the blocker closed from a second terminal appeared and disappeared without `r`, and the parent lost its `blocked` mark.
- With kata's daemon stopped, `r` showed `STOPPED` with kata's own message and the `kata daemon start` hint; the daemon was still stopped 8 seconds later. After starting it, `r` returned to `READY · live`.
- From a directory with no kata binding the view opened on Attention with "Run kata init here to bind a project"; `1` explained that Attention works without one.
- At 40 columns the list and detail stack vertically.
- No `kata events` process remained 5 seconds after quitting or after closing the pane. `q` restored focus to the exact origin.

Agents were qualified with omp 19.2.1 against a local stub model with zero hosted model calls. Bash required approval, and the stub's first turn asked to run `kata claim "$KATA_REF" --ttl 2h`:

- Split, window and background agents each ran `omp @<prompt file>` with `KATA_REF` set to their issue in the invoking directory (`ps eww`). After approval in the agent's block, `kata show` listed the lab actor as owner with a two-hour expiry. The background agent opened an unfocused `Kata agents` window and the desk kept focus.
- Without refresh, badges went `waiting for you` while omp asked to run `kata claim`, then `working`, then `done` when the turn ended. With omp's `terminal.programStatus` off, the agent's badge read `state unknown`.
- `n` focused the waiting agent's exact block during the approval step. `g` focused the background agent in its window.
- Stop asked first and closed only that agent's block; the desk, the origin and the other agents' blocks remained.
- A closed issue, a desk started without `REX_SESSION`/`REX_BLOCK`, and agents recorded before a Rex server restart were each refused with a visible reason. No Rex block changed. `rexdeck enable katadesk` without omp on `PATH` printed install guidance and registered nothing.
- Quit while agents ran listed each with issue, placement and state; `n` kept the desk open. After quitting, the agents kept running. Reopening listed them again, showed an agent whose omp exited as `exited 143` until Stop closed its block, and dropped an agent whose block was closed in Rex.
- No `rex events` process remained 5 seconds after quitting or after closing the desk's pane. After `kill -9` of the desk, it ended when the pane closed.

## Configuration

### Pane layout

Use `rexdeck enable katadesk --placement below --size 60` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80. Default: right, 50%. Below 100 columns the list and detail stack vertically.

### kata settings

Kata Desk uses kata's own daemon selection, credentials and project binding: `<KATA_HOME>/config.toml` (default `~/.kata/config.toml`) and the workspace's `.kata.toml` and `.kata.local.toml`, read by the `kata` commands it runs. `KATA_INBOX_USER` selects the attention recipient. These come from the Rex server's environment, not your shell.

Agents run with omp's own configuration under the Rex server's `HOME`. RexDeck passes omp no model, approval or provider flags.

RexDeck passes no project, daemon, actor or credential flags and writes no configuration. It stores the agents it started and their prompt files under RexDeck's workspace in `katadesk/state/` (mode 0600, at most 32 agents), and nothing else. The launch environment is inherited from the Rex server, with relative `PATH` entries removed and kata's and omp's enable-time directories first.

## Capabilities and limits

**The desk itself never writes to kata.** It runs only `kata daemon locate`, `whoami`, `list`, `show` and `inbox` with `--json`, plus one `kata events --tail --json` while it is open. Agents are different.

**Agents are write-capable and autonomous.** omp runs with your omp configuration and its tool approvals, writes to kata as your actor, edits the working directory as you allow it, and runs until it exits or you stop it, also after the desk quits. Stop closes only that agent's block, after confirmation. Kata Desk never stops an agent on its own.

**kata's daemon is never started by Kata Desk.** Every refresh and every issue detail first asks `kata daemon locate`, which does not start a daemon, and a stopped daemon is reported without running any other command. Other kata commands start a stopped local daemon by themselves, so if the daemon stops in the moment between the probe and a read, that read can still start it.

**Both event streams live only as long as the view.** `kata events --tail` and `rex events --session` run in the pane's process group: quitting the view stops them, and closing the pane ends them through the terminal's hangup. When the daemon goes away, kata's stream keeps retrying instead of exiting; Kata Desk drops `live` on its first error, probes the daemon again, and stops the stream once the daemon reports stopped. If the view itself is killed (for example `kill -9`), the streams keep running until the pane closes. The pane then waits at "Press Enter to close RexDeck", which may not respond after the killed view left the terminal in raw mode; close the pane instead.

Issue titles, bodies, comments and requests are shown as kata returns them, with terminal control sequences removed, and stay in memory only. Links in Markdown are not opened.

**Network:** none by RexDeck. omp reaches the model provider your omp configuration names. kata's daemon sends anonymous PostHog telemetry by default; opt out with `KATA_TELEMETRY_ENABLED=0` or `DO_NOT_TRACK=1` in the Rex server's environment before the daemon starts. A configured remote kata daemon is reached by kata with its own credentials; remote daemons were not qualified.

Qualification used an isolated lab HOME with its own `KATA_HOME`, a kata daemon with telemetry turned off, and a private Rex endpoint. The lab was removed afterwards.

## Disable

```sh
rexdeck disable katadesk
```

Only Kata Desk's owned registration and module change. In qualification, open Kata Desk, Kata and System panes all survived `rexdeck disable katadesk`, Kata and System stayed enabled, and `rex do rexdeck_katadesk` then reported no such action. kata, its daemon and its data remain untouched. Running agents keep running, and the agent records in `katadesk/state/` stay, so enabling and opening the desk again lists them.

Kata Desk is part of RexDeck and [MIT licensed](../../LICENSE). kata is an unbundled, owner-managed [MIT-licensed upstream tool](https://github.com/kenn-io/kata/blob/v0.23.0/LICENSE).

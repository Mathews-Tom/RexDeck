# Kata Desk

Read the issues of the [kata](https://github.com/kenn-io/kata) project bound to the invoking directory, and the attention requests addressed to you across every project, in a RexDeck-owned view in a native Rex pane. The list updates live while the view is open. Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and [RexDeck setup](../../README.md#getting-started).
- Owner-installed **kata** on a trusted absolute `PATH` entry when you run `rexdeck enable katadesk`; enable refuses without it. Kata Desk reads kata's JSON output and was qualified with [kata 0.23.0](https://github.com/kenn-io/kata/releases/tag/v0.23.0). Enable records the absolute path, and the pane starts with that directory first on `PATH`, so the app-started Rex server's minimal `PATH` does not hide it. Re-run `rexdeck enable katadesk` after moving kata.
- kata's daemon must already be running (`kata daemon start`). Kata Desk never starts it.
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
| `r` | Read everything again |
| `q` | Quit and restore invoking-terminal focus |

The header names the project, the daemon kind and whose attention requests are shown. The status line shows the state (`READY`, `LOADING`, `STOPPED`, `UNBOUND`, `FAILED`, `VERSION`), the view, and `live` while kata's event stream is connected, otherwise the time of the last successful read.

- **Issues** lists ID, priority, status (with `blocked` when an open issue blocks it), title, owner and last update. The detail shows the body as Markdown, links (parent, children, blocking), children and comments.
- **Attention** lists the requests kata holds for the recipient: `KATA_INBOX_USER` from the Rex server's environment when set, otherwise the actor `kata whoami` reports. kata itself supplies no default recipient. The detail of a request shows the issue it was raised on, in whichever project.
- **Live updates:** while the view is open it follows `kata events --tail` for the bound project and reads again shortly after each event. Issues created, commented on or closed from another terminal or by an agent appear without pressing `r`.

Controls exercised in the private Rex qualification lab, against a disposable project with a parent, a child, a blocking issue, a comment and one attention request:

- The list showed the parent as `P1` and `open · blocked`, its child nested under it, and the blocker. The parent's detail showed its Markdown body, links, child and comment.
- `2` listed the request with its sender and message; its detail showed the parent issue.
- An issue created and the blocker closed from a second terminal appeared and disappeared without `r`, and the parent lost its `blocked` mark.
- With kata's daemon stopped, `r` showed `STOPPED` with kata's own message and the `kata daemon start` hint; the daemon was still stopped 8 seconds later. After starting it, `r` returned to `READY · live`.
- From a directory with no kata binding the view opened on Attention with "Run kata init here to bind a project"; `1` explained that Attention works without one.
- At 40 columns the list and detail stack vertically.
- No `kata events` process remained 5 seconds after quitting or after closing the pane. `q` restored focus to the exact origin.

## Configuration

### Pane layout

Use `rexdeck enable katadesk --placement below --size 60` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80. Default: right, 50%. Below 100 columns the list and detail stack vertically.

### kata settings

Kata Desk uses kata's own daemon selection, credentials and project binding: `<KATA_HOME>/config.toml` (default `~/.kata/config.toml`) and the workspace's `.kata.toml` and `.kata.local.toml`, read by the `kata` commands it runs. `KATA_INBOX_USER` selects the attention recipient. These come from the Rex server's environment, not your shell.

RexDeck passes no project, daemon, actor or credential flags, writes no configuration and stores nothing. The launch environment is inherited from the Rex server, with relative `PATH` entries removed and kata's enable-time directory first.

## Capabilities and limits

**Read-only.** Kata Desk runs only `kata daemon locate`, `whoami`, `list`, `show` and `inbox` with `--json`, plus one `kata events --tail --json` while it is open. It makes no kata writes; use `kata` or the [Kata](../kata/README.md) integration to change issues.

**kata's daemon is never started by Kata Desk.** Every read first asks `kata daemon locate`, which does not start a daemon, and a stopped daemon is reported without running any other command. Issue details load only while the daemon answered. Other kata commands start a stopped local daemon by themselves, so if the daemon stops in the moment between the probe and a read, that read can still start it.

**The event stream lives only as long as the view.** It runs in the pane's process group: quitting the view stops it, and closing the pane ends it through the terminal's hangup. If the view itself is killed (for example `kill -9`), the stream keeps running until the pane closes. The pane then waits at "Press Enter to close RexDeck", which may not respond after the killed view left the terminal in raw mode; close the pane instead.

Issue titles, bodies, comments and requests are shown as kata returns them, with terminal control sequences removed, and stay in memory only. Links in Markdown are not opened.

**Network:** none by RexDeck. kata's daemon sends anonymous PostHog telemetry by default; opt out with `KATA_TELEMETRY_ENABLED=0` or `DO_NOT_TRACK=1` in the Rex server's environment before the daemon starts. A configured remote kata daemon is reached by kata with its own credentials; remote daemons were not qualified.

Qualification used an isolated lab HOME with its own `KATA_HOME`, a kata daemon with telemetry turned off, and a private Rex endpoint. The lab was removed afterwards.

## Disable

```sh
rexdeck disable katadesk
```

Only Kata Desk's owned registration and module change. In qualification, open Kata Desk, Kata and System panes all survived `rexdeck disable katadesk`, Kata and System stayed enabled, and `rex do rexdeck_katadesk` then reported no such action. kata, its daemon and its data remain untouched.

Kata Desk is part of RexDeck and [MIT licensed](../../LICENSE). kata is an unbundled, owner-managed [MIT-licensed upstream tool](https://github.com/kenn-io/kata/blob/v0.23.0/LICENSE).

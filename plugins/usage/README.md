# Usage

Watch AI token usage, models, sessions and projects in a native Rex pane with unmodified [tokscale](https://github.com/junhoyeo/tokscale). The pane runs tokscale's normal online TUI for local omp sessions, and you refresh it with tokscale's own controls. Quitting returns focus to the exact terminal you invoked it from.

[Back to RexDeck](../../README.md)

## Requirements

- macOS arm64, a running Rex server, and RexDeck set up as described in the [root README](../../README.md#getting-started).
- tokscale **4.21.0** on a trusted absolute `PATH` entry. Install it yourself, for example with `npm install -g tokscale@4.21.0`, and check `tokscale --version`. RexDeck never installs, upgrades or removes it. Other versions are refused with manual guidance until they are qualified.

## Enable

```sh
rexdeck enable usage
rexdeck status usage
```

Usage is independent of the other integrations. Pane placement and size follow the shared convention, for example `rexdeck enable usage --placement below --size 60`; see [pane placement and size](../../README.md#pane-placement-and-size).

## Use

Choose **RexDeck Usage** in the palette. RexDeck runs `tokscale tui --client omp` as one structured argv. The tabs show:

- Overview, Models and Daily totals;
- Sessions and Projects, where each omp session is attributed to its recorded workspace (for example a `RexDeck` row);
- Hourly, Monthly, Stats and Agents views.

Usage is collected **machine-wide** from local session logs. Opening it from a project terminal does not filter to that project; use the Projects tab instead.

**Refresh** uses upstream controls only:

- `r` refreshes now. In the private lab, through the RexDeck pane, today's Daily row went from 5404 to 5426 messages after `r` while an omp session kept writing.
- `Shift+R` turns tokscale's own automatic refresh on or off; the footer shows its state and interval. tokscale saves this choice in its own settings (`autoRefreshEnabled`). In the lab, with auto-refresh on, the same row went from 5401 to 5404 with no key pressed; `Shift+R` then showed `Auto-refresh OFF`.
- An auto-refresh setting you already have is preserved. RexDeck neither passes `--refresh` nor sends keys.

`q` quits back to the origin.

The pane always runs online. Offline mode is available only by running `tokscale --offline tui` outside RexDeck. Costs are estimates from public pricing data, not an invoice.

## Network, credentials and accounts

RexDeck launches tokscale's normal online mode and does not read, copy or isolate credentials. Behaviour qualified for 4.21.0:

- **Pricing:** the first data load in a session downloads public model pricing (LiteLLM, models.dev and OpenRouter data) when the 1-hour cache under `~/.config/tokscale/cache` has expired. Later refreshes in the same session reuse it.
- **Tokscale social login** is separate and optional. When you are signed in, the TUI also fetches your account stats. Being signed in does not mean any AI provider is signed in.
- **Usage tab:** opening it, and each refresh while it is open, queries provider quota APIs with whatever supported provider credentials tokscale finds. This happens regardless of `--client`: the client filter limits which usage logs are shown, not credential access. Finding credentials reads the macOS keychain through `security` (for example Claude Code, gh for Copilot, Cursor and Codex items).
- **Codex login import:** opening the Usage tab can copy your current Codex login (from `~/.codex/auth.json` or the keychain) into tokscale's own store, `~/.config/tokscale/codex-credentials.json`. tokscale may later refresh that stored login itself, which rotates the refresh token the Codex CLI also holds.
- **Cursor sync:** with the `omp` filter, tokscale skips its automatic Cursor sync before the TUI starts. The sync runs only when the client filter includes Cursor.
- **Account controls:** the unchanged upstream UI includes provider login (the `a` key runs `codex login`), account switch and removal controls, and the `x` key spends a Codex rate-limit reset credit after confirmation. They act on your real accounts. RexDeck never activates them and never runs `login`, `logout`, `submit`, `autosubmit` or `report`.
- **Helper processes:** besides keychain reads, tokscale starts `grok agent` when Grok credentials exist and `codex login` from its UI.
- **Autosubmit:** if you have enabled tokscale's scheduled autosubmit, that schedule belongs to tokscale and runs independently of this pane.

tokscale is not read-only or credential-isolated. It also writes its own settings, caches (including a cache derived from session transcripts) and any imported provider logins under `~/.config/tokscale`.

## Disable

```sh
rexdeck disable usage
```

This removes only the Usage loader and module. tokscale, its configuration, history, credentials, any autosubmit schedule and open panes all remain.

tokscale is an unbundled [MIT-licensed upstream tool](https://github.com/junhoyeo/tokscale/blob/main/LICENSE).

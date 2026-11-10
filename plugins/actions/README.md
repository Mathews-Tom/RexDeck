# Actions

Read GitHub workflow runs, jobs and the last 200 lines of a job log in a packaged
Textual view in a native Rex pane. No dispatch, rerun, cancellation, approval or
other GitHub write actions are provided.

[Back to RexDeck](../../README.md)

## Prerequisites and enablement

Use macOS arm64, Python 3.12+, Rex and separately installed Git and GitHub CLI
(`gh` **2.97 or newer**). No additional TUI executable is required. Configure
supported gh auth outside RexDeck; the explicit account must match the launcher's
read-only identity check. Every view refresh rechecks that account before reading
data, refusing a different login. RexDeck never switches gh accounts or copies
credentials.

```sh
rexdeck setup
rexdeck enable actions
rexdeck status actions
rex do rexdeck_actions account=YOUR_LOGIN
rex do rexdeck_actions remote=upstream account=YOUR_LOGIN
rex do rexdeck_actions repo=github.com/OWNER/REPO account=YOUR_LOGIN
```

Invoke from a GitHub repository worktree terminal. Choose only one of `remote` or
`repo`. An explicit repo includes HOST/OWNER/REPO; without either, exactly one
unambiguous github.com remote is required. The shared [GitHub selection rules](../github/README.md)
apply, including refusal of repository-chosen non-github.com credential hosts.
Authentication, repository and exact-origin return are owned by the common
launcher; this view cannot be launched without its verified environment.
`python -m rexdeck.actions` alone fails clearly rather than guessing an account.

## Read and navigate

The initial filter is the invoking worktree's current branch (`git branch
--show-current`), or HEAD's commit SHA in a detached worktree. This is a filter on
the explicitly selected remote repository, not a claim that a local branch has
been pushed there.

- **Up/Down**, **Enter**: select a run and read its jobs; select a job and read logs.
- **b**: focus branch filter and clear commit; **s**: focus commit and clear branch.
  Submit a nonempty branch or 7–40 hexadecimal commit SHA with **Enter** or Apply.
  GitHub's `head_sha` filter matches exact SHAs; use the full 40-character SHA for
  reliable commit results. Exactly one filter must be filled.
- **n / p**: next/previous 30-run page. GitHub caps filtered search at 1,000 runs;
  narrow the branch/commit selection for older results. All job pages are read.
- **r**: explicit refresh of the current runs, jobs or log tail. No polling.
- **Escape**: jobs → runs, logs → jobs, or leave a filter input.
- **o**: explicitly open the fresh selected run's exact validated
  `https://github.com/OWNER/REPO/actions/runs/ID` URL via macOS `open`.
  Links never open automatically; other hosts are refused by this browser action.
- **q**: quit and return to the exact invoking Rex origin. Text input keeps normal
  typing keys; leave an input with Escape before using view shortcuts.

Loading, empty results, permission errors (403/404), rate limits (403/429), expired
logs (410), authentication and network errors are visible. Failed refreshes retain
the last successful result for that same selection as **STALE**, with its update
time and age calculated when the display last rendered, not a ticking live age.
Stale browser opens are refused. Repair connectivity/auth outside the pane, or
wait for a rate limit to reset, and press **r** to recover.
A missing/expired/unavailable log is an error, not an empty successful log.

## Safety and removal

Logs may contain secrets despite GitHub's masking. gh downloads each requested
job log into process memory; only a sanitized last-200-line tail is retained in
the view, never written to disk by RexDeck. Terminal control sequences and Unicode
formatting controls are removed; log text is not interpreted as markup or links.
gh 2.97+ supports `gh api --allow-escape-sequences` (qualified with 2.103.3). Only
the log request uses that flag to capture ANSI-bearing responses into a pipe;
the view strips escape sequences before any rendering.
Do not screenshot or share sensitive logs. Repository/account scope is fixed for
this invocation. No owner credentials, upstream settings or Rex config are changed.
The command palette is disabled, including its screenshot-to-disk command.

After updating RexDeck, `rexdeck enable actions` refreshes its owned module.
`rexdeck disable actions` removes only this integration; GitHub and Git remain
independent. Disable all applied integrations before uninstalling RexDeck.

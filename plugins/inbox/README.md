# Inbox

A packaged Textual view of unread [GitHub notifications](https://docs.github.com/en/rest/activity/notifications), using existing GitHub CLI authentication. RexDeck neither creates credentials nor requests additional scopes. If GitHub refuses access, the view says **existing auth lacks access**; any access decision belongs to the owner outside RexDeck.

## Enable and select an account

Install GitHub CLI yourself, configure existing supported authentication outside RexDeck, then:

```sh
rexdeck setup
rexdeck enable inbox
rex do rexdeck_inbox account=YOUR_LOGIN
```

No repository or worktree is needed. The account is explicit; an optional `host=github.com` defaults to `github.com`. Other hosts are refused until Enterprise is qualified, along with missing accounts, repository/file selections and account mismatches. Only isolated RexDeck-owned modules can use this account context. The launcher captures the selection, verifies `gh api user --jq .login` against it, sets `GH_HOST`, `GH_PATH`, `GH_PROMPT_DISABLED` and `REXDECK_GITHUB_ACCOUNT`, and strips `GH_REPO`, `GH_FORCE_TTY` and `CLICOLOR_FORCE` without changing the parent environment. Existing repository-scoped GitHub workflows are unchanged. Re-run `rexdeck enable inbox` after host updates.

## Controls and limits

- `r`: refresh; every refresh and page/participating change re-verifies the selected login. While a request runs, repeated refresh, page and participating keys are ignored rather than spawning concurrent requests. Loading, empty, stale, permission, rate-limit and network states are explicit. Freshness is the last successful read time and elapsed seconds at the most recent UI interaction; there is no timer or polling.
- `f`: cycle all, review_requested, mention, ci_activity and other reasons. Filtering applies to the **current page**, not the whole account.
- `t`: toggle participating-only (off by default). All requests use `all=false`, so only unread notifications are listed.
- `n` / `p`: next / previous 50-item page. A full last page may offer one empty next page.
- Arrow keys: select; the wrapping detail area shows the selected repository, subject title/type, reason and updated time even in a narrow pane.
- `o`: open the selected URL, never automatically. Pull requests, issues and full-SHA commits map only from canonical `https://api.github.com/repos/OWNER/REPO/...` subject URLs to the same ASCII owner/repository/type/identifier on `https://github.com`. A CheckSuite with a null subject URL opens the validated repository's **Actions page**, not an exact run; the status line explicitly says so. Non-null check-suite URLs resolve through the documented [workflow-run check_suite_id filter](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository); a suite ID is never guessed to be a run ID. Only one exact matching run and canonical browser URL are accepted. Other unmappable subjects and malformed URLs are refused visibly. The null-suite fixture is synthetic: no CheckSuite subjects were available in the owner's accessible notification history during qualification.
- `q`: quit to the exact invoking terminal. The command palette and API markup are disabled; API text is sanitized.

GET requests only: no mark-read, done, subscribe, writes, background notifications or polling. Opening a browser uses normal upstream browser behavior; RexDeck itself does not mark a thread read. Notification titles/repository names are personal payloads: they are held in memory and must not be captured in diagnostic evidence. Native qualification uses synthetic visual fixtures and live counts/state names only.

`rexdeck disable inbox` removes only this integration; siblings and already-open panes remain. Live qualification used the owner's **already-existing interactive `GITHUB_TOKEN`**, passed only in memory into the isolated lab environment, with no credential files copied. The token-free stored `gh`/macOS-keyring route was attempted using the owner's existing `GH_CONFIG_DIR` read-only and no `GITHUB_TOKEN` or `GH_TOKEN`; `GET /user` returned HTTP 401 in that lab, so that route is not qualified. The authorized existing environment route verified the selected account and read notifications successfully. Enterprise hosts remain refused until separately qualified.

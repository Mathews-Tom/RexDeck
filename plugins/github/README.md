# GitHub

Host unmodified [gh-dash 4.26.2](https://github.com/dlvhdr/gh-dash) in a native Rex pane for repository-scoped pull requests and issues. Local [Git](../git/README.md) remains independently selectable and unchanged. Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

The qualified platform is macOS arm64, Python 3.12+, and an existing Rex CLI/server (API revision 10 baseline). Install **gh-dash 4.26.2**, Git, and GitHub CLI (`gh`) yourself. Qualification used gh **2.103.3**. Download the standalone official [4.26.2 release](https://github.com/dlvhdr/gh-dash/releases/tag/v4.26.2), verify its published checksum, and place `gh-dash` in a trusted absolute PATH directory; no `gh extension install` is required. The official artifact reports `module version: v4.26.2` in `gh-dash --version`. RexDeck never installs or upgrades tools.

Use existing supported gh authentication for the selected host. RexDeck does not log in, create accounts, extract/copy credentials, or call `gh auth switch`. Environment authentication follows [gh's documented precedence](https://cli.github.com/manual/gh_help_environment). An explicit `account=LOGIN` must match the authenticated login returned by `gh api --hostname HOST user`; otherwise launch stops visibly. To use a different account, provide an already configured matching supported authentication environment outside RexDeck. RexDeck does not select inactive stored accounts automatically.

## Enable and open

```sh
rexdeck setup
rexdeck enable github
rexdeck status github
```

From the intended repository/worktree terminal, invoke the native action with a deliberate account and, where needed, repository choice:

```sh
rex do rexdeck_github account=YOUR_LOGIN
rex do rexdeck_github remote=upstream account=YOUR_LOGIN
rex do rexdeck_github repo=github.com/OWNER/REPO account=YOUR_LOGIN
```

The optional `repo` argument **requires HOST/OWNER/REPO**, including the host. `remote` selects a named fetch remote. Do not combine them. Without either, exactly one remote pointing literally to `github.com` is required. Multiple remotes (including fork/upstream, different hosts, or duplicate identities), multiple fetch URLs, SSH aliases, unsupported URL forms, and missing remotes require an explicit choice. A remote whose URL points to a host other than `github.com` is never used, even when named: remote URLs come from the repository, so choose `repo=HOST/OWNER/REPO` and type the host yourself. This keeps existing credentials (for example an enterprise token in the environment) from being sent to a host the repository chose. HTTPS and literal `git@HOST:OWNER/REPO.git` / `ssh://git@HOST/OWNER/REPO.git` fetch URLs are supported. An explicit `repo` can deliberately target a repository other than the cwd's remote.

A repository-less or bare terminal always fails clearly, even with `repo`. The integration reuses local Git's worktree discovery and inherited `GIT_*` override refusal. It does not change remotes, Git config or `safe.directory`. The chosen repo and account are captured before creating a pane; later remote/focus changes do not redirect that invocation. `GH_HOST`, `GH_REPO`, and `GH_PATH` are passed only to the launched process tree. Auth identity and repository accessibility are verified with read-only gh API requests before gh-dash starts. Missing authentication, inaccessible/renamed repositories and API errors stay visible for acknowledgement, not an empty successful dashboard.



Open **RexDeck GitHub** from Rex’s command palette with the same explicit account/repository arguments, or use the native action above.

## Use

The default split is right/50%; `rexdeck enable github --placement below --size 70` changes only this integration. Use upstream `j`/`k` to select a PR, `p` to toggle its detail preview, `s` to switch views, and `o` to open the selected item's exact upstream deep link with your browser. `?` shows normal upstream controls. The initial PR and issue sections include all statuses, including merged/closed items. Normal `q` returns focus to the exact captured origin; a closed origin produces a visible failure rather than selecting a replacement.

## Configuration

### Pane layout

`rexdeck enable github --placement below --size 35` changes only this integration; the default is right/50%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size). `--placement` accepts `right`, `left`, `below` or `above`; `--size` is a whole percentage from 20 to 80.

### gh-dash settings

Edit YAML with `$EDITOR "${XDG_CONFIG_HOME:-$HOME/.config}/gh-dash/config.yml"`. Use `GH_DASH_CONFIG` to select another file, or edit `.gh-dash.yml` / `.gh-dash.yaml` in the invoking directory. Theme, keybindings, pagers, repository paths and custom commands are useful here; launch section/filter overrides are described below. Paths and layering are defined by the [qualified upstream parser](https://github.com/dlvhdr/gh-dash/blob/v4.26.2/internal/config/parser.go). RexDeck never edits upstream config.

Manage authentication outside RexDeck with `gh auth login` and inspect it with `gh auth status`; do not copy credential files into RexDeck. gh uses `GH_CONFIG_DIR`, otherwise `$XDG_CONFIG_HOME/gh` or `~/.config/gh`, and supported `GH_TOKEN` / `GITHUB_TOKEN` or `GH_ENTERPRISE_TOKEN` / `GITHUB_ENTERPRISE_TOKEN` environment authentication; see [upstream environment settings](https://cli.github.com/manual/gh_help_environment). The launcher inherits upstream configuration/authentication environment, but removes relative and empty `PATH` entries so helpers are not found in the invoking directory.

The launcher supplies a temporary `--config` overlay with PR and issue sections scoped to the explicit repository, no notification sections, and initial PR view. gh-dash's normal global config still loads. The overlay includes the normally selected `GH_DASH_CONFIG` file or `.gh-dash.yml` / `.gh-dash.yaml` in the **invoking directory**, matching upstream's cwd-based lookup rather than searching the repository root; **theme, keybindings, custom commands, pagers, repo paths and other settings are preserved**. Custom section lists, initial view and smart-filtering-at-launch are replaced for this invocation, not written back. User settings can still intentionally navigate elsewhere or change filters; the initial scope is not a sandbox. Missing/invalid selected config is left to upstream's visible parser error, never silently ignored. The temporary overlay is removed on normal exit and on SIGHUP/SIGTERM; SIGKILL/power loss cannot run cleanup.

This uses upstream 4.26.2's supported config layering ([parser.go](https://github.com/dlvhdr/gh-dash/blob/v4.26.2/internal/config/parser.go): config selection 610–632, section replacement/keybinding union 635–671, includes 693–719, global/overlay merge 728–735). `GH_REPO` overrides upstream's otherwise first-known-remote selection ([go-gh repository.Current](https://github.com/cli/go-gh/blob/v2.13.0/pkg/repository/repository.go)); `GH_HOST` selects its API host and existing authentication. Repo-scoped sections avoid relying on the user's optional smart-filter default.

## Capabilities and limits

**Write-capable, not read-only:** upstream can comment, review, merge/close PRs/issues, change assignments, checkout branches, launch configured editors/pagers/custom commands, and open your browser. It runs as your OS account with existing auth; no sandbox or read-only enforcement is claimed. gh-dash may initialize its normal config/cache. RexDeck's smoke uses only read APIs and UI inspection/browser URL capture, not writes to real GitHub work or review solicitation. Enterprise hosts and multi-account credentials are validated/refused by the same explicit protocol but have no live Enterprise/multi-account qualification evidence.

### Verification

Run root Ruff lint/format, strict mypy and the full pytest suite. Build/install the wheel outside the checkout on a private Rex endpoint. Inspect PR and issue lists, select a PR and its detail/deep link, verify account/auth/API failures, repository-less refusal, ambiguous-remotes refusal and explicit success, normal quit to the exact origin, and independent local Git behavior. Terminal captures establish native-pane behavior, not owner-app desktop visual approval. gh-dash is an unbundled MIT-licensed upstream tool; GitHub CLI and Git are separately managed dependencies.

## Disable

After updating RexDeck, run `rexdeck enable github` to refresh its owned module. `rexdeck disable github` removes only its loader/action/module, preserving Git, Files, upstream settings and running panes. Disable every applied integration before uninstalling RexDeck. No credentials or upstream executables are removed.

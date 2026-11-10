# Git

Launch unmodified [lazygit 0.66.2 / 0.66.3](https://github.com/jesseduffield/lazygit) in a native Rex terminal, using the invoking terminal's repository/worktree and returning to its captured origin.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)



Quitting returns focus to the exact invoking terminal.

## Requirements

- macOS arm64, existing Rex CLI/server (API revision 10 baseline, not an exact-version gate), Python 3.12+, and `uv`.
- `git` and `lazygit` on `PATH`. Lazygit **0.66.2 and 0.66.3** are qualified versions; other installed versions require manual resolution, never implicit upgrade. Private runtime qualification used Git 2.56.0; other platforms are not qualified.
- An invoking terminal that reports its current local directory to Rex. A normal repository subdirectory or linked worktree is supported; a bare repository, missing directory, or unavailable cwd is rejected.

## Enable and open

Install RexDeck as described in the root README, then select one target once:

```sh
rexdeck setup
rexdeck enable git
rexdeck status git
```

Setup/status never install packages. `enable git` validates preinstalled Git and qualified lazygit **0.66.2 or 0.66.3**, reusing the qualified executable and revalidating metadata identity plus the saved target before registration. Missing or unqualified selected tools return actionable nonzero manual-install/version-check/rerun guidance without prompts or partial registration. RexDeck never installs, upgrades or removes Git/lazygit, or probes/executes Homebrew.

Install [lazygit 0.66.2](https://github.com/jesseduffield/lazygit/releases/tag/v0.66.2) or [0.66.3](https://github.com/jesseduffield/lazygit/releases/tag/v0.66.3) yourself using [upstream instructions](https://github.com/jesseduffield/lazygit#installation); provision Git separately. If choosing Homebrew, inspect `brew info lazygit` yourself first; skip Homebrew if its selected version is not qualified and use a qualified upstream release instead. Only run `brew install lazygit` yourself when its version qualifies and you accept its dependency/source-build/upgrade effects. Check `lazygit --version` and `git --version`, then rerun `rexdeck enable git`. RexDeck qualifies neither Homebrew's version nor its prefix; the owner controls all package effects. Disable/uninstall leaves Git/lazygit and their configuration intact.



Open **RexDeck Git** from Rex’s command palette, or `rex do rexdeck_git` (no arguments), in the intended repository/worktree.

## Use

| Key | Action |
| --- | --- |
| `q` | Quit to the captured invoking terminal |

A typical workflow is to inspect the worktree diff, stage intended changes, review them, then commit using lazygit’s upstream controls. Custom keybindings retain their upstream meanings.

From the intended repository terminal, choose **RexDeck Git** in Rex's native palette. The terminal split runs lazygit with structured argv and the validated invoking cwd. Git performs its normal repository discovery; RexDeck does not manufacture a `.git` directory or collapse linked worktrees to their common repository.

Use upstream lazygit controls to inspect diffs, stage, commit, and perform other Git operations. Normal `q` returns to the exact captured terminal. Each deliberate launch is independent. If the origin has closed, focus restoration fails visibly for acknowledgement instead of selecting a replacement pane. Launch failures and nonzero tool exits are also visible.

The lazygit pane opens to the right of the invoking terminal using half its space by default. Choose another side or share with `rexdeck enable git --placement below --size 40` (since 0.2.0); see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). The choice is saved with Git's ownership record and does not change Files, lazygit's configuration, or Git settings.

## Configuration

### Pane layout

`rexdeck enable git --placement right|left|below|above --size 20-80` changes only this integration; the default is right/50%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### Lazygit settings

Edit YAML with `$EDITOR "$HOME/Library/Application Support/lazygit/config.yml"` on macOS. `XDG_CONFIG_HOME` can relocate the configuration directory; older installations may use `~/Library/Application Support/jesseduffield/lazygit/config.yml`. Repository overrides live in `<repo>/.git/lazygit.yml`; `.lazygit.yml` files in parent directories also load. Set only needed options such as `gui`, `keybinding`, `git` and `customCommands`; see [qualified upstream configuration](https://github.com/jesseduffield/lazygit/blob/v0.66.3/docs/Config.md). Git settings and credentials remain Git-managed.

RexDeck starts lazygit with no extra arguments, config overlay or tool-specific environment overrides, in the validated invoking worktree directory. It never edits upstream config. The launcher inherits upstream configuration/authentication environment, but removes relative and empty `PATH` entries so helpers are not found in the invoking directory.

### Git discovery environment

Inherited repository/config-injection/discovery variables such as `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`, `GIT_CONFIG`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*`, `GIT_CEILING_DIRECTORIES`, and `GIT_DISCOVERY_ACROSS_FILESYSTEM` are rejected, including empty values. Resolve the ambiguous environment before launching; RexDeck does not unset it silently or change your configuration. Normal upstream settings homes and supported authentication stores remain unchanged.

Git validation searches only absolute `PATH` directories and invokes the resolved executable explicitly; relative and empty entries cannot select a Git binary from the invoking repository. Git discovery failures retain upstream diagnostics, including ownership recovery guidance; RexDeck does not change `safe.directory`.

### Independent ownership

`enable files` and `enable git` share one saved target but own separate actions, modules, loaders, and receipts. `status` reports both; `status files` and `status git` inspect one. Ready means owned configuration/action registration matches the current package, not that a tool process is running.

Git deploys `plugins/git/rexdeck_git.lua` under the saved RexDeck data root. Its receipt/lock directory is `git/` beneath the saved Files-compatible ownership workspace. Files' existing settings schema 2, receipt schema 3, paths, and loader identity remain valid. Shared config/data-root locks prevent overlapping lifecycle mutations; a competing operation fails explicitly and can be retried after the other finishes.

## Capabilities and limits

Lazygit is **write-capable**, runs as your OS account, and retains its normal keys, settings, editor, custom commands, authentication, and network operations. RexDeck neither sandboxes it nor makes it read-only. Upstream Git hooks and configured commands retain their effects. Enable does not fetch, stage, commit, push, log in, or change Git settings. The initial directory is not a filesystem jail.

### Verification

Run the root Ruff/format/strict-mypy/full-pytest chain and compile the shared packaged `src/rexdeck/host/main.lua` asset. Build/install the wheel outside the checkout and use a private native Rex endpoint with disposable repositories. Exercise actual diff/stage/commit, normal subdirectory and linked-worktree origins, default and changed pane layouts, independent quits after focus changes, closed-origin failure, missing tools, both sibling enable/disable orders, and open-pane survival. Native terminal captures prove the private TUI interaction; they do not claim owner-app palette/font/desktop visual approval.

Lazygit is an unbundled [MIT-licensed upstream tool](https://github.com/jesseduffield/lazygit/blob/master/LICENSE). Rex is external and separately licensed. See the [release workflow and owner gate](../../docs/releasing.md) for publication/evidence limits.

## Disable

After reinstalling the updated package, explicitly refresh every integration you use:

```sh
rexdeck enable git
rexdeck enable files
rexdeck status
```

Only unchanged owned artifacts are refreshed. Disable is independent and preserves siblings, existing tool panes, unrelated config edits, symlinks/modes, Git settings, repositories, and credentials:

```sh
rexdeck disable git
```

Run `disable` for **every applied** integration before uninstalling RexDeck or deliberately replacing its saved target, including integrations reported as disabled after a failed enable. A disabled action can still have an applied loader and module; Python package removal alone does not remove Rex loaders. Changed artifacts produce an ownership conflict for manual judgment; they are not silently deleted. Cancellation recovery has the same limits as Files: SIGKILL/power loss cannot run rollback.

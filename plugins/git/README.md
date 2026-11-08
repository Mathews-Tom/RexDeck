# Git

Launch unmodified [lazygit](https://github.com/jesseduffield/lazygit) in a native Rex terminal, using the invoking terminal's repository/worktree and returning to its captured origin.

[Back to RexDeck](../../README.md)

## Requirements and capabilities

- macOS arm64, Rex CLI/server API revision 10, Python 3.12+, and `uv`.
- `git` and `lazygit` on `PATH`. Private runtime qualification used Git 2.56.0 and lazygit 0.66.2; other versions/platforms are not qualified.
- An invoking terminal that reports its current local directory to Rex. A normal repository subdirectory or linked worktree is supported; a bare repository, missing directory, or unavailable cwd is rejected.

Lazygit is **write-capable**, runs as your OS account, and retains its normal keys, settings, editor, custom commands, authentication, and network operations. RexDeck neither sandboxes it nor makes it read-only. Upstream Git hooks and configured commands retain their effects. Enable does not fetch, stage, commit, push, log in, install tools, or change Git settings. The initial directory is not a filesystem jail.

## Enable and use

Install RexDeck as described in the root README, then select one target once:

```sh
rexdeck setup
rexdeck enable git
rexdeck status git
```

From the intended repository terminal, choose **RexDeck Git** in Rex's native palette. The terminal split runs lazygit with structured argv and the validated invoking cwd. Git performs its normal repository discovery; RexDeck does not manufacture a `.git` directory or collapse linked worktrees to their common repository.

Use upstream lazygit controls to inspect diffs, stage, commit, and perform other Git operations. Normal `q` returns to the exact captured terminal. Each deliberate launch is independent. If the origin has closed, focus restoration fails visibly for acknowledgement instead of selecting a replacement pane. Launch failures and nonzero tool exits are also visible.

Inherited repository/config-injection/discovery variables such as `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`, `GIT_CONFIG`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*`, `GIT_CEILING_DIRECTORIES`, and `GIT_DISCOVERY_ACROSS_FILESYSTEM` are rejected, including empty values. Resolve the ambiguous environment before launching; RexDeck does not unset it silently or change your configuration. Normal upstream settings homes and supported authentication stores remain unchanged.

Git validation searches only absolute `PATH` directories and invokes the resolved executable explicitly; relative and empty entries cannot select a Git binary from the invoking repository. Git discovery failures retain upstream diagnostics, including ownership recovery guidance; RexDeck does not change `safe.directory`.

## Independent ownership, update, and removal

`enable files` and `enable git` share one saved target but own separate actions, modules, loaders, and receipts. `status` reports both; `status files` and `status git` inspect one. Ready means owned configuration/action registration matches the current package, not that a tool process is running.

Git deploys `plugins/git/rexdeck_git.lua` under the saved RexDeck data root. Its receipt/lock directory is `git/` beneath the saved Files-compatible ownership workspace. Files' existing settings schema 2, receipt schema 3, paths, and loader identity remain valid. Shared config/data-root locks prevent overlapping lifecycle mutations; a competing operation fails explicitly and can be retried after the other finishes.

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

## Verification

Run the root Ruff/format/strict-mypy/full-pytest chain and compile the shared packaged `src/rexdeck/plugins/files/main.lua` asset. Build/install the wheel outside the checkout and use a private native Rex endpoint with disposable repositories. Exercise actual diff/stage/commit, normal subdirectory and linked-worktree origins, independent quits after focus changes, closed-origin failure, missing tools, both sibling enable/disable orders, and open-pane survival. Native terminal captures prove the private TUI interaction; they do not claim owner-app palette/font/desktop visual approval.

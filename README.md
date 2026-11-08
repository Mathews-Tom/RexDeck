# RexDeck

A collection of independently selectable plugins and extensions that bring existing tools into your own Rex application.

Files and local Git bring unmodified Yazi and lazygit into native Rex terminals. GitHub workflows, rich viewers, agent/subagent views, and task-provider integrations remain planned. Select the tools you need without installing an unrelated default suite.

## Plugins and extensions

| Integration | Purpose | Status |
| --- | --- | --- |
| [Files](plugins/files/README.md) | Browse, preview, and manage files using unmodified Yazi in a native Rex terminal | Available |
| [Git](plugins/git/README.md) | Inspect, stage, and commit in the invoking repository/worktree using unmodified lazygit | Available |
| GitHub | Pull-request/issue workflows with explicit repository/host/account | Planned |
| Rich viewers | Rendered Markdown, PDF, and image viewing | Planned |
| Agents and subagents | Attention, observed state, and navigation to agent work | Planned |
| Tasks | Independently selectable Linear and ClickUp workflows | Planned |

Planned integrations are not implemented or installable yet. RexDeck is not published on PyPI; install the current source package as described below.

## Getting started

The qualified platform is **macOS arm64, Rex API revision 10 baseline, and Python 3.12+**. Revision 10 is not an exact-version compatibility gate. Rex must already be installed and running; RexDeck does not bootstrap Rex, Homebrew, Python, or uv. Other platforms, remote hosts, and multi-device operation are not qualified.

Until the approved PyPI release is published, install the pinned public source baseline (Files and Git; before S1 dependency onboarding):

```sh
uv tool install 'git+https://github.com/Mathews-Tom/RexDeck.git@d7f934e31d2becf53bc435ac931fa6e1b984bbd0'
```

For the S1 candidate, use its reviewed checkout and record the exact commit:

```sh
uv tool install .
rexdeck setup
rexdeck enable files
rexdeck status
```

The installed command works from any directory; the package includes the shared native action and Python launcher for Files and Git. No shell wrapper, `RD_*` variables, or developer checkout is needed at runtime. Ensure the tool executable directory reported by `uv` is on `PATH`.

`setup` remembers the selected Rex configuration and running server without changing Rex configuration. Interactive setup asks for approval before saving a personal-server target. Use `enable files` or `enable git` independently, `status [files|git]` to inspect ownership/registration, and `disable <id>` to remove only that integration. Unqualified `status` reports both integrations, retaining healthy results alongside an integration-specific fault if its sibling cannot be inspected. See the plugin READMEs for prerequisites, capabilities, updates, and removal.

The uv-managed package remains the runtime source. Setup freezes the app data root from `$XDG_DATA_HOME/rexdeck` (default `~/.local/share/rexdeck`); each integration deploys `plugins/<id>/rexdeck_<id>.lua` there with an independently owned tracked loader in Rex's `init.lua`. Approved preferences remain in `$XDG_CONFIG_HOME/rexdeck/config.json`. Files receipt/lock paths remain in the saved `$XDG_STATE_HOME/rexdeck/files` workspace; Git uses its `git/` subdirectory. Later `XDG_DATA_HOME` changes do not retarget the saved installation. Run `disable` for every applied integration before uninstalling RexDeck or deliberately resetting a saved target, including integrations reported as disabled after a failed enable.

### Selected dependencies

`setup` and `status` never install packages. `enable files` checks only Files' Yazi requirement; `enable git` checks Git/lazygit. Qualified existing tools are reused: **Yazi 26.9.1, lazygit 0.66.2 or 0.66.3**. A different installed version requires deliberate manual resolution, never an implicit upgrade. The consented route requires **Homebrew 7.0.12** on macOS arm64, at `/opt/homebrew` or an existing short custom prefix (at most 13 bytes, following [Homebrew support tiers](https://docs.brew.sh/Support-Tiers)). A missing selected tool can be installed only after an interactive **default-No** prompt displays the resolved `brew install yazi` or `brew install lazygit` command and its effects. Installation uses argv, not a shell, and shows progress/errors. RexDeck then rechecks the qualified tool version and frozen approved target before registration. Downloading does not hold the Rex configuration lock.

Homebrew qualification also requires the selected prefix's own `bin/brew`, its standard Cellar path at most 20 bytes, enabled relocation, and no conflicting prefix/Cellar/brew-file environment redirects. The installer disables automatic metadata updates, implicit install-upgrade, dependent checks, and cleanup for its child process. These safeguards do not promise transaction rollback or replace upstream package-manager diagnostics.

Executable discovery ignores empty and relative `PATH` entries, so checkout-local tools cannot run during pre-consent version probes or replace the validated lifecycle executable. Put supported tools and Homebrew in absolute `PATH` directories.

Declining, noninteractive use, unsupported environments, or a missing manager never authorize installation or partial action registration. Install manually using the upstream instructions ([Yazi](https://yazi-rs.github.io/docs/installation/), [lazygit](https://github.com/jesseduffield/lazygit#installation)), or with an existing supported Homebrew:

```sh
brew install yazi       # Files only
rexdeck enable files
# Or, independently:
brew install lazygit    # Git only; git must also be installed
rexdeck enable git
```

No install-all, automatic manager installation, sudo, silent upgrade, tool configuration change, or package removal is performed. Failed/cancelled installs may leave package-manager changes; RexDeck does not undo Homebrew transactions. `disable` and Python package uninstall leave upstream tools installed.

## Design principles

- **Reuse tools, do not rebuild them.** Preserve their normal interfaces, configuration, authentication, and capabilities.
- **Prefer native Rex integration.** Use native actions and panes where sufficient. LazyRex is optional only where a particular integration needs an existing capability; it is not a collection-wide dependency.
- **Keep features independently selectable.** Install only the dependencies needed by the chosen integration; no implicit default suite, hooks, or autostart.
- **Preserve user context and ownership.** Pass explicit launch context and structured arguments, keep unrelated work intact, and make removal reversible.
- **Disclose capabilities accurately.** External tools run as your OS account and can be write-capable; they are not sandboxed or automatically read-only.

## Development

Install LuaJIT before running the suite: native Lua behavior tests require `lua` or `luajit` and otherwise skip. On macOS:

```sh
brew install luajit
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
```

Plugin-specific syntax checks and real interaction verification belong in each plugin's README. Keep plugin documentation under `plugins/<name>/README.md` and packaged runtime assets under `src/rexdeck/plugins/<name>/`. Link available integrations from the table above. Do not create empty plugin folders for planned features.

## Release preparation and owner gate

Version **0.1.0 is prepared, not evidence of publication**. Do not use a PyPI install command until the release's public registry files and hashes have been verified. A PyPI name lookup returning 404 does not establish ownership, name eligibility, or publishing access.

The manual [`publish.yml`](.github/workflows/publish.yml) workflow supports [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) using [uv](https://docs.astral.sh/uv/guides/package/#publishing-your-package). No ordinary push, PR, or tag publishes anything; no release-time rebuild occurs.

Before dispatch, the owner must configure a PyPI project or pending publisher for `rexdeck`: GitHub owner `Mathews-Tom`, repository `RexDeck`, workflow `publish.yml`, environment `pypi`. Configure that GitHub environment with the repository owner as a required reviewer and restrict deployment to `main`. Self-review prevention is optional when another authorized reviewer is available; it must not prevent the sole owner from approving an assistant-dispatched run under the owner's GitHub identity. These account/environment changes require owner participation and are not performed by setup or this repository. Missing protection or publisher access holds publication.

1. Merge only the reviewed candidate. A successful **push on main** `checks.yml` run builds wheel/sdist once and retains the exact pair as `rexdeck-0.1.0`; download and inspect them, record both SHA-256 hashes, immutable artifact ID, full commit SHA, run URL, and runtime/security/review evidence. Artifacts expire; a rebuild requires fresh hashes and approval.
2. Obtain explicit owner approval naming that exact commit, artifact ID, both filenames/hashes, and reviewed evidence. Any changed input requires renewed approval. After this approval only, create/verify the annotated `v0.1.0` tag and GitHub Release at that commit, attaching the same files; never rebuild for a tag.
3. Dispatch `publish.yml` **from main at that approved commit**, entering the exact SHA, artifact ID, hashes and approval-evidence URL. The protected-environment reviewer must compare the dispatch inputs/run summary against the approval before approving the deployment. The workflow verifies repository/run provenance, tag target and hashes, then publishes only `approved/rexdeck-0.1.0.tar.gz` and `approved/rexdeck-0.1.0-py3-none-any.whl` with `uv publish --trusted-publishing always` (no token secret or broad `dist/*` upload).
4. Verify [PyPI JSON](https://pypi.org/pypi/rexdeck/0.1.0/json) exposes both approved hashes. Then run an isolated no-cache public-index install, allowing index propagation, and repeat actual private Rex use/quit/refresh/disable. Only then document `uv tool install 'rexdeck==0.1.0'` as available:

   ```sh
   uv --no-cache run --isolated --no-project --index https://pypi.org/simple \
     --with rexdeck==0.1.0 rexdeck --help
   ```

CI and private endpoint/TUI captures do not prove owner-app palette, glyph, or desktop visual approval. Successful package installation does not prove native interaction; successful local builds do not prove registry publication. Keep those evidence claims separate.

## License

RexDeck is [MIT licensed](LICENSE), included in wheel/sdist metadata. External executables are **not bundled**: [Yazi is MIT](https://github.com/sxyazi/yazi/blob/main/LICENSE) and [lazygit is MIT](https://github.com/jesseduffield/lazygit/blob/master/LICENSE), retaining upstream notices and terms. Git retains its own upstream license. Rex is an externally installed, separately licensed application prerequisite; RexDeck's MIT license does not grant rights to Rex or redistribute it.

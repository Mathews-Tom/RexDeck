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

Planned integrations are not implemented or installable yet. Published RexDeck releases are available on [PyPI](https://pypi.org/project/rexdeck/); `rexdeck plugins` is new in version 0.1.1.

## Getting started

The qualified platform is **macOS arm64, Rex API revision 10 baseline, and Python 3.12+**. Revision 10 is not an exact-version compatibility gate. Rex must already be installed and running; RexDeck does not bootstrap Rex, Homebrew, Python, or uv. Other platforms, remote hosts, and multi-device operation are not qualified.

Install the latest published release from PyPI:

```sh
uv tool install --default-index https://pypi.org/simple rexdeck
```

Then save your target and enable only the integrations you need:

```sh
rexdeck setup
rexdeck enable files
rexdeck status
```

The installed command works from any directory; the package includes the shared native action and Python launcher for Files and Git. No shell wrapper, `RD_*` variables, or developer checkout is needed at runtime. Ensure the tool executable directory reported by `uv` is on `PATH`.

`setup` remembers the selected Rex configuration and running server without changing Rex configuration. Interactive setup asks for approval before saving a personal-server target. Use `enable files` or `enable git` independently, `status [files|git]` to inspect ownership/registration, and `disable <id>` to remove only that integration. Unqualified `status` reports both integrations, retaining healthy results alongside an integration-specific fault if its sibling cannot be inspected. See the plugin READMEs for prerequisites, capabilities, updates, and removal.

Run `rexdeck plugins` to list every available plugin shipped with this installation and its current state as JSON. Configured rows include `enabled: true` or `enabled: false`; before setup, rows report `not-configured` rather than guessing that a target is disabled. Plugin inspection faults remain visible alongside healthy sibling rows and return a nonzero exit status. Listing never enables integrations or installs dependencies. Planned integrations are not included.

The uv-managed package remains the runtime source. Setup freezes the app data root from `$XDG_DATA_HOME/rexdeck` (default `~/.local/share/rexdeck`); each integration deploys `plugins/<id>/rexdeck_<id>.lua` there with an independently owned tracked loader in Rex's `init.lua`. Approved preferences remain in `$XDG_CONFIG_HOME/rexdeck/config.json`. Files receipt/lock paths remain in the saved `$XDG_STATE_HOME/rexdeck/files` workspace; Git uses its `git/` subdirectory. Later `XDG_DATA_HOME` changes do not retarget the saved installation. Run `disable` for every applied integration before uninstalling RexDeck or deliberately resetting a saved target, including integrations reported as disabled after a failed enable.

### Selected dependencies

`setup` and `status` never install packages. `enable files` validates preinstalled Yazi; `enable git` validates preinstalled Git/lazygit. Qualified versions are **Yazi 26.9.1, lazygit 0.66.2 or 0.66.3**. Missing or unqualified selected tools produce actionable nonzero manual-install/version-check/rerun guidance without registration or installation prompts. RexDeck never installs, upgrades, downloads, cancels package transactions, or removes upstream tools, and never probes or executes Homebrew.

Executable discovery ignores empty and relative `PATH` entries. Absolute entries, including checkout directories and symlinks to them, remain eligible for version probes and lifecycle lookup. Put only trusted tools in absolute `PATH` directories. RexDeck captures followed-target device/inode/mode/size/nanosecond timestamps/owner before the version probe and rechecks that identity afterward and under its operation locks immediately before applying or enabling an integration. Replacement or modification requires rerunning enable. This is metadata-based detection, not pinned execution or protection against an executable changed after the final check.

Install dependencies yourself using qualified upstream releases: [Yazi 26.9.1](https://github.com/sxyazi/yazi/releases/tag/v26.9.1), [lazygit 0.66.2](https://github.com/jesseduffield/lazygit/releases/tag/v0.66.2) or [0.66.3](https://github.com/jesseduffield/lazygit/releases/tag/v0.66.3). Follow [Yazi installation](https://yazi-rs.github.io/docs/installation/) or [lazygit installation](https://github.com/jesseduffield/lazygit#installation); Git must also already be installed for Git integration.

If you choose Homebrew, first run `brew info yazi` or `brew info lazygit` yourself and inspect the selected version and dependency/source-build/upgrade effects. **Skip Homebrew if its selected version is not qualified**; use the qualified upstream release instead. Only if the version qualifies and you accept the package effects, run the selected `brew install yazi` or `brew install lazygit` yourself. RexDeck does not qualify a Homebrew version, prefix or transaction; all package effects remain owner-controlled.

Check `yazi --version` or `lazygit --version` (and `git --version` for Git), then rerun `rexdeck enable files` or `rexdeck enable git`.

Rex, Yazi, lazygit, Git, Homebrew, Python and uv remain externally managed prerequisites. No install-all, sudo, silent upgrade, tool configuration change, or upstream package removal is performed. `disable` and Python package uninstall leave upstream tools installed.

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

## Release workflow and owner gate

Source version **0.1.1** targets the plugin-listing patch release. Confirm publication using its public registry files and hashes; local version metadata, a tag or a GitHub Release alone does not prove PyPI delivery. The previous **0.1.0** release is already published and verified.

The manual [`publish.yml`](.github/workflows/publish.yml) workflow supports [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) using [uv](https://docs.astral.sh/uv/guides/package/#publishing-your-package). No ordinary push, PR, or tag publishes anything; no release-time rebuild occurs.

Before dispatch, the owner must configure a PyPI project or pending publisher for `rexdeck`: GitHub owner `Mathews-Tom`, repository `RexDeck`, workflow `publish.yml`, environment `pypi`. Configure that GitHub environment with `Mathews-Tom` as its **sole required reviewer** and restrict deployment to `main`; GitHub permits any listed reviewer to approve, so additional reviewers do not enforce owner approval. Leave self-review prevention disabled for owner-dispatched runs so the sole owner can approve an assistant-dispatched run under the owner's GitHub identity. These account/environment changes require owner participation and are not performed by setup or this repository. Missing protection or publisher access holds publication.

The S1 publication boundary assumes this remains a personal-account repository with Mathews-Tom as its sole administrator. Owner-controlled bypass/configuration is trusted; transfer to an organization requires re-evaluating that gate.

1. Merge only the reviewed candidate. A successful **push on main** `checks.yml` run builds wheel/sdist once and retains the exact pair as `rexdeck-0.1.1`; download and inspect them, record both SHA-256 hashes, immutable artifact ID, full commit SHA, run URL, and runtime/security/review evidence. Artifacts expire; a rebuild requires fresh hashes and approval.
2. Obtain explicit owner approval naming that exact commit, artifact ID, both filenames/hashes, and reviewed evidence. Any changed input requires renewed approval. After this approval only, create/verify the annotated `v0.1.1` tag and GitHub Release at that commit, attaching the same files; never rebuild for a tag.
3. Dispatch `publish.yml` **from main at that approved commit**, entering the exact SHA, artifact ID, hashes and approval-evidence URL. The protected-environment reviewer must compare the dispatch inputs/run summary against the approval before approving the deployment. The workflow verifies repository/run provenance, tag target and hashes, then publishes only `approved/rexdeck-0.1.1.tar.gz` and `approved/rexdeck-0.1.1-py3-none-any.whl` with `uv publish --trusted-publishing always` (no token secret or broad `dist/*` upload).
4. Verify [PyPI JSON](https://pypi.org/pypi/rexdeck/0.1.1/json) exposes both approved hashes. Then run an isolated no-cache public-index install, allowing index propagation, and repeat actual private Rex use/quit/refresh/disable. Only then claim `rexdeck==0.1.1` is published:

   ```sh
   uv --no-cache run --isolated --no-project --index https://pypi.org/simple \
     --with rexdeck==0.1.1 rexdeck plugins
   ```

CI and private endpoint/TUI captures do not prove owner-app palette, glyph, or desktop visual approval. Successful package installation does not prove native interaction; successful local builds do not prove registry publication. Keep those evidence claims separate.

## License

RexDeck is [MIT licensed](LICENSE), included in wheel/sdist metadata. External executables are **not bundled**: [Yazi is MIT](https://github.com/sxyazi/yazi/blob/main/LICENSE) and [lazygit is MIT](https://github.com/jesseduffield/lazygit/blob/master/LICENSE), retaining upstream notices and terms. Git retains its own upstream license. Rex is an externally installed, separately licensed application prerequisite; RexDeck's MIT license does not grant rights to Rex or redistribute it.

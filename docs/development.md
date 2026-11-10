# Development

[Back to RexDeck](../README.md) · [Releasing](releasing.md)

## Local checks

Install LuaJIT before running the suite: native Lua behaviour tests require `lua` or `luajit` and otherwise skip. On macOS:

```sh
brew install luajit
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
```

Plugin-specific syntax checks and real interaction verification belong in each plugin's guide.

## Documentation layout

| Content | Where |
| --- | --- |
| What RexDeck is, quick start, links | `README.md`, generic only: it names no individual integration, so adding one never changes it |
| Catalogue of integrations | `docs/integrations.md`: add one row in the matching category |
| Settings locations | `docs/configuration.md`: add one row linking to the guide's `#configuration` section |
| Commands, states, dependencies, releases | `docs/usage.md`, `docs/dependencies.md`, `docs/releasing.md` |
| Everything specific to one integration | `plugins/<id>/README.md` |

Every integration guide uses the same sections, in this order:

1. **Title and summary**
2. **Requirements**
3. **Enable and open**
4. **Use**
5. **Configuration**: pane layout plus the tool's own settings, and exactly what RexDeck passes at launch
6. **Capabilities and limits**
7. **Disable**

Do not create empty plugin folders for planned features.

## Media

`assets/rexdeck-banner.png`, `assets/rexdeck-launch.mp4` and `assets/rexdeck-launch.gif` are rendered from the HTML sources in [`assets/source/`](../assets/source/). The artwork is deliberately generic, so new integrations never require new media. It shows four categories (Files & code, Data & logs, Systems & services, Work & APIs) and archetypal panes with skeleton content, and it never names a specific tool, version or count. Keep it that way.

To re-render, use a Playwright version whose Chromium is installed, plus `ffmpeg` on `PATH`:

```sh
uv run --no-project --with playwright playwright install chromium
uv run --no-project --with playwright python assets/source/render.py
```

## Adding an integration

Every integration shares one native host:

- [`src/rexdeck/host/main.lua`](../src/rexdeck/host/main.lua) registers the **RexDeck &lt;Title&gt;** palette action and the `rexdeck_<id>` native action, and opens the pane.
- [`src/rexdeck/host/launch.py`](../src/rexdeck/host/launch.py) validates context, runs the tool on the pane's real TTY and returns focus to the exact captured origin.
- Lifecycle ownership, shared locking, rollback, dependency validation and status are generic.

To host another installed interactive terminal tool, add two files, and do not copy lifecycle or focus code:

- one module `src/rexdeck/catalogue/<id>.py` that exports its `Integration` descriptor as `INTEGRATION`
- a `plugins/<id>/README.md` that follows the guide structure above

[`src/rexdeck/integrations.py`](../src/rexdeck/integrations.py) discovers the [catalogue modules](../src/rexdeck/catalogue/) and lists new plugins after the original seven, ordered by id. The descriptor declares:

- **Identity and dependencies:** a unique `id` matching `[a-z][a-z0-9]*` (`attention` is reserved) and a plain-word `title`, plus:
  - the executable name, resolved from absolute `PATH` entries
  - qualified versions, with the version-report pattern
  - the release URL and upstream license
  - any extra preinstalled prerequisites, with their install source
- **Context:**
  - `directory`: the invoking terminal's validated cwd.
  - `git-worktree`: that cwd plus Git's own discovery; inherited repository redirection is refused.
  - `document`: an explicitly selected `.md`/`.markdown` file from the action's `file` argument, otherwise the invoking directory.
  - `media`: a required selected local `.pdf`/`.jpg`/`.jpeg`/`.png`/`.svg` file.
  - `selected-file`: a required explicit existing file whose suffix the descriptor accepts, or a directory when allowed; glob metacharacters are refused.
  - `dekit-config`: a required explicit trusted file named exactly `dekit.yaml`, launched as `dekit -C <root>` with a quit-time report of surviving tasks.
  - `github-account`: an explicit verified GitHub account on github.com for owned views, without a repository or worktree.
  - `github-worktree`: reuses Git discovery and also freezes an explicit host, repository and account choice.
    - Ambiguous remotes require `remote` or a fully qualified `repo`, and `account` must match supported existing gh authentication.
    - Without descriptor `args`, the launcher always passes gh-dash's `--config` overlay with process-local `GH_HOST`/`GH_REPO`, so this branch supports gh-dash only.
    - An owned view (descriptor `args` before the captured selection) runs after the same verification with `GH_HOST`/`GH_REPO`/`GH_PATH` and `REXDECK_GITHUB_ACCOUNT`, and gets no overlay.

  File contexts pass the validated target as the tool's final argument. A cwd never stands in for a selected file, account or agent identity.
- **Argv:** fixed arguments appended after the executable. The launch is shell-less; nothing is interpolated.
- **Layout:** a default placement and size, overridable per integration with `enable --placement/--size`.
- **Capabilities:** honest disclosure of writes, filesystem or transcript access, network, authentication and external process execution. Descriptive metadata is not a sandbox.

The host suits programs that own an interactive TTY until the user quits. A one-shot command that prints and exits closes its pane immediately on success (nonzero exits stay visible for acknowledgement), so it first needs an explicit pager or presentation route. Data-backed views need a supported structured backend and real provider evidence. GUI applications are not terminal panes. Adding a descriptor does not make every CLI an integration.

After adding an integration, add its row to [Integrations](integrations.md) and [Configuration](configuration.md). The README and media need no change.

# Integrations

Every integration is independently selectable: enable only the ones you need. Each opens in a native Rex pane split from the terminal you invoke it from, and quitting returns focus to that exact terminal. `rexdeck plugins` lists everything shipped with your installed version, including state, qualified tool versions and pane layout.

Integrations come in two kinds:

- **Hosted terminal tools** run an unmodified upstream TUI that you install yourself. The tool keeps its own license, settings and capabilities.
- **Purpose-built views** are RexDeck-authored Textual interfaces over a supported CLI or API. They need no separate TUI executable.

Each guide covers requirements, how to open the integration, its controls, configuration, capabilities and limits. Open any integration from Rex's command palette as **RexDeck &lt;Title&gt;**, or with `rex do rexdeck_<id>`. See [Usage](usage.md) for commands and [Configuration](configuration.md) for settings.

[Back to RexDeck](../README.md)

## Files & code

| Integration | id | Built with | Opens on | What you can do |
| --- | --- | --- | --- | --- |
| [Files](../plugins/files/README.md) | `files` | Yazi | invoking directory | Browse, preview and manage files |
| [Git](../plugins/git/README.md) | `git` | lazygit | invoking Git worktree | Inspect, stage and commit in the invoking repository or worktree |
| [GitHub](../plugins/github/README.md) | `github` | gh-dash | verified repository and account | Work with repository-scoped pull requests and issues |
| [Actions](../plugins/actions/README.md) | `actions` | RexDeck view over GitHub CLI | verified repository and account | Read workflow runs, jobs and job-log tails without GitHub writes |
| [Markdown](../plugins/markdown/README.md) | `markdown` | markdown-reader | selected document or directory | Read Markdown with in-pane Mermaid diagrams |
| [Viewer](../plugins/viewer/README.md) | `viewer` | Yazi, pdftoppm and resvg | selected PDF or image | Preview PDF pages, JPG, PNG and SVG through Rex Kitty graphics |
| [Slides](../plugins/slides/README.md) | `slides` | presenterm | selected Markdown deck | Present a Markdown slide deck |

## Data & logs

| Integration | id | Built with | Opens on | What you can do |
| --- | --- | --- | --- | --- |
| [JSON](../plugins/json/README.md) | `json` | jless | selected JSON file | Collapse, search and copy JSON nodes |
| [Logs](../plugins/logs/README.md) | `logs` | lnav | selected log file or directory | Filter, query and navigate merged logs |
| [SQL](../plugins/sql/README.md) | `sql` | Harlequin | selected SQLite database | Browse schemas and query the selected database opened read-only |
| [Tables](../plugins/tables/README.md) | `tables` | VisiData | selected CSV, TSV, JSON or JSONL | Sort, select and summarize tabular data; editing remains available |
| [Dependencies](../plugins/deps/README.md) | `deps` | RexDeck view over deps.dev and OSV | package chosen in the pane | Inspect public dependency graphs, licenses and known advisories |
| [Images](../plugins/images/README.md) | `images` | RexDeck view over public OCI registries | repository chosen in the pane | Browse public image tags, digests and platforms without downloading layers |

## Systems & services

| Integration | id | Built with | Opens on | What you can do |
| --- | --- | --- | --- | --- |
| [System](../plugins/system/README.md) | `system` | bottom | this machine | Monitor processes, CPU, memory, disks and network with read-only controls |
| [Disk](../plugins/disk/README.md) | `disk` | dua | invoking directory | Explore directory disk usage; upstream deletion remains available |
| [Containers](../plugins/containers/README.md) | `containers` | lazydocker | current Docker context | Inspect containers, logs and stats |
| [Redis](../plugins/redis/README.md) | `redis` | redis-tui | upstream connection settings | Browse keys, values, TTLs and server metrics |
| [Processes](../plugins/procs/README.md) | `procs` | dekit | selected trusted `dekit.yaml` | Start, attach to, restart and stop project tasks |
| [HTTP](../plugins/http/README.md) | `http` | Slumber | invoking directory | Send HTTP requests from a collection and inspect responses |
| [Dashboard](../plugins/dashboard/README.md) | `dashboard` | Dashbrew | selected trusted board JSON | Open a board with script and API panels |

## Work & APIs

| Integration | id | Built with | Opens on | What you can do |
| --- | --- | --- | --- | --- |
| [ClickUp](../plugins/clickup/README.md) | `clickup` | RexDeck view over the ClickUp API | saved account, workspace and list or space | Browse a list or space queue, read details and comments, and apply reviewed task changes |
| [Inbox](../plugins/inbox/README.md) | `inbox` | RexDeck view over GitHub CLI | verified GitHub account | Read unread notifications with filters and explicit refresh |
| [Usage](../plugins/usage/README.md) | `usage` | tokscale | this machine | Explore token usage by model, session and project |
| [Focus](../plugins/focus/README.md) | `focus` | pomo | invoking directory | Run a work timer with trusted directory settings |
| [Music](../plugins/music/README.md) | `music` | spotify_player | invoking directory | Browse Spotify playlists and control playback |

Exact qualified versions are listed in each guide and reported by `rexdeck plugins`.

## Built-in CLI extension

[Attention](../plugins/attention/README.md) is a built-in command, not a pane plugin. It does not appear in `rexdeck plugins` and needs no `enable`. After `rexdeck setup`, it observes a terminal's reported status on demand and jumps back to that exact terminal after revalidation. Optional Claude Code and Codex hooks add subagent rows. Run `rexdeck attention --help`.

## Help cover more environments

Feedback and pull requests are welcome, especially for environments the maintainer cannot currently qualify:

- **Linear:** there is no integration yet. Follow [Adding an integration](development.md#adding-an-integration) and exercise real Linear data.
- **GitHub Enterprise Server or multiple GitHub accounts:** explicit host, repository and account checks are implemented, but only `github.com` with one account has been exercised.
- **A second ClickUp account or workspace:** isolation has test coverage and a deliberate account-mismatch check, but has not been tried against a second real account.

Include what you ran and what you observed in an issue or pull request.

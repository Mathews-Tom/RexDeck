# Containers

Inspect Docker services, containers, logs and stats in a native Rex pane with unmodified [lazydocker 0.25.2](https://github.com/jesseduffield/lazydocker/releases/tag/v0.25.2). Quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64, a running Rex server and [RexDeck setup](../../README.md#getting-started).
- Owner-installed **lazydocker 0.25.2** on a trusted absolute `PATH` entry. Use the [qualified upstream release](https://github.com/jesseduffield/lazydocker/releases/tag/v0.25.2), verify it yourself and check `lazydocker --version` (the first line is `Version: 0.25.2`). RexDeck never installs, upgrades or removes it; missing and other versions are refused with manual installation guidance before registration.
- A reachable Docker daemon and an existing Docker context; Docker Desktop was used in qualification. Enable requires a preinstalled [Docker CLI](https://docs.docker.com/get-docker/) on `PATH`. Upstream Compose commands use `docker compose`, falling back to standalone `docker-compose` when the version probe fails; these helpers must be available on the **Rex server's `PATH`**, which can differ from your enabling shell. No Podman compatibility is claimed.

## Enable and open

```sh
rexdeck enable containers
rexdeck status containers
```

Open **RexDeck Containers** from Rex’s command palette, or `rex do rexdeck_containers`.

Containers is independently selectable. Default placement is right, 50%; the shared `--placement` and `--size` options apply.

## Use

Select the intended service, inspect Logs and Stats, then quit without executing lifecycle commands.

| Key | Action |
| --- | --- |
| `2` | Focus Services |
| `]` / `[` | Next / previous main tab |
| `b` / `Esc` | Open / cancel bulk-command menu |
| `q` | Quit and restore invoking-terminal focus |

From the directory containing your Compose project, choose **RexDeck Containers** in the palette. RexDeck launches `lazydocker` with no arguments, using that exact directory as cwd. It does not force a project name, compose file or Docker endpoint. Without a Compose project, upstream still displays daemon resources.

The endpoint comes from upstream Docker selection: `DOCKER_HOST` takes precedence, then `DOCKER_CONTEXT` or the saved current Docker context, then the platform default socket. RexDeck adds no endpoint flags or credential isolation; the Rex server's environment can differ from your shell.

Launching is not passive: upstream automatically runs `docker compose version` to choose its Compose command, falls back to `docker-compose` if that fails, then runs the selected command's `config --quiet` in the launch directory. This interprets the Compose file and its `.env` inputs before any inspection keys are pressed. An `ssh://` Docker endpoint also starts an `ssh` tunnel automatically; opening the **Top** tab runs the selected Compose command's `top`. These subprocesses resolve through the Rex server's `PATH`; trust both the executables and the launch directory. Owner-customized command templates can change what runs.

The private lab used **standalone `/usr/local/bin/docker-compose` (Docker Compose v5.6.1)**, not the Docker CLI plugin: its isolated HOME had no `~/.docker/cli-plugins`, and `docker compose version` failed. Reconstructing the original isolated HOME/XDG/PATH confirmed that failure and the working fallback; the lab's displayed bulk commands also used `docker-compose`.

Controls exercised in the private Rex qualification lab:

- The Compose project's `logger` service appeared in **Services** with its running status and image. `2` focuses Services.
- Logs were visible by default, including a unique fixture marker. `]` selects the next main tab: **Stats** displayed live CPU/memory graphs and process/network counters, followed by **Container Env** and **Container Config**; `[` moves back.
- Resizing to **40 columns** kept logs visible and the UI responsive, but truncated labels and the footer. Enlarge the pane for useful inspection.
- `q` quits and restores the exact origin block's focus.
- `b` opens a bulk-command menu and `Esc` cancels it. Qualification only inspected and cancelled these menus; it never executed a bulk action.

## Configuration

### Pane layout

Use `rexdeck enable containers --placement right|left|below|above --size 20-80` to choose placement and size; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size). Default: right, 50%.

### lazydocker settings

Use the upstream project/status panel’s `e` to edit the YAML configuration, or `o` to open it; quit and reopen lazydocker to apply changes. For the default modern macOS location, the equivalent is `$EDITOR "$HOME/Library/Application Support/lazydocker/config.yml"`; use the actual legacy/XDG/`CONFIG_DIR` location if configured. Relevant settings include `gui.sidePanelWidth`, `gui.wrapMainPanel`, `logs`, `commandTemplates`, custom/bulk commands and OS openers. See the [configuration guide](https://github.com/jesseduffield/lazydocker/blob/v0.25.2/docs/Config.md).

RexDeck passes no arguments, endpoint flags, environment overrides or configuration overlay. Docker context/config, `DOCKER_HOST`, `DOCKER_CONTEXT` and Compose project inputs remain upstream-managed in the Rex server’s inherited environment.

RexDeck never edits upstream configuration. The launch environment is inherited from the Rex server, with relative `PATH` entries removed so helpers cannot resolve from the working directory.

lazydocker reads and can write `config.yml` in its configuration directory. On macOS the default is `~/Library/Application Support/lazydocker`; an existing legacy `~/Library/Application Support/jesseduffield/lazydocker` is preferred. XDG settings can change this (the isolated lab created `$XDG_CONFIG_HOME/lazydocker/config.yml`); `CONFIG_DIR` overrides it. Debug mode writes `development.log` beside it. Configuration can supply executable custom commands, editor/openers and command templates. Session inspection/statistics are upstream runtime state, not a separate RexDeck data store. See the [configuration guide](https://github.com/jesseduffield/lazydocker/blob/v0.25.2/docs/Config.md) and [configuration implementation](https://github.com/jesseduffield/lazydocker/blob/v0.25.2/pkg/config/app_config.go).

## Capabilities and limits

**This is not a read-only Docker viewer or a project sandbox.** The launch directory selects the Compose project, but lazydocker can see other containers, images, volumes and networks on the same daemon. Qualification visibly included unrelated daemon resources alongside the disposable project. Do not operate on resources you do not intend to change.

**Container secrets are not masked.** The **Container Env** tab and inspect/config views display container environment variables and configuration verbatim, often including credentials or other secrets, for any container on the daemon. Opening from one Compose project does not protect other containers' secrets. Treat the pane and any captures/screenshots as sensitive.

The [upstream keybindings](https://github.com/jesseduffield/lazydocker/blob/v0.25.2/docs/keybindings/Keybindings_en.md) include start/stop, restart, pause, recreate, remove, attach, **exec shell** and configured custom commands. Project down can remove volumes/images; resource menus include **prune** (the image-prune menu was inspected without execution). These actions use your Docker authority and may irreversibly destroy data. Upstream browser openers and custom commands also use your OS authority. RexDeck does not remove these controls. Destructive actions were not invoked in qualification; automatic Compose validation, endpoint helpers and view-triggered subprocesses still run as described above.

Network access includes the configured Docker endpoint, which may be remote; Compose up/build/pull and custom commands may fetch images or other data. Compose files can reference environment files and secrets. Docker's existing configuration/context and any credentials used by its helpers remain owner-managed, not isolated by RexDeck.

Qualification created only one labelled disposable Compose project on Docker Desktop, inspected its logs/stats/config, and then removed that project with `down -v`. The single newly pulled fixture image was removed; existing daemon resources were not mutated. The lab used its own HOME/XDG directories and a private Rex endpoint, with only non-secret Docker socket/context metadata recreated locally. No existing credentials or owner configuration were copied.

## Disable

```sh
rexdeck disable containers
```

Only Containers' owned registration/module changes. An open Containers pane and an open Files sibling both survived qualification disable. Docker resources, lazydocker, its configuration and other plugins remain untouched.

lazydocker is an unbundled, owner-managed [MIT-licensed upstream tool](https://github.com/jesseduffield/lazydocker/blob/v0.25.2/LICENSE).

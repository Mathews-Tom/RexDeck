"""Containers: unmodified lazydocker using the existing Docker context."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="containers",
    title="Containers",
    tool="lazydocker",
    tool_label="lazydocker",
    description="Open lazydocker in the invoking directory with the existing Docker context",
    keywords="containers docker compose lazydocker logs stats",
    context="directory",
    versions=("0.25.2",),
    version_pattern=r"^Version:[ \t]+(\d+\.\d+\.\d+)[ \t]*\r?$",
    release="https://github.com/jesseduffield/lazydocker/releases/tag/v0.25.2",
    license="MIT",
    capabilities=(
        "reads Docker containers, logs, stats, images, volumes and networks using "
        "the existing Docker context; a compose project in the launch directory "
        "is a starting point, not isolation from other daemon resources",
        "displays container environment variables and inspect config (often "
        "secrets) unmasked for any container on the Docker daemon",
        "can start, stop, restart, pause, recreate and remove containers or compose "
        "projects, remove volumes/images/networks and run bulk prune operations",
        "can attach to containers, exec shells, run configured custom commands and "
        "open browser links; actions use the owner's Docker and OS authority",
        "automatically runs docker compose version, falling back to docker-compose, "
        "then compose config --quiet in the launch directory (interpreting compose "
        "and .env files); an ssh:// Docker endpoint starts an ssh tunnel; the Top "
        "tab runs compose top; helper executables come from the Rex server PATH",
        "network: talks to the configured Docker endpoint; compose up/build and "
        "custom commands can pull images or otherwise access the network",
        "reads/writes lazydocker config.yml (macOS: ~/Library/Application Support/"
        "lazydocker, or legacy jesseduffield/lazydocker); CONFIG_DIR can override "
        "the location; debug mode writes development.log there; reads Docker "
        "context/config and project compose files, which can reference .env files",
    ),
    default_layout=Layout("right", 50),
    enable_notice=(
        "Containers needs a running Docker daemon, with docker on the Rex server's "
        "PATH, before you open it."
    ),
    prerequisites=(("docker", "https://docs.docker.com/get-docker/"),),
)

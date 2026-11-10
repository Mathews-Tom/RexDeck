"""Redis: upstream redis-tui with its own connection manager."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="redis",
    title="Redis",
    tool="redis-tui",
    tool_label="redis-tui",
    description="Open redis-tui and select a Redis connection in its connection manager",
    keywords="redis redis-tui keys ttl metrics database",
    context="directory",
    versions=("1.0.48",),
    version_pattern=(
        r"^redis-tui (\d+\.\d+\.\d+) "
        r"\(commit: [^\r\n,)]+, built: [^\r\n)]+\)[ \t]*\r?$"
    ),
    release="https://github.com/davidbudnick/redis-tui/releases/tag/v1.0.48",
    license="MIT",
    capabilities=(
        "network: connects to the Redis server selected in upstream's connection "
        "manager; the invoking directory is only the pane cwd, not a server selector",
        "network: on every launch queries api.github.com releases/latest for an "
        "update notice (no opt-out); RexDeck never runs --update",
        "Redis read/write: browse values and TTLs, edit/create/delete keys, bulk "
        "delete, flush databases, import/export JSON, execute server-side Lua, "
        "change runtime CONFIG, publish/subscribe and monitor server metrics",
        "reads/writes ~/.config/redis-tui/config.json for connections, favorites, "
        "recent keys and settings; upstream strips Redis/SSH passwords and SSH "
        "passphrases when saving, but reads manually supplied plaintext passwords "
        "and retains passwords in memory",
        "legacy migration copies ~/.redis/config.json as-is to "
        "~/.config/redis-tui/config.json, including plaintext passwords and "
        "ssh_config.passphrase",
        "TLS requires upstream flags or config (not the connection form); "
        "RexDeck passes no connection flags or passwords",
        "Vault-configured connections use VAULT_ADDR with credentials from "
        "VAULT_TOKEN, ~/.vault-token, or the external token_helper program "
        "configured in ~/.vault",
        "SSH tunneling is not implemented; watch mode toggles a flag without "
        "automatic value refresh; upstream clipboard and file export/import "
        "controls remain available, with no sandbox or read-only guarantee",
    ),
    default_layout=Layout("right", 50),
)

"""Music: unmodified spotify_player with explicit upstream OAuth and device selection."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="music",
    title="Music",
    tool="spotify_player",
    tool_label="spotify_player",
    description="Browse Spotify playlists and control playback with spotify_player",
    keywords="music spotify spotify-player spotify_player playlists playback audio",
    context="directory",
    versions=("0.25.2",),
    version_pattern=r"^spotify_player (\d+\.\d+\.\d+)[ \t]*\r?$",
    release="https://github.com/aome510/spotify-player/releases/tag/v0.25.2",
    license="MIT",
    capabilities=(
        "network: Spotify Premium and interactive upstream OAuth required; reads "
        "private playlists, library and listening history and controls the "
        "account's playback on any selected Spotify Connect device",
        "not read-only: upstream can modify playlists, library, likes and follows; "
        "RexDeck does not restrict the OAuth scopes or upstream controls",
        "OAuth credentials are cached on disk in $HOME/.cache/spotify-player "
        "by default (Web API *_token.json and streaming credentials.json); "
        "the cache also holds logs, images and optional audio",
        "reads/writes $HOME/.config/spotify-player by default, not XDG overrides; "
        "trusted app.toml can execute player_event_hook_command and client_id_command",
        "streaming-enabled builds register a Spotify Connect device, can take over "
        "an idle account's playback (paused) at launch and output audio locally; "
        "desktop notifications and cover-image caching are on by default when compiled in",
        "upstream CLI control socket uses loopback UDP client_port 8080 by default; "
        "OAuth uses loopback TCP 8989 by default; avoid colliding with other instances",
        "RexDeck starts only the foreground TUI with no arguments, credentials, "
        "config/cache overrides or daemon; directory is only the pane cwd",
    ),
    default_layout=Layout("right", 50),
)

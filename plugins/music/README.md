# Music

Browse playlists and control Spotify playback in a native Rex pane with unmodified [spotify_player 0.25.2](https://github.com/aome510/spotify-player/releases/tag/v0.25.2). Quit returns to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- A running Rex server and RexDeck [set up](../../README.md#getting-started).
- Owner-installed **spotify_player 0.25.2** on a trusted absolute `PATH` entry. Use the matching [upstream release](https://github.com/aome510/spotify-player/releases/tag/v0.25.2), verify its checksum and check `spotify_player --version` reports `spotify_player 0.25.2`. RexDeck never installs, updates or removes it. Missing and other versions are refused before registration with manual guidance.
- Your **Spotify Premium** account and your participation in upstream OAuth. RexDeck never signs in for you or passes secrets in argv.

## Enable and open

```sh
rexdeck enable music
rexdeck status music
```

Choose **RexDeck Music** in the palette. RexDeck runs `spotify_player` **without arguments** in the invoking directory. The directory is only the pane cwd, not an account selector. Placement follows the shared convention, for example `rexdeck enable music --placement below --size 60`. Or run `rex do rexdeck_music` from a Rex terminal. No account or file argument is required; missing, remote or invalid directory context is refused.

## Use

### Sign in and choose a device

On first launch upstream opens an authorization URL in your browser and waits for its loopback callback. Sign in with the intended Premium account and review the consent page. Web API and streaming access require separate approvals; a custom client ID can require a third ncspot fallback approval. `spotify_player authenticate` forces fresh interactive approvals rather than reusing cached tokens; RexDeck never invokes it.

**Fresh-cache OAuth limitation in 0.25.2:** the default ncspot client needs a different callback path from the streaming client. [Upstream #1108](https://github.com/aome510/spotify-player/pull/1108) fixes the custom-client fallback, but the default-client branch still uses the streaming default and can fail with **`redirect_uri: Not matching configuration`**. Changing only the port or client ID is not a fix.

For a fresh default-client cache, or when the cached Web API token can no longer be refreshed (for example after Spotify's refresh-token expiry), with **no other spotify_player instance using the configured `client_port`**, authenticate the two stages separately. In the expiry case, rerun the bootstrap command; do not delete the cache:

```sh
spotify_player -o login_redirect_uri=http://127.0.0.1:8989/ncspot_login get key playback
# Approve ncspot's Web API consent. With no streaming credentials yet, this
# read-only CLI then exits with "new session" / "No cached credentials found".
spotify_player
# Approve the separate streaming consent; wait until the music interface loads.
```

The first command uses upstream OAuth and caches the Web API token; it does not print or copy token contents. The second reuses that token and uses the default streaming callback. This two-stage workaround was exercised in the isolated qualification lab. Never point the bootstrap CLI at another instance: it otherwise forwards the command to that instance rather than bootstrapping its own cache. If you already have working credentials, leave them alone.

The streaming default `login_redirect_uri` is **`http://127.0.0.1:8989/login`**; ncspot's registered callback is **`http://127.0.0.1:8989/ncspot_login`**. Both require TCP port **8989** to be available during sign-in. The override above is command-local, not a permanent change to streaming configuration. A custom client's ncspot fallback uses its hardcoded ncspot callback; see the [pinned authentication guide](https://github.com/aome510/spotify-player/blob/v0.25.2/README.md#authentication). No new app, client ID or client secret is required.

Open Spotify on your Mac or phone to make a playback device available. In the TUI, **`D`** opens device selection; use `j`/`k` and **Enter** to choose the intended device. Streaming-enabled builds also register **spotify-player** as a Spotify Connect device and can play through local audio output. Select it only if local playback is intended. If no device is active when it starts, upstream automatically transfers (paused) playback to its own spotify-player device; press `D` to choose another. Controlling playback affects your account's currently selected device, including another computer or phone.

Default controls from the [pinned upstream command table](https://github.com/aome510/spotify-player/blob/v0.25.2/README.md#commands):

- `u p`: browse your playlists; `j`/`k` and **Enter** select a playlist and track.
- **Space**: play/pause; `n`/`p`: next/previous track.
- `+`/`-`: volume by 5%; `>`/`<`: seek forward/back (default 5 seconds). Some devices limit volume control.
- `r`: refresh playback; `?`: help; **Esc**: close a popup.
- `q` or **Ctrl-C**: quit the foreground application and return to origin. RexDeck never starts daemon mode (`-d`/`--daemon`). Quitting is not a guarantee that playback on another Spotify device stops.



## Configuration

### Pane layout

`rexdeck enable music --placement right|left|below|above --size 20-80`; see [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### spotify_player settings

Version 0.25.2 defaults to **`$HOME/.config/spotify-player`** for `app.toml`, `keymap.toml` and `theme.toml`, and **`$HOME/.cache/spotify-player`** for caches. These defaults use `HOME`, **not `XDG_CONFIG_HOME` or `XDG_CACHE_HOME`**. Upstream supports `-c`/`--config-folder` and `-C`/`--cache-folder`; RexDeck passes neither, preserving your upstream configuration.

Edit TOML with `$EDITOR ~/.config/spotify-player/app.toml`; use `keymap.toml` for key bindings and `theme.toml` for colours in that same directory. See the [pinned configuration reference](https://github.com/aome510/spotify-player/blob/v0.25.2/docs/config.md). Review `client_port`, OAuth callback settings, notification/cover caching and executable hooks before launch. RexDeck passes no arguments, tool-specific environment overrides or config overlays, and never edits upstream config; it inherits the pane environment with helper lookup restricted to trusted absolute PATH entries.

## Capabilities and limits

At **40 columns**, the upstream playlist remains open, but table columns and playback/device metadata are heavily truncated. Widen the pane to inspect full titles or confirm the selected device; RexDeck does not restyle the upstream layout.

This is **not read-only**. Upstream requests playback, playlist, follow, library and listening-history scopes; it can modify playlists, likes, saved items and follows as well as control playback. Network requests reach Spotify. Album-artwork caching and desktop notifications are **on by default** in builds with those features and expose track metadata (disable with `enable_notify = false` / `enable_cover_image_cache = false` in `app.toml`). RexDeck adds no account sandbox or restricted-scope OAuth wrapper.

**OAuth credentials are cached on disk**, not in a RexDeck keychain: Web API tokens use `<client_id>_token.json`, and streaming credentials use `credentials.json`. Treat the whole cache as sensitive: it also contains logs/backtraces, cover images and optional audio. Do not copy it into evidence or source control. Sign out/revoke access through Spotify when appropriate; disabling Music does not delete upstream credentials.

**Trust your upstream configuration.** [`player_event_hook_command`](https://github.com/aome510/spotify-player/blob/v0.25.2/docs/config.md#player-event-hook-command) executes configured programs on playback events, and `client_id_command` executes a configured command to obtain the client ID. They run with your OS account's authority. No such commands are configured by RexDeck.

The foreground TUI opens an upstream CLI control socket on **loopback UDP `client_port = 8080`** by default. Another instance can collide even with a separate HOME; set a distinct port in the intended instance's `app.toml` before launch. This is separate from the **TCP 8989** OAuth listener. Never aim lab CLI commands at an owner's existing instance.

Build features vary: `spotify_player --help` shows available flags, and `spotify_player features` reports the compiled features. The qualified owner binary has streaming, rodio audio, media-control, image/ratatui-image and notify; no daemon, fzf, sixel or pixelate. Media controls are disabled by upstream default on macOS; notifications and audio are upstream capabilities, not RexDeck services. The integration does not enable extra features or start a daemon.

**Observed streaming limitation:** the qualified macOS binary initialized its Rodio sink on CoreAudio and selected the MacBook speakers, but local playback produced no audible output. Logs reported `audio key error`, `Unable to load key ... Service unavailable`, then `Symphonia Decoder Error: end of stream` and automatic track skipping. Host output was unmuted and both host/player volumes were nonzero. Play/pause, next and volume changes were visible on the selected `spotify-player` device, but this is **not proof of audible streaming**. No CoreAudio initialization failure was logged under the isolated environment. Version 0.25.2 chooses its compiled backend internally: `device.audio_backend` is not a supported option, and changing volume or normalization cannot repair a missing decryption key. RexDeck does not rebuild the binary or silently switch devices; treat audible local playback as an unresolved upstream/service limitation in this qualified environment.

## Disable

```sh
rexdeck disable music
```

Only Music's owned loader/module/receipt is removed. Other plugins, open panes, the upstream executable, account state and caches are not removed. spotify_player is an unbundled, owner-managed **MIT**-licensed tool; RexDeck does not fork or restyle it.

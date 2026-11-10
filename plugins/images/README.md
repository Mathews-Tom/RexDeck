# Images

Browse public OCI image tags, digests and platforms with a RexDeck-authored Textual view over public registry APIs. It opens in a native Rex pane; quitting returns focus to the exact invoking terminal.

[Back to RexDeck](../../README.md) · [All integrations](../../docs/integrations.md)

## Requirements

- macOS arm64 and the qualified Rex server; invoke from a Rex terminal reporting its directory through OSC7.
- The view is packaged with RexDeck (MIT); no additional tool, pinned upstream binary or account is required. An upstream `--version` check does not apply. RexDeck never installs external tools.
- Internet access to the public registry and its anonymous token endpoint.

## Enable and open

```sh
rexdeck enable images
rexdeck status images
```

Open **RexDeck Images** from Rex's command palette, or run `rex do rexdeck_images`. No `file=`, account or remote argument is required: the invoking directory is launch context only; choose a public registry repository inside the view.

## Use

Tags are fetchedone page at a time (30 requested). The reference field has focus on startup and after `e`, where letters are typed as text: press Escape to focus the tag table before using the letter shortcuts. From the table, use arrows and Enter to select a tag, `n`/`p` for pages, `r` to refresh the current page and selected manifest, `e` to edit the reference, and `q` to return to the exact invoking terminal. The detail area scrolls. Source and fetched-at times are shown separately for tags and manifest; refresh is explicit, never polling. Loading, empty, stale, permission, rate-limit and network states remain visible. A failed refresh preserves the last successful data as stale; `r` retries.

## Configuration

### Pane layout

`rexdeck enable images --placement right|left|below|above --size 20-80` sets placement and size; default: right, 60%. See [pane placement and size](../../docs/configuration.md#pane-placement-and-size).

### Images view settings

Enter a repository reference such as `nginx`, `team/image` or `ghcr.io/astral-sh/uv`; the view displays its normalized reference (`nginx` → `docker.io/library/nginx`). Use `e` to edit and Enter/Fetch to apply it. There is no persistent reference/config file or credential environment variable to configure; anonymous tokens and fetched data remain in memory. Docker configuration and credentials are never read; the cwd is not inspected.

RexDeck launches its distribution's interpreter with `-I -m rexdeck.images` in the invoking directory. It preserves the inherited environment except for restricting PATH to absolute entries (or the system default if none remain), adds no view-specific environment override or configuration overlay, and never edits upstream configuration. Pane layout is the only persistent setting described here.

## Capabilities and limits

Image references leave the machine: requests go to the selected HTTPS public registry and its anonymous token endpoint. No Docker credentials or local Docker configuration is read. Bearer challenges are followed only to the same registry host (or `auth.docker.io` for Docker Hub), with repository pull scope. Anonymous tokens live only in memory, never in output, diagnostics or disk caches. Private registries are unsupported; 401/403 reports this rather than requesting login. Docker Hub anonymous rate limits are surfaced; wait before retrying.

Only `/v2/<repository>/tags/list` and `/manifests/<tag>` metadata is fetched. No layers/config blobs, pull-to-disk, run, push or delete operation is offered. The four OCI/Docker index/manifest media types are accepted. Digests use `Docker-Content-Digest`, or sha256 of the exact manifest body when absent. Platforms come only from index descriptors (including any `unknown/unknown` attestation entries); a single manifest does not declare platforms without its config blob, which is deliberately not fetched. **This is not a vulnerability or signature verdict.** References accept repository names only, not tag/digest suffixes, URLs, ports or credentials.

`o` opens a validated exact Docker Hub repository page with `/usr/bin/open` argv (`https://hub.docker.com/_/nginx` for library images, `/r/<namespace>/<name>/tags` otherwise). GHCR image coordinates do not identify the owning GitHub source repository, so the view refuses to guess a package web URL. Other registries likewise refuse when there is no well-defined mapping. No automatic browser opens.

 ### Protocol design check

GO: [OCI Distribution v1.1.1](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md) provides tag pagination (`n`, `last`, `Link: rel="next"`) and manifest metadata. Live Docker Hub `library/nginx` and GHCR `astral-sh/uv` reads verified the 401 authentication challenge, anonymous token GET, tags, and multi-platform index manifests. GET is used rather than HEAD because platforms and fallback digest computation require the body. No blob request is necessary. The implementation validates same-origin pagination and printable, canonical token realm URLs and pull scope before issuing requests. Redirects are not followed; duplicate tag names, malformed and unexpected responses fail visibly.

## Disable

```sh
rexdeck disable images
```

Disabling Images removes its owned loader/module/receipt only. Already-open panes and other integrations remain usable; upstream services, tools and configuration remain untouched.

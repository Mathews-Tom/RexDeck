# Images

Enable with `rexdeck enable images`, invoke **Images: Open here** from a directory,
and enter `nginx`, `team/image` or `ghcr.io/astral-sh/uv`. RexDeck shows the
normalized reference (`nginx` → `docker.io/library/nginx`). This is the packaged
`rexdeck.images` Textual view, launched with `-I -m`; it does not inspect the cwd.

Tags are fetched one page at a time (30 requested). Use arrows and Enter to select
a tag, `n`/`p` for pages, `r` to refresh the current page and selected manifest,
`e` to edit the reference, Escape to return to the tag table, and `q` to return to
the exact invoking terminal. The detail area scrolls. Source and fetched-at times
are shown separately for tags and manifest; refresh is explicit, never polling.
Loading, empty, stale, permission, rate-limit and network states remain visible.
A failed refresh preserves the last successful data as stale; `r` retries.

## Boundaries

Image references leave the machine: requests go to the selected HTTPS public
registry and its anonymous token endpoint. No Docker credentials or local Docker
configuration is read. Bearer challenges are followed only to the same registry
host (or `auth.docker.io` for Docker Hub), with repository pull scope. Anonymous
tokens live only in memory, never in output, diagnostics or disk caches. Private
registries are unsupported; 401/403 reports this rather than requesting login.
Docker Hub anonymous rate limits are surfaced; wait before retrying.

Only `/v2/<repository>/tags/list` and `/manifests/<tag>` metadata is fetched.
No layers/config blobs, pull-to-disk, run, push or delete operation is offered.
The four OCI/Docker index/manifest media types are accepted. Digests use
`Docker-Content-Digest`, or sha256 of the exact manifest body when absent.
Platforms come only from index descriptors (including any `unknown/unknown`
attestation entries); a single manifest does not declare platforms without its
config blob, which is deliberately not fetched. **This is not a vulnerability
or signature verdict.** References accept repository names only, not tag/digest
suffixes, URLs, ports or credentials.

`o` opens a validated exact Docker Hub repository page with `/usr/bin/open` argv
(`https://hub.docker.com/_/nginx` for library images, `/r/<namespace>/<name>/tags`
otherwise). GHCR image coordinates do not identify the owning GitHub source
repository, so the view refuses to guess a package web URL. Other registries
likewise refuse when there is no well-defined mapping. No automatic browser opens.
Disabling Images preserves already-open panes and other integrations.

## Protocol design check

GO: [OCI Distribution v1.1.1](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md)
provides tag pagination (`n`, `last`, `Link: rel="next"`) and manifest metadata.
Live Docker Hub `library/nginx` and GHCR `astral-sh/uv` reads verified the 401
authentication challenge, anonymous token GET, tags, and
multi-platform index manifests. GET is used rather than HEAD because platforms
and fallback digest computation require the body. No blob request is necessary.
The implementation validates same-origin pagination and printable, canonical
token realm URLs and pull scope before issuing requests. Redirects are not
followed; duplicate tag names, malformed and unexpected responses fail visibly.

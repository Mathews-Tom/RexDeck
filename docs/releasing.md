# Release workflow and owner gate

[Back to RexDeck](../README.md) · [Development](development.md)

Published versions are listed in [CHANGELOG.md](../CHANGELOG.md) and on [PyPI](https://pypi.org/project/rexdeck/). Each one's GitHub Release and PyPI files carry the same approved wheel/sdist hashes. Never rebuild, retag or republish a published version.

A later release needs a separately named version target. Its release-preparation change updates the package version, and `<version>` below stands for that version. Local version metadata, a tag or a GitHub Release alone does not prove PyPI delivery.

The manual [`publish.yml`](../.github/workflows/publish.yml) workflow supports [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) using [uv](https://docs.astral.sh/uv/guides/package/#publishing-your-package). No ordinary push, PR or tag publishes anything, and no rebuild happens at release time.

## Owner prerequisites

Before dispatch, the owner configures a PyPI project or pending publisher for `rexdeck` with these values:

- GitHub owner: `Mathews-Tom`
- Repository: `RexDeck`
- Workflow: `publish.yml`
- Environment: `pypi`

The `pypi` GitHub environment must have:

- `Mathews-Tom` as its **sole required reviewer**. GitHub lets any listed reviewer approve, so adding reviewers would stop the approval from being the owner's.
- Deployment restricted to `main`.
- Self-review prevention left disabled, so the sole owner can approve an assistant-dispatched run under the owner's GitHub identity.

These account and environment changes need the owner and are not made by setup or this repository. Missing protection or publisher access holds publication.

This publication boundary assumes the repository stays a personal-account repository with Mathews-Tom as its sole administrator, and trusts owner-controlled bypass and configuration. Transferring the repository to an organization requires re-evaluating this gate.

## Steps

1. **Merge only the reviewed candidate.**
   - A successful **push on main** `checks.yml` run builds the wheel and sdist once and retains the exact pair as `rexdeck-<version>`.
   - Download and inspect both files.
   - Record:
     - both SHA-256 hashes
     - the immutable artifact ID
     - the full commit SHA
     - the run URL
     - the runtime, security and review evidence
   - Artifacts expire; a rebuild requires fresh hashes and approval.
2. **Obtain explicit owner approval.**
   - The approval names the exact commit, the artifact ID, both filenames and hashes, and the reviewed evidence. Any changed input requires renewed approval.
   - After this approval only, create and verify the annotated `v<version>` tag and the GitHub Release at that commit, attaching the same files. Never rebuild for a tag.
3. **Dispatch `publish.yml`.**
   - Dispatch **from main at the approved commit**, entering the exact SHA, artifact ID, hashes and approval-evidence URL.
   - The protected-environment reviewer compares the dispatch inputs and run summary against the approval before approving the deployment.
   - The workflow checks repository and run provenance, the tag target and the hashes. It then publishes only `approved/rexdeck-<version>.tar.gz` and `approved/rexdeck-<version>-py3-none-any.whl` with `uv publish --trusted-publishing always`, with no token secret and no broad `dist/*` upload.
4. **Verify the publication.**
   - Confirm `https://pypi.org/pypi/rexdeck/<version>/json` exposes both approved hashes.
   - Run an isolated no-cache public-index install, allowing time for index propagation.
   - Repeat real use, quit, refresh and disable in a private Rex.
   - Only then claim `rexdeck==<version>` is published:

   ```sh
   uv --no-cache run --isolated --no-project --python 3.12 --index https://pypi.org/simple \
     --with rexdeck==<version> rexdeck plugins
   ```

Keep these evidence claims separate:

- CI and private endpoint or TUI captures do not prove that the owner approved the app palette, glyphs or desktop visuals.
- A successful package installation does not prove native interaction.
- A successful local build does not prove registry publication.

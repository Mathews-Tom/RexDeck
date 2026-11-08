"""Identity, context, argv, layout and disclosure of each hosted terminal tool.

An integration is one installed upstream terminal program opened in a native Rex
pane. The shared host (``host/main.lua`` and ``host/launch.py``) and lifecycle
ownership consume only these fields; adding an interactive tool means adding a
descriptor here plus its plugin README, not copying lifecycle or focus code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# How the host resolves the invoking terminal's reported cwd before launch.
# "directory" opens the tool in that exact directory. "git-worktree" also
# requires cwd-based Git discovery to find a non-bare worktree and rejects
# inherited repository redirection. "document" accepts an explicitly selected
# Markdown file (the action's ``file`` argument, absolute or relative to the
# invoking directory) and otherwise browses the invoking directory; the
# validated target is passed to the tool as its final argument. "media" requires
# an explicit PDF/JPG/PNG/SVG file and never falls back to directory browsing.
# "github-worktree" reuses Git discovery and captures an explicit repository/host
# and account choice, verified against existing gh authentication before launch.
CONTEXTS = ("directory", "git-worktree", "document", "github-worktree", "media")
PLACEMENTS = ("right", "left", "below", "above")
MIN_SIZE = 20
MAX_SIZE = 80
_ID = re.compile(r"[a-z][a-z0-9]*\Z")
_TITLE = re.compile(r"[A-Z][A-Za-z0-9 ]*\Z")
_TOOL = re.compile(r"[a-z][a-z0-9._-]*\Z")
# Generated-module option names; a tool executable name keys its path beside them.
_RESERVED = frozenset(
    (
        "python",
        "rex",
        "server",
        "launcher_code",
        "plugin",
        "title",
        "tool",
        "tool_label",
        "description",
        "keywords",
        "context",
        "args",
        "layout",
    )
)


@dataclass(frozen=True)
class Layout:
    """Where a new tool pane opens beside its origin and its share of that space."""

    placement: str
    size: int

    def __post_init__(self) -> None:
        if self.placement not in PLACEMENTS:
            raise ValueError(
                f"Unknown placement {self.placement!r}; use one of "
                + ", ".join(PLACEMENTS)
            )
        if (
            not isinstance(self.size, int)
            or isinstance(self.size, bool)
            or not MIN_SIZE <= self.size <= MAX_SIZE
        ):
            raise ValueError(
                f"Pane size must be a whole percentage from {MIN_SIZE} to {MAX_SIZE}"
            )

    def split(self) -> dict[str, str | float]:
        """Translate to Rex session.new_split, whose ratio is the before part's share."""
        side = "after" if self.placement in ("right", "below") else "before"
        share = self.size / 100
        return {
            "direction": "horizontal"
            if self.placement in ("right", "left")
            else "vertical",
            "side": side,
            "ratio": round(1 - share if side == "after" else share, 2),
        }

    def document(self) -> dict[str, object]:
        return {"placement": self.placement, "size": self.size}

    @classmethod
    def load(cls, value: object) -> Layout:
        """Decode a saved layout exactly, refusing unknown or coerced fields."""
        if not isinstance(value, dict) or set(value) != {"placement", "size"}:
            raise ValueError("Saved layout must contain exactly placement and size")
        placement, size = value["placement"], value["size"]
        if not isinstance(placement, str):
            raise ValueError("Saved layout placement must be a string")
        if not isinstance(size, int) or isinstance(size, bool):
            raise ValueError("Saved layout size must be an integer")
        return cls(placement, size)


@dataclass(frozen=True)
class Integration:
    """One independently selectable upstream terminal tool."""

    id: str
    title: str
    tool: str
    tool_label: str
    description: str
    keywords: str
    context: str
    versions: tuple[str, ...]
    version_pattern: str
    release: str
    license: str
    capabilities: tuple[str, ...]
    default_layout: Layout
    args: tuple[str, ...] = ()
    # Extra preinstalled executables and where the owner installs them from.
    prerequisites: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _ID.match(self.id):
            raise ValueError(
                f"Integration id must be lowercase alphanumeric: {self.id}"
            )
        # Native attention owns `<workspace>/attention`; no integration may share it.
        if self.id == "attention":
            raise ValueError("Integration id 'attention' is reserved")
        # The title is embedded verbatim in the loader's markers and error string.
        if not _TITLE.match(self.title):
            raise ValueError(f"Integration title must be plain words: {self.title!r}")
        for field in (self.tool_label, self.description, self.keywords, *self.args):
            if not field or "\x00" in field or "\n" in field:
                raise ValueError(f"{self.id} has an empty or multiline text field")
        if self.context not in CONTEXTS:
            raise ValueError(f"Unknown integration context: {self.context}")
        if self.tool in _RESERVED or not _TOOL.match(self.tool):
            raise ValueError(f"Invalid tool executable name: {self.tool}")
        if not self.versions:
            raise ValueError(f"{self.id} must qualify at least one tool version")

    @property
    def loader_markers(self) -> tuple[bytes, bytes]:
        """Exact tracked-loader delimiters; Files keeps its original end marker."""
        end = (
            "-- END REXDECK\n" if self.id == "files" else f"-- END REXDECK {self.id}\n"
        )
        return (
            f"-- BEGIN REXDECK (owned native {self.title} loader)\n".encode(),
            end.encode(),
        )

    def describe(self) -> dict[str, object]:
        """Public identity, dependency and disclosure summary for listings."""
        return {
            "title": f"RexDeck {self.title}",
            "tool": self.tool,
            "versions": list(self.versions),
            "license": self.license,
            "context": self.context,
            "capabilities": list(self.capabilities),
            "default_layout": self.default_layout.document(),
        }


INTEGRATIONS: dict[str, Integration] = {
    integration.id: integration
    for integration in (
        Integration(
            id="files",
            title="Files",
            tool="yazi",
            tool_label="Yazi",
            description="Open yazi in the invoking terminal directory",
            keywords="files yazi explorer",
            context="directory",
            versions=("26.9.1",),
            version_pattern=(
                r"^Yazi(?:[ \t]+|[ \t]*\r?\n[ \t]*Version:[ \t]*)"
                r"(\d+\.\d+\.\d+)(?:\s|$)"
            ),
            release="https://github.com/sxyazi/yazi/releases/tag/v26.9.1",
            license="MIT",
            capabilities=(
                "filesystem read/write: create, rename, move, copy, delete, open",
                "runs configured openers, previewers and shell commands",
                "launch directory is a start point, not a root jail",
            ),
            default_layout=Layout("right", 50),
        ),
        Integration(
            id="git",
            title="Git",
            tool="lazygit",
            tool_label="lazygit",
            description="Open lazygit in the invoking terminal directory",
            keywords="git lazygit",
            context="git-worktree",
            versions=("0.66.2", "0.66.3"),
            version_pattern=r"(?:^|,\s*)version=(\d+\.\d+\.\d+)(?=,|\s*$)",
            release="https://github.com/jesseduffield/lazygit/releases/tag/v0.66.3",
            license="MIT",
            capabilities=(
                "repository writes: stage, commit, branch, rebase, discard, stash",
                "network when the user fetches, pulls or pushes with existing Git auth",
                "runs configured editors, pagers and custom commands",
            ),
            default_layout=Layout("right", 50),
            prerequisites=(("git", "https://git-scm.com/downloads"),),
        ),
        Integration(
            id="markdown",
            title="Markdown",
            tool="markdown-reader",
            tool_label="markdown-reader",
            description=(
                "Open markdown-reader on a selected Markdown file or the invoking "
                "directory, with Mermaid diagrams rendered in the pane"
            ),
            keywords="markdown md mermaid docs readme reader",
            context="document",
            versions=("1.35.4",),
            version_pattern=r"^markdown-reader (\d+\.\d+\.\d+)\s*$",
            release="https://github.com/leboiko/markdown-reader/releases/tag/v1.35.4",
            license="MIT",
            capabilities=(
                "file writes: hybrid (i) and fullscreen (I) edit modes save Markdown "
                "in place with :w",
                "reads the browsed directory tree; stores its own config, session "
                "and cache under the user's Library",
                "runs `git status` (PATH git, inherited GIT_* environment and "
                "repository config honoured) in the browsed root at launch and on file "
                "changes to colour the tree; this may refresh the Git index",
                "network: checks crates.io for a newer version at most daily unless "
                "[updates] check_for_updates = false; --check-external is not used",
                "renders Mermaid in-process; in Rex panes it selects Unicode text "
                "diagrams, with no browser or hosted renderer",
            ),
            default_layout=Layout("right", 50),
        ),
        Integration(
            id="usage",
            title="Usage",
            tool="tokscale",
            tool_label="tokscale",
            description=(
                "Open the tokscale usage TUI for local omp sessions with upstream "
                "online pricing and refresh"
            ),
            keywords="usage tokens cost tokscale omp models",
            # Usage is machine-wide; the invoking directory is only the pane cwd.
            context="directory",
            versions=("4.21.0",),
            version_pattern=r"^tokscale (\d+\.\d+\.\d+)\s*$",
            release="https://github.com/junhoyeo/tokscale/releases/tag/v4.21.0",
            license="MIT",
            capabilities=(
                "reads local AI-client session logs machine-wide (omp sessions under "
                "~/.omp) and caches derived usage; launching in a project is not a "
                "project filter",
                "network: first data load downloads model pricing when its 1-hour "
                "cache expired; when signed in to Tokscale it fetches account stats; "
                "the Usage tab (on open and each refresh) queries provider quotas "
                "with existing provider credentials regardless of --client",
                "writes its own settings and caches under ~/.config/tokscale (Shift+R "
                "persists the auto-refresh choice; the Usage tab can import and "
                "later refresh the current Codex login in codex-credentials.json)",
                "runs helpers: macOS security keychain reads, grok agent when Grok "
                "credentials exist, codex login from its UI",
                "its UI exposes account controls (provider login, account switch, "
                "removal, Codex reset-credit use); RexDeck never invokes login, "
                "submit, autosubmit or report",
            ),
            default_layout=Layout("right", 50),
            args=("tui", "--client", "omp"),
        ),
        Integration(
            id="github",
            title="GitHub",
            tool="gh-dash",
            tool_label="gh-dash",
            description="Open GitHub PRs and issues with explicit repository and account",
            keywords="github gh dashboard pull requests issues",
            context="github-worktree",
            versions=("4.26.2",),
            version_pattern=r"^module version: v(\d+\.\d+\.\d+), checksum:",
            release="https://github.com/dlvhdr/gh-dash/releases/tag/v4.26.2",
            license="MIT",
            capabilities=(
                "authenticated GitHub network reads and writes: comments, reviews, "
                "merge, close, assignments and other upstream actions",
                "requires account=LOGIN matching existing gh authentication; never "
                "switches global accounts or copies credentials",
                "launch-scoped repository sections replace custom sections; normal "
                "global/repository theme, keybindings and custom commands are retained",
                "runs gh, configured editors, pagers, browser and custom commands; "
                "upstream may initialize its normal config/cache",
                "initial repository scope is not a sandbox: upstream navigation "
                "and editable filters may deliberately leave it",
            ),
            default_layout=Layout("right", 50),
            prerequisites=(
                ("git", "https://git-scm.com/downloads"),
                ("gh", "https://cli.github.com/"),
            ),
        ),
        Integration(
            id="viewer",
            title="Viewer",
            tool="yazi",
            tool_label="Yazi",
            description="Preview an explicitly selected PDF, JPG, PNG or SVG in Yazi",
            keywords="viewer pdf jpg jpeg png svg image",
            context="media",
            versions=("26.9.1",),
            version_pattern=(
                r"^Yazi(?:[ \t]+|[ \t]*\r?\n[ \t]*Version:[ \t]*)"
                r"(\d+\.\d+\.\d+)(?:\s|$)"
            ),
            release="https://github.com/sxyazi/yazi/releases/tag/v26.9.1",
            license="MIT",
            capabilities=(
                "in-pane Kitty graphics previews; PDF pages use pdftoppm; SVG uses "
                "resvg, not a browser; unbundled helper prerequisites: "
                "pdftoppm/Poppler (GPL), resvg (MIT/Apache-2.0)",
                "filesystem read/write and configured openers, editors and shell "
                "commands: the same unrestricted Yazi as Files",
                "selected file is a start point, not a root jail or sandbox",
                "opens the parent folder and type-checks/preloads visible siblings "
                "using file(1), pdftoppm, resvg and ImageMagick; use trusted or "
                "isolated folders for untrusted files",
                "Enter/open follows the owner's openers (macOS default application), "
                "which may fetch resources and run SVG scripts; RexDeck never invokes "
                "an external opener automatically",
            ),
            default_layout=Layout("right", 50),
            prerequisites=(
                ("pdftoppm", "https://poppler.freedesktop.org/"),
                ("resvg", "https://github.com/linebender/resvg/releases/tag/v0.49.0"),
            ),
        ),
    )
}
PLUGINS = tuple(INTEGRATIONS)
if len({integration.title for integration in INTEGRATIONS.values()}) != len(PLUGINS):
    raise RuntimeError("Integration titles must be unique; they delimit owned loaders")


def get(plugin: str) -> Integration:
    try:
        return INTEGRATIONS[plugin]
    except KeyError:
        raise RuntimeError(f"Unknown integration: {plugin}") from None

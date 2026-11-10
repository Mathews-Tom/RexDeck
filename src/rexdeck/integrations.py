"""Identity, context, argv, layout and disclosure of each hosted terminal tool.

An integration is one installed upstream terminal program opened in a native Rex
pane. The shared host (``host/main.lua`` and ``host/launch.py``) and lifecycle
ownership consume only these fields; adding an interactive tool means adding a
descriptor module under ``rexdeck/catalogue/`` plus its plugin README, not
copying lifecycle or focus code.
"""

from __future__ import annotations

import importlib
import pkgutil
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
        "interpreter",
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
    # Owned frontends run from this distribution's interpreter, not a PATH tool.
    owned_module: str | None = None

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
        if self.owned_module is None and not self.versions:
            raise ValueError(f"{self.id} must qualify at least one tool version")
        if self.owned_module is not None and (
            not self.owned_module.startswith("rexdeck.")
            or self.args != ("-I", "-m", self.owned_module)
            or self.versions
        ):
            raise ValueError(
                "Owned frontends must use isolated in-package module launch"
            )

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
            "owned_module": self.owned_module,
        }


# The original plugins keep their published order; later catalogue modules
# follow them sorted by id, so listings never depend on filesystem order.
_ORDER = ("files", "git", "markdown", "usage", "github", "viewer", "clickup")


def _discover() -> dict[str, Integration]:
    package = importlib.import_module("rexdeck.catalogue")
    names = [module.name for module in pkgutil.iter_modules(package.__path__)]
    missing = [name for name in _ORDER if name not in names]
    if missing:
        raise RuntimeError("Missing catalogue modules: " + ", ".join(missing))
    ordered = [*_ORDER, *sorted(name for name in names if name not in _ORDER)]
    catalogue: dict[str, Integration] = {}
    for name in ordered:
        integration = getattr(
            importlib.import_module(f"rexdeck.catalogue.{name}"), "INTEGRATION", None
        )
        if not isinstance(integration, Integration):
            raise RuntimeError(f"Catalogue module {name} must define INTEGRATION")
        if integration.id != name:
            raise RuntimeError(
                f"Catalogue module {name} must describe integration id {name!r}"
            )
        catalogue[name] = integration
    return catalogue


INTEGRATIONS: dict[str, Integration] = _discover()
PLUGINS = tuple(INTEGRATIONS)
if len({integration.title for integration in INTEGRATIONS.values()}) != len(PLUGINS):
    raise RuntimeError("Integration titles must be unique; they delimit owned loaders")


def get(plugin: str) -> Integration:
    try:
        return INTEGRATIONS[plugin]
    except KeyError:
        raise RuntimeError(f"Unknown integration: {plugin}") from None

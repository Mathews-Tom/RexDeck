"""Public package metadata only; never uploads repository content."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from rexdeck.actions.api import clean

SYSTEMS = {
    "npm": "npm",
    "pypi": "PyPI",
    "go": "Go",
    "maven": "Maven",
    "cargo": "crates.io",
    "nuget": "NuGet",
    "rubygems": "RubyGems",
}
GRAPH_SYSTEMS = frozenset({"npm", "cargo", "maven", "pypi"})
NO_ADVISORIES = "None known to deps.dev/OSV at fetch time; not a safety verdict."


class DepsError(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected string")
    return clean(value)


def obj(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def items(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Expected array")
    return value


@dataclass(frozen=True)
class Coordinates:
    system: str
    name: str
    version: str = ""

    def __post_init__(self) -> None:
        if self.system not in SYSTEMS:
            raise ValueError("Choose npm, pypi, go, maven, cargo, nuget or rubygems.")
        for value in (self.name, self.version):
            if value != clean(value) or any(c.isspace() for c in value):
                raise ValueError(
                    "Package coordinates cannot contain controls or whitespace."
                )
            if len(value) > 1024 or value in (".", ".."):
                raise ValueError("Invalid package coordinate.")
        if not self.name:
            raise ValueError("Enter a package name.")

    @property
    def path(self) -> str:
        base = f"/systems/{self.system}/packages/{quote(self.name, safe='')}"
        if self.version:
            base += f"/versions/{quote(self.version, safe='')}"
        return base

    @property
    def url(self) -> str:
        if not self.version:
            raise ValueError("Select a version before opening its page.")
        return (
            f"https://deps.dev/{self.system}/{quote(self.name, safe='')}/"
            f"{quote(self.version, safe='')}"
        )


def version_key(value: object) -> Coordinates:
    key = obj(value)
    return Coordinates(
        text(key["system"]).lower(), text(key["name"]), text(key["version"])
    )


@dataclass(frozen=True)
class Dependency:
    package: Coordinates
    relation: str


@dataclass(frozen=True)
class Advisory:
    id: str
    summary: str
    severity: str
    source: str


@dataclass(frozen=True)
class Snapshot:
    package: Coordinates
    licenses: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    advisories: tuple[Advisory, ...]
    fetched_at: str
    graph_notice: str = ""


def parse_graph(data: dict[str, Any]) -> tuple[tuple[Dependency, ...], str]:
    dependencies = []
    errors = [text(data.get("error", ""))]
    nodes = items(data["nodes"])
    if not nodes:
        raise ValueError("Missing graph root")
    for raw in nodes:
        node = obj(raw)
        relation = text(node["relation"])
        if relation not in ("SELF", "DIRECT", "INDIRECT"):
            raise ValueError("Unknown graph relation")
        package = version_key(node["versionKey"])
        for error in items(node.get("errors", [])):
            errors.append(text(error))
        if relation != "SELF":
            dependencies.append(Dependency(package, relation.lower()))
    joined = "; ".join(e for e in errors if e)
    return tuple(dependencies), f"Incomplete graph: {joined}" if joined else ""


def parse_osv(data: dict[str, Any]) -> tuple[Advisory, ...]:
    advisories = []
    for raw in items(data.get("vulns", [])):
        entry = obj(raw)
        severity = []
        for raw_score in items(entry.get("severity", [])):
            score = obj(raw_score)
            severity.append(f"{text(score['type'])}: {text(score['score'])}")
        database_severity = obj(entry.get("database_specific", {})).get("severity")
        if database_severity:
            severity.append(text(database_severity))
        advisories.append(
            Advisory(
                text(entry["id"]),
                text(entry.get("summary") or "Summary not provided"),
                "; ".join(severity) or "Not provided",
                "OSV",
            )
        )
    return tuple(advisories)


def validate_url(url: str, package: Coordinates) -> None:
    try:
        parts = urlsplit(url)
    except ValueError as error:
        raise DepsError("error", "Refusing an untrusted package URL.") from error
    if (
        url != package.url
        or parts.scheme != "https"
        or parts.netloc != "deps.dev"
        or parts.query
        or parts.fragment
        or re.search(r"[\x00-\x20\x7f]", url)
    ):
        raise DepsError("error", "Refusing an untrusted package URL.")


class Client:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def _request(
        self,
        http: httpx.Client,
        source: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = (
                http.get(url) if payload is None else http.post(url, json=payload)
            )
        except httpx.RequestError as error:
            raise DepsError(
                "network", f"{source}: network unavailable; r retries."
            ) from error
        status = response.status_code
        if status == 404:
            raise DepsError(
                "empty", f"{source}: package/version or metadata not found; r retries."
            )
        if status == 429:
            raise DepsError("rate-limit", f"{source}: rate limited; wait then press r.")
        if status in (401, 403):
            raise DepsError("permission", f"{source}: access refused; r retries.")
        if status >= 500:
            raise DepsError("network", f"{source}: service unavailable; r retries.")
        if status != 200:
            raise DepsError("error", f"{source}: unexpected HTTP {status}; r retries.")
        try:
            return obj(response.json())
        except (ValueError, TypeError) as error:
            raise DepsError(
                "error", f"{source}: malformed response; r retries."
            ) from error

    def fetch(
        self, selected: Coordinates, *, cancelled: Callable[[], bool] | None = None
    ) -> Snapshot:
        try:
            with httpx.Client(
                transport=self.transport, timeout=20, follow_redirects=False
            ) as http:

                def request(
                    source: str, url: str, payload: dict[str, Any] | None = None
                ) -> dict[str, Any]:
                    if cancelled is not None and cancelled():
                        raise DepsError("cancelled", "Superseded package request.")
                    return self._request(http, source, url, payload)

                package = selected
                if not package.version:
                    data = request("deps.dev", "https://api.deps.dev/v3" + package.path)
                    defaults = [
                        obj(v)
                        for v in items(data["versions"])
                        if obj(v).get("isDefault") is True
                    ]
                    if len(defaults) != 1:
                        raise DepsError(
                            "error", "deps.dev has no unique default; enter a version."
                        )
                    package = version_key(defaults[0]["versionKey"])
                base = "https://api.deps.dev/v3" + package.path
                version = request("deps.dev", base)
                package = version_key(version["versionKey"])
                licenses = tuple(text(v) for v in items(version["licenses"]))
                # Validate links too, but never open arbitrary API-provided links.
                for link in items(version.get("links", [])):
                    text(obj(link)["url"])
                graph: tuple[Dependency, ...] = ()
                notice = (
                    f"Dependency graph not provided by deps.dev for {package.system}"
                )
                if package.system in GRAPH_SYSTEMS:
                    try:
                        graph, notice = parse_graph(
                            request("deps.dev", base + ":dependencies")
                        )
                    except DepsError as error:
                        if error.state != "empty":
                            raise
                advisories = [
                    Advisory(
                        text(obj(key)["id"]),
                        "Details from OSV below when available",
                        "Not provided",
                        "deps.dev",
                    )
                    for key in items(version["advisoryKeys"])
                ]
                payload: dict[str, Any] = {
                    "package": {
                        "name": package.name,
                        "ecosystem": SYSTEMS[package.system],
                    },
                    "version": package.version,
                }
                seen: set[str] = set()
                while True:
                    data = request("OSV", "https://api.osv.dev/v1/query", payload)
                    advisories.extend(parse_osv(data))
                    token = text(data.get("next_page_token", ""))
                    if not token:
                        break
                    if token in seen:
                        raise ValueError("Repeated OSV pagination token")
                    seen.add(token)
                    payload["page_token"] = token
                return Snapshot(
                    package,
                    licenses,
                    graph,
                    tuple(advisories),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    notice,
                )
        except (KeyError, ValueError, TypeError) as error:
            raise DepsError(
                "error",
                "Malformed package metadata; r retries or choose another version.",
            ) from error

    def open_package(self, package: Coordinates) -> None:
        url = package.url
        validate_url(url, package)
        try:
            subprocess.run(
                ["/usr/bin/open", url], check=True, capture_output=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DepsError(
                "error", "Browser open failed; press o to retry."
            ) from error

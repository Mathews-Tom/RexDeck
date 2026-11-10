"""Anonymous OCI metadata reads only: tags and manifests, never blobs."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import parse_http_list, parse_keqv_list

import httpx

from rexdeck.actions.api import clean

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
ACCEPT = ", ".join((OCI_INDEX, OCI_MANIFEST, DOCKER_INDEX, DOCKER_MANIFEST))
PAGE_SIZE = 30
TAG = re.compile(r"[\w][\w.-]{0,127}", re.ASCII)
COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*")


class ImagesError(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Reference:
    registry: str
    repository: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", self.registry)
            or "." not in self.registry
            or any(
                not x or x.startswith("-") or x.endswith("-")
                for x in self.registry.split(".")
            )
            or not self.repository
            or len(self.repository) > 255
            or not all(COMPONENT.fullmatch(p) for p in self.repository.split("/"))
        ):
            raise ValueError(
                "Enter a public registry/repository (no URL, tag, digest or credentials)."
            )

    @classmethod
    def parse(cls, value: str) -> Reference:
        parts = value.strip().split("/")
        registry = "docker.io"
        if len(parts) > 1 and (
            "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
        ):
            registry = parts.pop(0).lower()
        if registry in ("index.docker.io", "registry-1.docker.io"):
            registry = "docker.io"
        if registry == "docker.io" and len(parts) == 1:
            parts.insert(0, "library")
        return cls(registry, "/".join(parts))

    @property
    def normalized(self) -> str:
        return f"{self.registry}/{self.repository}"

    @property
    def host(self) -> str:
        return "registry-1.docker.io" if self.registry == "docker.io" else self.registry

    @property
    def base(self) -> str:
        return f"https://{self.host}/v2/{self.repository}"

    @property
    def web_url(self) -> str:
        if self.registry == "docker.io":
            if self.repository.startswith("library/"):
                return f"https://hub.docker.com/_/{self.repository.removeprefix('library/')}"
            return f"https://hub.docker.com/r/{self.repository}/tags"
        # GHCR does not encode the source GitHub repository in an image reference.
        raise ImagesError(
            "error",
            "No exact upstream web page can be derived for this registry; refusing to guess.",
        )


def validate_url(url: str, reference: Reference) -> None:
    if url != reference.web_url:
        raise ImagesError("error", "Refusing an untrusted upstream URL.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "hub.docker.com"
        or parsed.query
        or parsed.fragment
    ):
        raise ImagesError("error", "Refusing an untrusted upstream URL.")


def challenge(header: str, reference: Reference) -> tuple[str, dict[str, str]]:
    scheme, _, fields = header.partition(" ")
    if scheme.lower() != "bearer":
        raise ImagesError(
            "permission",
            "Anonymous pull unavailable; private registries are unsupported. r retries.",
        )
    parts = parse_http_list(fields)
    params = parse_keqv_list(parts)
    if len(params) != len(parts) or not params.get("realm"):
        raise ValueError("Malformed bearer challenge")
    realm = params["realm"]
    parsed = urlsplit(realm)
    allowed = {reference.host}
    if reference.registry == "docker.io":
        allowed.add("auth.docker.io")
    if (
        not realm.isprintable()
        or realm != urlunsplit(parsed)
        or parsed.scheme != "https"
        or parsed.netloc not in allowed
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Untrusted anonymous token realm")
    scope = f"repository:{reference.repository}:pull"
    if params.get("scope", scope) != scope:
        raise ValueError("Unexpected anonymous token scope")
    query = {"scope": scope}
    if "service" in params:
        query["service"] = params["service"]
    return realm, query


@dataclass(frozen=True)
class TagPage:
    tags: tuple[str, ...]
    next_cursor: str | None
    fetched_at: str


@dataclass(frozen=True)
class Manifest:
    tag: str
    digest: str
    media_type: str
    platforms: tuple[str, ...]
    fetched_at: str


def parse_manifest(response: httpx.Response, tag: str) -> Manifest:
    data = response.json()
    if not isinstance(data, dict) or data.get("schemaVersion") != 2:
        raise ValueError("Malformed manifest")
    media_type = (
        data.get("mediaType") or response.headers.get("Content-Type", "").split(";")[0]
    )
    if media_type not in (OCI_INDEX, OCI_MANIFEST, DOCKER_INDEX, DOCKER_MANIFEST):
        raise ValueError("Unsupported manifest media type")
    digest = (
        response.headers.get("Docker-Content-Digest")
        or "sha256:" + hashlib.sha256(response.content).hexdigest()
    )
    if not re.fullmatch(r"[a-z0-9]+(?:[+._-][a-z0-9]+)*:[A-Fa-f0-9]{32,}", digest):
        raise ValueError("Malformed manifest digest")
    platforms: list[str] = []
    if media_type in (OCI_INDEX, DOCKER_INDEX):
        descriptors = data["manifests"]
        if not isinstance(descriptors, list):
            raise ValueError("Malformed image index")
        for descriptor in descriptors:
            platform = descriptor.get("platform", {})
            if not isinstance(platform, dict):
                raise ValueError("Malformed platform")
            if platform:
                values = [
                    platform.get("os", "unknown"),
                    platform.get("architecture", "unknown"),
                ]
                if platform.get("variant"):
                    values.append(platform["variant"])
                if not all(isinstance(v, str) for v in values):
                    raise ValueError("Malformed platform")
                platforms.append(clean("/".join(values)))
    return Manifest(tag, digest, media_type, tuple(dict.fromkeys(platforms)), now())


class Client:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self._tokens: dict[Reference, str] = {}

    def _status(self, response: httpx.Response) -> None:
        code = response.status_code
        if code in (401, 403):
            raise ImagesError(
                "permission",
                "Anonymous pull refused; private registries are unsupported. r retries.",
            )
        if code == 404:
            raise ImagesError(
                "empty", "Repository or tag not found. r retries or e edits."
            )
        if code == 429:
            raise ImagesError(
                "rate-limit",
                "Registry rate limit (including Docker Hub anonymous limits); wait then r.",
            )
        if code >= 500:
            raise ImagesError("network", "Registry unavailable; r retries.")
        if code != 200:
            raise ImagesError("error", f"Unexpected registry HTTP {code}; r retries.")

    def _request(self, reference: Reference, url: str) -> httpx.Response:
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=20,
                follow_redirects=False,
                trust_env=False,
            ) as http:
                response = http.get(
                    url,
                    headers={
                        "Accept": ACCEPT,
                        **(
                            {"Authorization": "Bearer " + self._tokens[reference]}
                            if reference in self._tokens
                            else {}
                        ),
                    },
                )
                if response.status_code == 401:
                    self._tokens.pop(reference, None)
                    realm, params = challenge(
                        response.headers.get("WWW-Authenticate", ""), reference
                    )
                    token_response = http.get(realm, params=params)
                    self._status(token_response)
                    body = token_response.json()
                    token = body.get("token") or body.get("access_token")
                    if (
                        not isinstance(token, str)
                        or not token
                        or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
                    ):
                        raise ValueError("Malformed anonymous token")
                    self._tokens[reference] = token
                    del token, body, token_response
                    response = http.get(
                        url,
                        headers={
                            "Accept": ACCEPT,
                            "Authorization": "Bearer " + self._tokens[reference],
                        },
                    )
                self._status(response)
                return response
        except httpx.RequestError:
            raise ImagesError("network", "Network unavailable; r retries.") from None
        except (httpx.InvalidURL, ValueError, KeyError, TypeError, AttributeError):
            raise ImagesError(
                "error",
                "Malformed registry metadata or untrusted token challenge; r retries.",
            ) from None

    def tags(self, reference: Reference, cursor: str = "") -> TagPage:
        if cursor and not TAG.fullmatch(cursor):
            raise ImagesError("error", "Invalid pagination cursor.")
        params: dict[str, str | int] = {"n": PAGE_SIZE}
        if cursor:
            params["last"] = cursor
        response = self._request(
            reference, reference.base + "/tags/list?" + urlencode(params)
        )
        try:
            body = response.json()
            values: Any = body["tags"]
            if values is None:
                values = []
            if (
                not isinstance(values, list)
                or not all(isinstance(v, str) and TAG.fullmatch(v) for v in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("Malformed tag list")
            tags = tuple(values)
            next_cursor = None
            if "next" in response.links:
                link = urlsplit(
                    urljoin(str(response.url), response.links["next"]["url"])
                )
                query = parse_qs(link.query, strict_parsing=True)
                if (
                    link.scheme != "https"
                    or link.netloc != reference.host
                    or link.path != f"/v2/{reference.repository}/tags/list"
                    or link.fragment
                    or set(query) - {"last", "n"}
                    or len(query.get("last", [])) != 1
                    or (
                        "n" in query
                        and (len(query["n"]) != 1 or not query["n"][0].isdigit())
                    )
                ):
                    raise ValueError("Untrusted pagination link")
                next_cursor = query["last"][0]
                if not TAG.fullmatch(next_cursor):
                    raise ValueError("Malformed pagination cursor")
            elif len(tags) >= PAGE_SIZE:
                next_cursor = tags[-1]
            if next_cursor == cursor or (
                next_cursor and (not tags or next_cursor != tags[-1])
            ):
                raise ValueError("Non-advancing pagination")
            return TagPage(tags, next_cursor, now())
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ImagesError(
                "error", "Malformed tag page or untrusted pagination link; r retries."
            ) from None

    def manifest(self, reference: Reference, tag: str) -> Manifest:
        if not TAG.fullmatch(tag):
            raise ImagesError("error", "Invalid image tag.")
        response = self._request(reference, reference.base + "/manifests/" + tag)
        try:
            return parse_manifest(response, tag)
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ImagesError("error", "Malformed image manifest; r retries.") from None

    def open_reference(self, reference: Reference) -> None:
        url = reference.web_url
        validate_url(url, reference)
        try:
            subprocess.run(
                ["/usr/bin/open", url], check=True, capture_output=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            raise ImagesError("error", "Browser open failed; o retries.") from None

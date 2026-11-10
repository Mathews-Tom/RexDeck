"""On-demand GitHub notifications; every request is explicitly GET."""

from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


class InboxError(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def clean(value: str) -> str:
    value = re.sub(
        r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)", "", value
    )
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))


@dataclass(frozen=True)
class Context:
    host: str
    account: str
    gh: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Context:
        values = os.environ if env is None else env
        host, account, gh = (
            values.get(k, "") for k in ("GH_HOST", "REXDECK_GITHUB_ACCOUNT", "GH_PATH")
        )
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", account)
            or not Path(gh).is_absolute()
            or values.get("GH_PROMPT_DISABLED") != "1"
            or any(k in values for k in ("GH_REPO", "GH_FORCE_TTY", "CLICOLOR_FORCE"))
        ):
            raise InboxError(
                "context",
                "Launch Inbox with account=LOGIN in the verified RexDeck account context.",
            )
        return cls(host, account, gh)


@dataclass(frozen=True)
class Notification:
    id: str
    repo: str
    title: str
    kind: str
    reason: str
    updated: str
    subject_url: str | None


@dataclass(frozen=True)
class Page:
    items: tuple[Notification, ...]
    number: int
    has_next: bool


REASONS = ("all", "review_requested", "mention", "ci_activity", "others")
PER_PAGE = 50


def filter_reason(
    items: tuple[Notification, ...], reason: str
) -> tuple[Notification, ...]:
    if reason not in REASONS:
        raise ValueError("Unknown notification reason filter")
    if reason == "all":
        return items
    named = REASONS[1:4]
    return tuple(
        item
        for item in items
        if (item.reason not in named if reason == "others" else item.reason == reason)
    )


def parse_page(data: object, number: int) -> Page:
    if not isinstance(data, list) or len(data) > PER_PAGE:
        raise ValueError("Expected notification page")
    items = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("Invalid notification")
        subject, repository = entry["subject"], entry["repository"]
        if not isinstance(subject, dict) or not isinstance(repository, dict):
            raise ValueError("Invalid subject or repository")
        fields = (
            entry["id"],
            repository["full_name"],
            subject["title"],
            subject["type"],
            entry["reason"],
            entry["updated_at"],
        )
        if not all(isinstance(field, str) and field for field in fields):
            raise ValueError("Invalid notification fields")
        url = subject.get("url")
        if url is not None and not isinstance(url, str):
            raise ValueError("Invalid subject URL")
        items.append(Notification(*fields, url))
    return Page(tuple(items), number, len(items) == PER_PAGE)


def subject_target(item: Notification) -> tuple[str, str]:
    """Only canonical API paths for the exact notification repository are usable."""
    repo_parts = item.repo.split("/")
    if (
        len(repo_parts) != 2
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", repo_parts[0])
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo_parts[1])
        or repo_parts[1] in (".", "..")
    ):
        raise InboxError("url", "Cannot map this thread safely: invalid repository.")
    url = item.subject_url
    if item.kind == "CheckSuite" and url is None:
        return "check-suites", ""
    if not isinstance(url, str):
        raise InboxError("url", "Cannot map this thread safely: missing subject URL.")
    match = re.fullmatch(
        r"https://api\.github\.com/repos/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/(pulls|issues|commits|check-suites)/([A-Za-z0-9]+)",
        url,
    )
    kinds = {
        "pulls": "PullRequest",
        "issues": "Issue",
        "commits": "Commit",
        "check-suites": "CheckSuite",
    }
    if (
        not url.isascii()
        or not match
        or f"{match[1]}/{match[2]}" != item.repo
        or kinds[match[3]] != item.kind
    ):
        raise InboxError(
            "url",
            "Cannot map this thread safely: unsupported or mismatched subject URL.",
        )
    kind, identifier = match[3], match[4]
    pattern = r"[0-9a-fA-F]{40}" if kind == "commits" else r"[1-9][0-9]*"
    if not re.fullmatch(pattern, identifier):
        raise InboxError(
            "url", "Cannot map this thread safely: invalid subject identifier."
        )
    return kind, identifier


class Client:
    def __init__(self, context: Context) -> None:
        self.context = context

    def _request(self, endpoint: str, *, login: bool = False) -> str:
        argv = [
            self.context.gh,
            "api",
            "--method",
            "GET",
            "--hostname",
            self.context.host,
            endpoint,
        ]
        if login:
            argv.extend(("--jq", ".login"))
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise InboxError(
                "network", "GitHub request unavailable or timed out. Press r to retry."
            ) from error
        if result.returncode:
            # Never display stderr or payloads: gh may include personal request content.
            error_text = result.stderr.lower()
            status = re.search(r"\bhttp\s+(\d{3})\b", error_text)
            code = status[1] if status else ""
            if (
                code == "429"
                or "rate limit" in error_text
                or "abuse detection" in error_text
            ):
                raise InboxError(
                    "rate-limit", "GitHub rate limit reached. Wait, then press r."
                )
            if code in ("401", "403", "404") or result.returncode == 4:
                raise InboxError(
                    "permission",
                    f"GitHub {('HTTP ' + code) if code else 'authentication refusal'}: existing auth lacks access. Press r to retry after the owner resolves access outside RexDeck.",
                )
            raise InboxError(
                "network", "GitHub request failed. Check connectivity, then press r."
            )
        return result.stdout

    def verify_account(self) -> None:
        if (
            self._request("user", login=True).strip().casefold()
            != self.context.account.casefold()
        ):
            raise InboxError(
                "permission",
                "GitHub account changed; existing auth no longer matches the selected account. Press r after restoring existing auth outside RexDeck.",
            )

    def notifications(self, number: int = 1, participating: bool = False) -> Page:
        if number < 1:
            raise ValueError("Page must be positive")
        self.verify_account()
        query = urlencode(
            {
                "all": "false",
                "participating": str(participating).lower(),
                "per_page": PER_PAGE,
                "page": number,
            }
        )
        try:
            return parse_page(
                json.loads(self._request(f"notifications?{query}")), number
            )
        except (ValueError, TypeError, KeyError) as error:
            raise InboxError(
                "error", "GitHub returned invalid notifications. Press r to retry."
            ) from error

    def thread_url(self, item: Notification) -> str:
        kind, identifier = subject_target(item)
        if self.context.host != "github.com":
            raise InboxError("url", "Only exact github.com thread links are supported.")
        if kind != "check-suites":
            path = {"pulls": "pull", "issues": "issues", "commits": "commit"}[kind]
            return f"https://github.com/{item.repo}/{path}/{identifier}"
        if not identifier:
            return f"https://github.com/{item.repo}/actions"
        # Suite IDs are NOT workflow run IDs. Resolve the documented suite filter,
        # then require the exact matching run and its canonical browser URL.
        self.verify_account()
        query = urlencode({"check_suite_id": identifier, "per_page": 100, "page": 1})
        try:
            data: Any = json.loads(
                self._request(f"repos/{item.repo}/actions/runs?{query}")
            )
            if not isinstance(data, dict) or data.get("total_count") != 1:
                raise ValueError("No unique Actions run for suite")
            runs = data["workflow_runs"]
            if not isinstance(runs, list) or len(runs) != 1:
                raise ValueError("No unique run")
            run = runs[0]
            if (
                not isinstance(run, dict)
                or type(run.get("id")) is not int
                or run["id"] <= 0
                or run.get("check_suite_id") != int(identifier)
            ):
                raise ValueError("Mismatched suite")
            expected = f"https://github.com/{item.repo}/actions/runs/{run['id']}"
            if run.get("html_url") != expected:
                raise ValueError("Mismatched browser URL")
            return expected
        except (ValueError, TypeError, KeyError) as error:
            raise InboxError(
                "url",
                "Cannot map this check suite to an exact GitHub Actions thread; refusing to open.",
            ) from error

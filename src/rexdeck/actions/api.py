"""Read-only Actions requests through the launcher's verified GitHub CLI context."""

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
from urllib.parse import urlencode, urlsplit


class ActionsError(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def clean(text: str) -> str:
    """Do not let API names or log escape sequences control the terminal."""
    text = re.sub(
        r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)", "", text
    )
    return "".join(
        c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C")
    )


@dataclass(frozen=True)
class Context:
    host: str
    owner: str
    repo: str
    gh: str
    account: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Context:
        values = os.environ if env is None else env
        host = values.get("GH_HOST", "")
        parts = values.get("GH_REPO", "").split("/")
        gh = values.get("GH_PATH", "")
        account = values.get("REXDECK_GITHUB_ACCOUNT", "")
        if (
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)
            or len(parts) != 3
            or parts[0] != host
            or not all(
                re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in (".", "..")
                for p in parts[1:]
            )
            or not Path(gh).is_absolute()
            or not re.fullmatch(r"[A-Za-z0-9-]+", account)
            or values.get("GH_PROMPT_DISABLED") != "1"
        ):
            raise ActionsError(
                "context",
                "Launch Actions from RexDeck in a verified GitHub worktree with account=LOGIN; launcher environment is missing or invalid.",
            )
        return cls(host, parts[1], parts[2], gh, account)

    @property
    def endpoint(self) -> str:
        return f"repos/{self.owner}/{self.repo}/actions"


@dataclass(frozen=True)
class RunFilter:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in ("branch", "head_sha") or not self.value.strip():
            raise ValueError("Choose a nonempty branch or commit SHA.")
        if self.kind == "head_sha" and not re.fullmatch(
            r"[0-9a-fA-F]{7,40}", self.value
        ):
            raise ValueError("Commit SHA must contain 7–40 hexadecimal characters.")


def current_filter() -> RunFilter:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        if branch:
            return RunFilter("branch", branch)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        return RunFilter("head_sha", sha)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ActionsError(
            "context", "Cannot read the current worktree branch or detached HEAD."
        ) from error


@dataclass(frozen=True)
class Run:
    id: int
    name: str
    status: str
    conclusion: str
    branch: str
    sha: str
    url: str


@dataclass(frozen=True)
class Job:
    id: int
    name: str
    status: str
    conclusion: str


@dataclass(frozen=True)
class RunsPage:
    runs: tuple[Run, ...]
    page: int
    total: int
    has_next: bool


def parse_runs(data: dict[str, Any], page: int) -> RunsPage:
    runs = tuple(
        Run(
            int(r["id"]),
            str(r.get("name") or r.get("display_title") or "Workflow"),
            str(r["status"]),
            str(r.get("conclusion") or ""),
            str(r.get("head_branch") or ""),
            str(r["head_sha"]),
            str(r["html_url"]),
        )
        for r in data["workflow_runs"]
    )
    total = int(data["total_count"])
    # GitHub caps filtered run searches at 1,000 results. Narrow the filter for older runs.
    return RunsPage(runs, page, total, bool(runs) and page * 30 < min(total, 1000))


def parse_jobs(data: dict[str, Any]) -> tuple[Job, ...]:
    return tuple(
        Job(
            int(j["id"]),
            str(j["name"]),
            str(j["status"]),
            str(j.get("conclusion") or ""),
        )
        for j in data["jobs"]
    )


def log_tail(text: str) -> str:
    return clean("\n".join(text.splitlines()[-200:]))


class Client:
    def __init__(self, context: Context) -> None:
        self.context = context

    def _request(
        self, endpoint: str, *, log: bool = False, jq: str | None = None
    ) -> str:
        argv = [self.context.gh, "api", "--hostname", self.context.host, endpoint]
        if log:
            # Modern gh otherwise refuses ANSI-bearing logs even on a captured pipe.
            # This never reaches a terminal: logs() sanitizes before returning a tail.
            argv.append("--allow-escape-sequences")
        if jq is not None:
            argv.extend(("--jq", jq))
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ActionsError(
                "network",
                "GitHub CLI unavailable or request timed out. Press r to retry.",
            ) from error
        if result.returncode:
            # Never display gh stderr: it may echo sensitive request/log content.
            status = re.search(r"\bHTTP\s+(\d{3})\b", result.stderr, re.IGNORECASE)
            code = status.group(1) if status else ""
            message = result.stderr.lower()
            if log and "unknown flag" in message:
                raise ActionsError(
                    "error", "gh 2.97 or newer is required to read job logs."
                )
            if code == "429" or any(
                text in message
                for text in ("api rate limit exceeded", "secondary rate limit")
            ):
                raise ActionsError(
                    "rate-limit",
                    "GitHub rate limit reached. Wait for the limit to reset, then press r.",
                )
            if code == "410":
                raise ActionsError(
                    "unavailable", "GitHub HTTP 410: log expired/unavailable."
                )
            if code in ("403", "404"):
                raise ActionsError(
                    "permission",
                    f"GitHub HTTP {code}: repository, Actions, or logs unavailable to this account. Press r to retry.",
                )
            if code == "401" or any(
                word in result.stderr.lower()
                for word in ("authenticate", "authentication", "token", "not logged")
            ):
                raise ActionsError(
                    "auth",
                    "GitHub authentication failed. Repair existing gh auth outside RexDeck, then press r.",
                )
            raise ActionsError(
                "network",
                "GitHub request failed. Check connectivity/service availability, then press r.",
            )
        return result.stdout

    def verify_account(self) -> None:
        login = self._request("user", jq=".login").strip()
        if login.casefold() != self.context.account.casefold():
            raise ActionsError(
                "auth",
                "GitHub account changed. Restore the selected account's existing "
                "authentication outside RexDeck, then press r.",
            )

    def _json(self, endpoint: str) -> dict[str, Any]:
        try:
            data: object = json.loads(self._request(endpoint))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return data
        except (ValueError, TypeError) as error:
            raise ActionsError(
                "error", "GitHub returned invalid data. Press r to retry."
            ) from error

    def runs(self, selected: RunFilter, page: int = 1) -> RunsPage:
        if page < 1 or page > 34:
            raise ValueError("Invalid run page")
        query = urlencode({selected.kind: selected.value, "per_page": 30, "page": page})
        try:
            return parse_runs(self._json(f"{self.context.endpoint}/runs?{query}"), page)
        except (KeyError, TypeError, ValueError) as error:
            raise ActionsError(
                "error", "GitHub returned invalid workflow runs. Press r to retry."
            ) from error

    def jobs(self, run_id: int) -> tuple[Job, ...]:
        result: list[Job] = []
        page = 1
        try:
            while True:
                data = self._json(
                    f"{self.context.endpoint}/runs/{run_id}/jobs?per_page=100&page={page}"
                )
                jobs = parse_jobs(data)
                result.extend(jobs)
                if not jobs or len(result) >= int(data["total_count"]):
                    return tuple(result)
                page += 1
        except (KeyError, TypeError, ValueError) as error:
            raise ActionsError(
                "error", "GitHub returned invalid jobs. Press r to retry."
            ) from error

    def logs(self, job_id: int) -> str:
        return log_tail(
            self._request(f"{self.context.endpoint}/jobs/{job_id}/logs", log=True)
        )

    def open_run(self, run: Run) -> None:
        url = urlsplit(run.url)
        expected = f"/{self.context.owner}/{self.context.repo}/actions/runs/{run.id}"
        if (
            url.scheme != "https"
            or url.netloc != "github.com"
            or self.context.host != "github.com"
            or not url.path.isascii()
            or url.path.lower() != expected.lower()
            or url.query
            or url.fragment
            or any(c.isspace() for c in run.url)
        ):
            raise ActionsError(
                "error",
                "Refusing browser link: expected this run's exact https://github.com URL.",
            )
        try:
            subprocess.run(
                ["/usr/bin/open", run.url], check=True, capture_output=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ActionsError(
                "error", "Could not open the selected run in the browser."
            ) from error

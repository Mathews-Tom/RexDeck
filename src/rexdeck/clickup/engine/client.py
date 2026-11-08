"""Fixed-origin ClickUp HTTP with process-wide pacing and safe diagnostics."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import unquote, urlsplit

import httpx

from .credentials import EXPORT_LINE, TOKEN_ENV
from .diagnostics import SEVERITY_ERROR, Diagnostic, EngineError

API_ROOT = "https://api.clickup.com/api/v2"
TIMEOUT_SECONDS = 30.0
MIN_REQUEST_INTERVAL = 1.0
_REQUEST_LOCK = threading.Lock()
_last_request_start: float | None = None

#: `params` values: a sequence repeats the key, which is how ClickUp spells a
#: multi-valued filter (`statuses[]=open&statuses[]=in progress`).
Params = Mapping[str, str | int | bool | Sequence[str]]


class Client:
    """A ClickUp session retaining request counts, never raw response bodies."""

    def __init__(
        self, token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._token = token
        self._http = httpx.Client(
            base_url=API_ROOT,
            headers={"Authorization": token, "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            follow_redirects=False,
        )
        self._calls = 0

    @property
    def calls(self) -> int:
        """How many requests this client has made."""
        return self._calls

    def get(self, path: str, params: Params | None = None) -> Mapping[str, object]:
        """GET `path` under the API root and return the decoded JSON object."""
        return self._request("GET", path, params=params)

    def put(self, path: str, body: Mapping[str, object]) -> Mapping[str, object]:
        """PUT `body` to `path` and return the decoded JSON object."""
        return self._request("PUT", path, body=body)

    def post(self, path: str, body: Mapping[str, object]) -> Mapping[str, object]:
        """POST `body` to `path` and return the decoded JSON object."""
        return self._request("POST", path, body=body)

    def _before_send(self, method: str, path: str) -> None:
        """Allow callers to recheck authorization immediately before transport."""

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        global _last_request_start
        if not _relative_path(path):
            raise EngineError(
                Diagnostic(
                    code="usage/bad-path",
                    severity=SEVERITY_ERROR,
                    message="ClickUp API paths must be relative to the fixed API origin",
                    subject="path",
                    evidence={},
                    supported_fixes=("/task/<task-id>",),
                ),
                exit_code=2,
            )
        safe_path = path.replace(self._token, "[REDACTED]") if self._token else path
        try:
            # Pace request starts across identities without holding slow I/O under the lock.
            with _REQUEST_LOCK:
                if _last_request_start is not None:
                    delay = MIN_REQUEST_INTERVAL - (
                        time.monotonic() - _last_request_start
                    )
                    if delay > 0:
                        time.sleep(delay)
                _last_request_start = time.monotonic()
            self._before_send(method, path)
            self._calls += 1
            response = self._http.request(
                method, path, params=dict(params or {}), json=body
            )
        except httpx.HTTPError as error:
            raise _unreachable(method, safe_path, error) from None
        if not 200 <= response.status_code < 300:
            raise _failed(method, safe_path, response)
        try:
            payload = json.loads(response.content)
        except (ValueError, UnicodeDecodeError):
            raise _bad_response(
                method, safe_path, response.status_code, "body is not valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise _bad_response(
                method, safe_path, response.status_code, "body is not a JSON object"
            )
        # Strip echoed credentials before projections, receipts or UI see them.
        return cast("Mapping[str, object]", _redact(payload, self._token))

    def close(self) -> None:
        self._http.close()


def _relative_path(path: str) -> bool:
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    decoded = unquote(path)
    return (
        bool(path)
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and not decoded.startswith("//")
        and "\\" not in decoded
        and not any(part in {".", ".."} for part in decoded.split("/"))
        and not any(ord(character) < 33 for character in decoded)
    )


def _redact(value: object, token: str) -> object:
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]") if token else value
    if isinstance(value, dict):
        return {
            str(_redact(key, token)): _redact(item, token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    return value


def _failed(method: str, path: str, response: httpx.Response) -> EngineError:
    if response.status_code == 429:
        raw_retry_after = response.headers.get("Retry-After", "")
        retry_after = int(raw_retry_after) if raw_retry_after.isdigit() else None
        return EngineError(
            Diagnostic(
                code="transport/rate-limited",
                severity=SEVERITY_ERROR,
                message="ClickUp rate-limited the request",
                subject=path,
                evidence={
                    "method": method,
                    "path": path,
                    "status": 429,
                    "retry_after": retry_after,
                },
                supported_fixes=(f"sleep {retry_after or 60}",),
            )
        )
    if response.status_code in (401, 403):
        access_restricted = response.status_code == 403
        if response.status_code == 401:
            try:
                payload = response.json()
                code = payload.get("ECODE") if isinstance(payload, dict) else None
                access_restricted = isinstance(code, str) and code.startswith(
                    ("ACCESS_", "TEAM_", "OAUTH_023", "OAUTH_027")
                )
            except ValueError:
                pass
        return EngineError(
            Diagnostic(
                code="auth/rejected",
                severity=SEVERITY_ERROR,
                message="ClickUp rejected the API token",
                subject=TOKEN_ENV,
                evidence={
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "access_restricted": access_restricted,
                },
                supported_fixes=(EXPORT_LINE,),
            )
        )
    return _bad_response(method, path, response.status_code, "the request failed")


def _bad_response(method: str, path: str, status: int, reason: str) -> EngineError:
    """Build the finding for a failing status, or a body that is not usable JSON.

    The fix splits on whether the status is worth retrying. Telling a caller to
    wait and try again after a 404 would send them round a loop that cannot
    terminate, and the ID they passed is the thing that is actually wrong.
    """
    retryable = status >= 500
    return EngineError(
        Diagnostic(
            code="transport/bad-response",
            severity=SEVERITY_ERROR,
            message=f"ClickUp returned a response this engine cannot use: {reason}",
            subject=path,
            evidence={
                "method": method,
                "path": path,
                "status": status,
                "reason": reason,
            },
            supported_fixes=("sleep 5",) if retryable else ("<id>",),
        )
    )


def _unreachable(method: str, path: str, error: httpx.HTTPError) -> EngineError:
    return EngineError(
        Diagnostic(
            code="transport/unreachable",
            severity=SEVERITY_ERROR,
            message="ClickUp could not be reached",
            subject=path,
            evidence={"method": method, "path": path, "error": type(error).__name__},
            supported_fixes=("curl -sS https://api.clickup.com/api/v2/team",),
        )
    )

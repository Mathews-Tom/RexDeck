"""Read inherited token environment references; never credential files or argv."""

from __future__ import annotations

import os
import re

from .diagnostics import SEVERITY_ERROR, Diagnostic, EngineError

TOKEN_ENV = "CLICKUP_API_TOKEN"
EXPORT_LINE = f"export {TOKEN_ENV}=pk_your_token_here"


def validate_env_name(name: str) -> None:
    """Require a reference in the dedicated ClickUp credential namespace."""
    if re.fullmatch(r"CLICKUP_[A-Z0-9_]+", name) is None:
        raise ValueError("token environment reference must match CLICKUP_[A-Z0-9_]+")


def read_token(env_name: str = TOKEN_ENV) -> str:
    """Return the API token from the environment, or fail with the export line.

    Environment only. A flag lands the token in shell history and a config file
    lands it in a commit, and both outlive the session that needed it.
    """
    validate_env_name(env_name)
    token = os.environ.get(env_name, "").strip()
    if not token:
        raise EngineError(
            Diagnostic(
                code="auth/missing-credential",
                severity=SEVERITY_ERROR,
                message=f"{env_name} is unset",
                subject=env_name,
                evidence={"variable": env_name},
                supported_fixes=(f"export {env_name}=<token>",),
            )
        )
    return token

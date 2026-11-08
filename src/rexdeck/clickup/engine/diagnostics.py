"""Coded diagnostics — every finding a verb reports, in a form a caller can act on."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITIES: tuple[str, ...] = (SEVERITY_ERROR, SEVERITY_WARNING)

#: Exit 0 is the absence of a failure, so it is not one a failure may carry.
#: 1 is a blocking finding or an operational failure; 2 is a usage error.
EXIT_CODES: tuple[int, ...] = (1, 2)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One coded finding.

    Args:
        code: Namespace/slug, e.g. ``registry/stale``.
        severity: ``error`` or ``warning``; any error sets exit 1.
        message: One sentence, no trailing period.
        subject: What the finding is about — an ID, a name, a path.
        evidence: The numbers that locate it.
        supported_fixes: Commands or field names, never prose advice. A
            diagnostic a caller cannot act on is a defect, so this must not be
            empty.

    Not hashable: `evidence` is a dict, so the `__hash__` that `frozen=True`
    generates raises `TypeError`. Diagnostics are collected in lists and
    counted, never used as set members or dict keys.
    """

    code: str
    severity: str
    message: str
    subject: str
    evidence: dict[str, object]
    supported_fixes: tuple[str, ...]

    def __post_init__(self) -> None:
        # Severity decides the exit code. A typo would downgrade a blocking
        # finding to nothing at all and the run would exit 0 on an error, so
        # reject it where it is written rather than where it is counted.
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"unknown severity {self.severity!r} on diagnostic {self.code!r};"
                f" expected one of {SEVERITIES}"
            )
        # A finding with nothing to do about it is a defect by contract. Left
        # unenforced, the two engines would quietly diverge on whether one is
        # allowed, which is exactly the divergence the shared core prevents.
        if not self.supported_fixes:
            raise ValueError(f"diagnostic {self.code!r} carries no supported fixes")
        # `evidence` is typed `dict[str, object]`, so a datetime or a set passes
        # construction and passes `as_payload`, then raises inside `render` —
        # after the verb has spent its API calls, destroying the receipt that is
        # the entire `--json` output. Fail where the bad value was written.
        try:
            json.dumps(self.evidence)
        except TypeError as error:
            raise ValueError(
                f"diagnostic {self.code!r} has evidence that will not serialise: {error}"
            ) from error


def as_payload(diagnostic: Diagnostic) -> dict[str, object]:
    """Return the receipt-serialisable form of one diagnostic."""
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "message": diagnostic.message,
        "subject": diagnostic.subject,
        "evidence": dict(diagnostic.evidence),
        "supported_fixes": list(diagnostic.supported_fixes),
    }


def severity_counts(diagnostics: Sequence[Diagnostic]) -> dict[str, int]:
    """Count diagnostics per severity, with every severity present even at zero.

    A missing key reads as "not measured"; a zero reads as "measured, none found".
    """
    counts = dict.fromkeys(SEVERITIES, 0)
    for diagnostic in diagnostics:
        counts[diagnostic.severity] += 1
    return counts


def has_error(diagnostics: Sequence[Diagnostic]) -> bool:
    """Return True when any diagnostic is error severity, which sets exit 1."""
    return any(diagnostic.severity == SEVERITY_ERROR for diagnostic in diagnostics)


class EngineError(Exception):
    """A coded failure that aborts a verb and becomes its receipt's diagnostic.

    A verb fails at whichever layer notices — registry, transport, projection —
    and the receipt is assembled one layer above all of them. Raising carries
    the finding up without every call site threading a nullable diagnostic back
    through its return type.

    Args:
        diagnostic: The finding. Error severity, because a warning does not
            abort a verb: warnings ride along in the receipt of a run that
            completed.
        exit_code: 1 for a blocking finding or an operational failure, 2 for a
            usage error. Transport failure is not usage failure, so the caller
            states which this is rather than the exception guessing from the
            code's namespace.
    """

    def __init__(self, diagnostic: Diagnostic, *, exit_code: int = 1) -> None:
        if diagnostic.severity != SEVERITY_ERROR:
            raise ValueError(
                f"diagnostic {diagnostic.code!r} is {diagnostic.severity!r};"
                " only an error-severity finding aborts a verb"
            )
        if exit_code not in EXIT_CODES:
            raise ValueError(
                f"exit code {exit_code} is not one a failure may carry;"
                f" expected one of {EXIT_CODES}"
            )
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic
        self.exit_code = exit_code

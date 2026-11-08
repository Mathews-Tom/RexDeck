"""Machine-readable verdicts for reads, staged writes and request accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .diagnostics import Diagnostic, as_payload, severity_counts

RECEIPT_SCHEMA_VERSION = 1


def digest_json(value: object) -> str:
    """Return the sha256 of `value` serialised canonically.

    One digest function for every digest in the contract — the argument digest,
    the registry digest, and the field digest that guards an ``apply``. Keys are
    sorted so an equal mapping always digests equal regardless of build order.
    """
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RegistryStamp:
    """Registry digest and topology-change time; null means no completed sync."""

    digest: str | None
    synced_at: str | None


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """What the verb returned, and whether the caller is seeing all of it."""

    rows: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class Mutation:
    """The write a verb staged or applied.

    `field_digest` covers only the fields being written: a whole-object digest
    would abort constantly on unrelated remote activity.
    """

    target: str
    field_digest: str
    diff: dict[str, object]


@dataclass(frozen=True, slots=True)
class Receipt:
    """Everything one verb run has to say for itself."""

    verb: str
    args_digest: str
    registry: RegistryStamp
    result: ResultSummary
    diagnostics: tuple[Diagnostic, ...]
    budget: Mapping[str, object]
    mutation: Mutation | None = None

    def payload(self) -> dict[str, object]:
        """Return the receipt as plain JSON-serialisable data."""
        payload: dict[str, object] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "verb": self.verb,
            "input": {"args_digest": self.args_digest},
            "registry": {
                "digest": self.registry.digest,
                "synced_at": self.registry.synced_at,
            },
            "result": {"rows": self.result.rows, "truncated": self.result.truncated},
            "severity_counts": severity_counts(self.diagnostics),
            "diagnostics": [as_payload(diagnostic) for diagnostic in self.diagnostics],
            "budget": dict(self.budget),
        }
        if self.mutation is not None:
            payload["mutation"] = {
                "target": self.mutation.target,
                "field_digest": self.mutation.field_digest,
                "diff": dict(self.mutation.diff),
            }
        return payload


def render(receipt: Receipt) -> str:
    """Serialize the receipt as JSON without changing its public field names."""
    return json.dumps(receipt.payload(), ensure_ascii=False)

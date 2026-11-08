"""Stable receipt structure, canonical digests, and actionable diagnostics."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from rexdeck.clickup.engine.diagnostics import (
    Diagnostic,
    EngineError,
    as_payload,
    has_error,
    severity_counts,
)
from rexdeck.clickup.engine.receipts import (
    Mutation,
    Receipt,
    RegistryStamp,
    ResultSummary,
    digest_json,
    render,
)


def finding(severity: str = "warning") -> Diagnostic:
    return Diagnostic(
        code="sample/actionable",
        severity=severity,
        message="sample finding",
        subject="synaa0001",
        evidence={"count": 2},
        supported_fixes=("refresh",),
    )


def receipt() -> Receipt:
    return Receipt(
        verb="tasks",
        args_digest=digest_json({"list": "70000009999"}),
        registry=RegistryStamp(digest="1" * 64, synced_at="2026-11-01T00:00:00Z"),
        result=ResultSummary(rows=2, truncated=False),
        diagnostics=(finding(),),
        budget={"api_calls_made": 1},
    )


def test_read_receipt_preserves_schema_stamps_results_and_diagnostics() -> None:
    payload = json.loads(render(receipt()))
    assert payload == {
        "schema_version": 1,
        "verb": "tasks",
        "input": {"args_digest": digest_json({"list": "70000009999"})},
        "registry": {"digest": "1" * 64, "synced_at": "2026-11-01T00:00:00Z"},
        "result": {"rows": 2, "truncated": False},
        "severity_counts": {"error": 0, "warning": 1},
        "diagnostics": [
            {
                "code": "sample/actionable",
                "severity": "warning",
                "message": "sample finding",
                "subject": "synaa0001",
                "evidence": {"count": 2},
                "supported_fixes": ["refresh"],
            }
        ],
        "budget": {"api_calls_made": 1},
    }
    assert "mutation" not in payload


def test_write_receipt_preserves_the_field_digest_and_proposed_diff() -> None:
    mutation = Mutation(
        target="synaa0001",
        field_digest="2" * 64,
        diff={"status": {"from": "open", "to": "complete", "digest": "3" * 64}},
    )
    payload = json.loads(render(replace(receipt(), verb="touch", mutation=mutation)))
    assert payload["mutation"] == {
        "target": "synaa0001",
        "field_digest": "2" * 64,
        "diff": {"status": {"from": "open", "to": "complete", "digest": "3" * 64}},
    }


def test_canonical_digest_ignores_mapping_order_but_not_changed_values() -> None:
    assert digest_json({"a": 1, "b": {"c": 2, "d": 3}}) == digest_json(
        {"b": {"d": 3, "c": 2}, "a": 1}
    )
    assert digest_json({"a": 1}) != digest_json({"a": 2})
    assert len(digest_json({"sample": "unicode ✓"})) == 64


def test_diagnostics_report_zero_counts_without_downgrading_errors() -> None:
    assert severity_counts(()) == {"error": 0, "warning": 0}
    assert not has_error((finding(),))
    assert has_error((finding(), finding("error")))
    assert severity_counts((finding(), finding("error"))) == {"error": 1, "warning": 1}
    assert as_payload(finding())["supported_fixes"] == ["refresh"]


@pytest.mark.parametrize("severity", ("", "info", "ERROR", "warn"))
def test_invalid_diagnostic_severity_is_refused(severity: str) -> None:
    with pytest.raises(ValueError, match="severity"):
        finding(severity)


def test_findings_require_a_supported_fix_and_serializable_evidence() -> None:
    with pytest.raises(ValueError, match="supported fixes"):
        replace(finding(), supported_fixes=())
    with pytest.raises(ValueError, match="serialise"):
        replace(finding(), evidence={"invalid": {1, 2}})


def test_only_error_findings_and_failure_exit_codes_can_abort() -> None:
    with pytest.raises(ValueError, match="only an error"):
        EngineError(finding())
    for exit_code in (0, 3, -1):
        with pytest.raises(ValueError, match="exit code"):
            EngineError(finding("error"), exit_code=exit_code)
    failure = EngineError(finding("error"), exit_code=2)
    assert failure.exit_code == 2
    assert failure.diagnostic.code == "sample/actionable"

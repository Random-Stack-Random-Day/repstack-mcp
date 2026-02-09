"""Tests for ingest_policy: status rules, confidence, strictness. Stateless — no storage."""

import pytest

from repstack.ingest import ingest_log_impl
from repstack.models import (
    IngestLogInput,
    IngestOptions,
    LogInput,
    UserInput,
)


def test_messy_workout_returns_needs_clarification() -> None:
    """Messy input (invalid lines + one valid Squat 225x5) -> confidence < 0.70, needs_clarification."""
    content = """Maybe
135

Squat 225x5
"""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="text", content=content),
        options=IngestOptions(allow_llm=False),
    )
    result = ingest_log_impl(payload)
    assert result.status == "needs_clarification"
    assert result.log_id is None
    assert result.summary.confidence < 0.70


def test_missing_date_with_strictness_strict_blocks() -> None:
    """When strictness=strict and session date is missing, status is needs_clarification."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5",
        ),
        options=IngestOptions(strictness="strict"),  # no session_date_hint
    )
    result = ingest_log_impl(payload)
    assert result.status == "needs_clarification"
    assert result.log_id is None
    assert any(i.type == "missing_date" and i.severity == "blocking" for i in result.issues)


def test_confidence_penalties_applied() -> None:
    """Confidence is reduced by missing_date and unmapped exercise."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nUnknownExercise,95,10",
        ),
        options=IngestOptions(),  # no date hint -> missing_date
    )
    result = ingest_log_impl(payload)
    assert result.summary.confidence <= 0.85
    assert result.summary.confidence >= 0.25


def test_log_id_none_when_status_needs_clarification() -> None:
    """When status is needs_clarification, log_id is None."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content="exercise,weight\nOnlyTwoCols,135"),
    )
    result = ingest_log_impl(payload)
    assert result.status == "needs_clarification"
    assert result.log_id is None


def test_ok_with_date_hint_returns_log_id() -> None:
    """When date hint provided and data valid, status ok and log_id set."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5",
        ),
        options=IngestOptions(session_date_hint="2025-02-01"),
    )
    result = ingest_log_impl(payload)
    assert result.status == "ok"
    assert result.log_id is not None
    assert result.summary.confidence >= 0.70


def test_tool_output_contains_no_ellipses() -> None:
    """MCP tool response JSON must not contain truncation markers (...)."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nSquat,225,5\nDeadlift,315,3\n",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    result = ingest_log_impl(payload)
    out = result.model_dump()
    import json
    js = json.dumps(out, default=str)
    assert "..." not in js, "Tool output must not contain ellipses truncation"


def test_confidence_lower_with_warnings() -> None:
    """Clean CSV with date -> high confidence; missing_date or unmapped lowers it."""
    clean = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nSquat,225,5",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    r_clean = ingest_log_impl(clean)
    assert r_clean.summary.confidence >= 0.85

    unmapped = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nSomeWeirdLift,95,8",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    r_unmapped = ingest_log_impl(unmapped)
    assert r_unmapped.summary.confidence < r_clean.summary.confidence
    assert any(i.type == "unmapped_exercise" for i in r_unmapped.issues)

    no_date = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content="exercise,weight,reps\nBench Press,135,5"),
        options=IngestOptions(),
    )
    r_no_date = ingest_log_impl(no_date)
    assert r_no_date.summary.confidence < r_clean.summary.confidence

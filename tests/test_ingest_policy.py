"""Tests for ingest_policy v1.1: status rules, confidence, storage gate, strictness."""

import tempfile
from pathlib import Path

import pytest

from repstack_mcp.ingest import ingest_log_impl
from repstack_mcp.models import (
    IngestLogInput,
    IngestOptions,
    LogInput,
    UserInput,
)
from repstack_mcp.storage import Storage


def test_messy_workout_returns_needs_clarification_and_not_stored() -> None:
    """Messy input (invalid lines + one valid Squat 225x5) -> confidence < 0.70, needs_clarification, not stored."""
    content = """Maybe
135
Squat 225x5
"""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="text", content=content),
        options=IngestOptions(allow_llm=False),
    )
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        result = ingest_log_impl(payload, storage=storage)
        storage.close()
    assert result.status == "needs_clarification"
    assert result.log_id is None
    assert result.summary.confidence < 0.70
    # One valid set may be parsed but we still don't store
    assert not any(i.severity == "blocking" for i in result.issues) or result.summary.confidence < 0.70


def test_missing_date_with_strictness_strict_blocks() -> None:
    """When strictness=strict and session date is missing, status is needs_clarification and log not stored."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5",
        ),
        options=IngestOptions(strictness="strict"),  # no session_date_hint
    )
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        result = ingest_log_impl(payload, storage=storage)
        storage.close()
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
    result = ingest_log_impl(payload, storage=None)
    # missing_date -0.15, unmapped -0.10 -> 0.75
    assert result.summary.confidence <= 0.85
    assert result.summary.confidence >= 0.25


def test_log_not_stored_when_status_needs_clarification() -> None:
    """When status is needs_clarification, log_id is None and DB has no new log."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content="exercise,weight\nOnlyTwoCols,135"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        result = ingest_log_impl(payload, storage=storage)
        assert result.status == "needs_clarification"
        assert result.log_id is None
        # DB should have no logs for this user (we might have created user)
        logs = storage.get_logs_for_user(result.user_id)
        assert len(logs) == 0
        storage.close()


def test_ok_with_date_hint_stored() -> None:
    """When date hint provided and data valid, status ok and log stored."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5",
        ),
        options=IngestOptions(session_date_hint="2025-02-01"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        result = ingest_log_impl(payload, storage=storage)
        storage.close()
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
    result = ingest_log_impl(payload, storage=None)
    out = result.model_dump()
    import json
    js = json.dumps(out, default=str)
    assert "..." not in js, "Tool output must not contain ellipses truncation"


def test_confidence_lower_with_warnings() -> None:
    """Clean CSV with date -> high confidence; missing_date or unmapped lowers it."""
    # Clean: date hint + all mapped
    clean = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nSquat,225,5",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    r_clean = ingest_log_impl(clean, storage=None)
    assert r_clean.summary.confidence >= 0.85

    # With unmapped exercise -> lower
    unmapped = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\nSomeWeirdLift,95,8",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    r_unmapped = ingest_log_impl(unmapped, storage=None)
    assert r_unmapped.summary.confidence < r_clean.summary.confidence
    assert any(i.type == "unmapped_exercise" for i in r_unmapped.issues)

    # Missing date -> lower
    no_date = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content="exercise,weight,reps\nBench Press,135,5"),
        options=IngestOptions(),
    )
    r_no_date = ingest_log_impl(no_date, storage=None)
    assert r_no_date.summary.confidence < r_clean.summary.confidence

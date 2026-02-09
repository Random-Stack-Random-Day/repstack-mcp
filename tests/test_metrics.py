"""Metrics computation test: tonnage, volume_spike, stateless API, guardrails."""

import tempfile
from pathlib import Path

import pytest

from repstack.ingest import ingest_log_impl
from repstack.metrics import MAX_SETS, MAX_SESSIONS, compute_metrics_impl
from repstack.models import (
    ComputeMetricsInput,
    DateRange,
    IngestLogInput,
    LogInput,
    UserInput,
)
from repstack.storage import Storage


def _ingest_csv(
    storage: Storage,
    csv_content: str,
    session_date: str,
    user_id: str | None = None,
) -> tuple[str, list[dict]]:
    """Ingest CSV and return (user_id, list of canonical session dicts from this log)."""
    from repstack.models import IngestOptions

    payload = IngestLogInput(
        user=UserInput(user_id=user_id, default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content=csv_content),
        options=IngestOptions(session_date_hint=session_date),
    )
    result = ingest_log_impl(payload, storage=storage)
    assert result.status == "ok", result.issues
    sessions = [s.model_dump() for s in result.canonical_log.sessions]
    return result.user_id, sessions


def test_metrics_tonnage_and_volume_spike() -> None:
    """Metrics from provided sessions (no storage passed to compute_metrics_impl)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "metrics_test.db"
        storage = Storage(str(db))

        # Week 1: 100 lb * 5 + 100 * 5 = 1000 tonnage, 2 sets
        uid, sessions1 = _ingest_csv(
            storage,
            "exercise,weight,reps\nSquat,100,5\nSquat,100,5",
            "2025-01-06",  # Monday
        )
        # Week 2: 200*5 + 200*5 = 2000 tonnage (>25% spike)
        _, sessions2 = _ingest_csv(
            storage,
            "exercise,weight,reps\nSquat,200,5\nSquat,200,5",
            "2025-01-13",
            user_id=uid,
        )
        storage.close()

    sessions = sessions1 + sessions2
    payload = ComputeMetricsInput(
        sessions=sessions,
        range=DateRange(start="2025-01-01", end="2025-01-31"),
    )
    result = compute_metrics_impl(payload)
    assert result.status == "ok"
    assert len(result.weekly) >= 2
    w1 = next((w for w in result.weekly if w.week_start == "2025-01-06"), None)
    w2 = next((w for w in result.weekly if w.week_start == "2025-01-13"), None)
    assert w1 is not None
    assert w2 is not None
    assert w1.tonnage_lb == 1000.0
    assert w2.tonnage_lb == 2000.0
    assert w1.hard_sets == 2
    assert w2.hard_sets == 2
    assert "volume_spike" in w2.flags
    assert len(result.exercise_summaries) >= 1
    squat = next((e for e in result.exercise_summaries if e.exercise_id == "back_squat"), None)
    assert squat is not None
    assert squat.total_hard_sets == 4
    assert squat.sessions == 2


def test_metrics_bodyweight_excluded_from_tonnage() -> None:
    """Bodyweight sets excluded from tonnage; metrics computed from provided sessions only."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bw_metrics.db"
        storage = Storage(str(db))
        _, sessions = _ingest_csv(
            storage,
            "exercise,weight,reps,unit\n"
            "Bench Press,135,5,lb\n"
            "Pull Ups,Bodyweight,10,\n"
            "Pull Ups,+25,6,lb\n",
            "2025-01-06",
        )
        storage.close()

    payload = ComputeMetricsInput(
        sessions=sessions,
        range=DateRange(start="2025-01-01", end="2025-01-31"),
    )
    result = compute_metrics_impl(payload)
    assert result.status == "ok"
    w1 = next((w for w in result.weekly if w.week_start == "2025-01-06"), None)
    assert w1 is not None
    assert w1.tonnage_lb == 825.0
    assert w1.hard_sets == 3
    assert w1.tonnage_excluded_sets == 1
    assert w1.tonnage_unknown_sets == 0


def test_metrics_deterministic_same_input() -> None:
    """Same input always yields the same output (no storage, no randomness)."""
    sessions = [
        {
            "session_id": "s1",
            "date": "2025-01-06",
            "exercises": [
                {
                    "exercise_raw": "Squat",
                    "exercise_id": "back_squat",
                    "exercise_display": "Back Squat",
                    "sets": [
                        {"set_index": 0, "weight": 100.0, "unit": "lb", "reps": 5, "load_type": "weighted"},
                        {"set_index": 1, "weight": 100.0, "unit": "lb", "reps": 5, "load_type": "weighted"},
                    ],
                }
            ],
        }
    ]
    payload = ComputeMetricsInput(
        sessions=sessions,
        range=DateRange(start="2025-01-01", end="2025-01-31"),
    )
    result1 = compute_metrics_impl(payload)
    result2 = compute_metrics_impl(payload)
    assert result1.model_dump() == result2.model_dump()
    assert result1.status == "ok"
    assert len(result1.weekly) == 1
    assert result1.weekly[0].tonnage_lb == 1000.0


def test_metrics_guardrail_max_sessions() -> None:
    """Payload with more than MAX_SESSIONS returns needs_clarification and payload_too_large."""
    sessions = [
        {
            "session_id": f"s{i}",
            "date": "2025-01-06",
            "exercises": [],
        }
        for i in range(MAX_SESSIONS + 1)
    ]
    payload = ComputeMetricsInput(sessions=sessions, range=DateRange(start="2025-01-01", end="2025-01-31"))
    result = compute_metrics_impl(payload)
    assert result.status == "needs_clarification"
    assert any(i.type == "payload_too_large" and "sessions" in i.message for i in result.issues)


def test_metrics_guardrail_max_sets() -> None:
    """Payload with more than MAX_SETS returns needs_clarification and payload_too_large."""
    # One session with many sets
    sets = [
        {"set_index": i, "weight": 100.0, "unit": "lb", "reps": 5, "load_type": "weighted"}
        for i in range(MAX_SETS + 1)
    ]
    sessions = [
        {
            "session_id": "s1",
            "date": "2025-01-06",
            "exercises": [
                {
                    "exercise_raw": "Squat",
                    "exercise_id": "back_squat",
                    "exercise_display": "Back Squat",
                    "sets": sets,
                }
            ],
        }
    ]
    payload = ComputeMetricsInput(sessions=sessions, range=DateRange(start="2025-01-01", end="2025-01-31"))
    result = compute_metrics_impl(payload)
    assert result.status == "needs_clarification"
    assert any(i.type == "payload_too_large" and "sets" in i.message for i in result.issues)


def test_metrics_logs_input_path() -> None:
    """compute_metrics_impl accepts logs= (list of { canonical_json: { sessions } }) with no storage."""
    logs = [
        {
            "canonical_json": {
                "sessions": [
                    {
                        "session_id": "s1",
                        "date": "2025-01-06",
                        "exercises": [
                            {
                                "exercise_raw": "Squat",
                                "exercise_id": "back_squat",
                                "exercise_display": "Back Squat",
                                "sets": [
                                    {"set_index": 0, "weight": 100.0, "unit": "lb", "reps": 5, "load_type": "weighted"},
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    ]
    payload = ComputeMetricsInput(logs=logs, range=DateRange(start="2025-01-01", end="2025-01-31"))
    result = compute_metrics_impl(payload)
    assert result.status == "ok"
    assert len(result.weekly) == 1
    assert result.weekly[0].tonnage_lb == 500.0

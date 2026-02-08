"""Metrics computation test: tonnage and volume_spike flag."""

import tempfile
from pathlib import Path

import pytest

from repstack.ingest import ingest_log_impl
from repstack.metrics import compute_metrics_impl
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
) -> str:
    from repstack.models import IngestOptions

    payload = IngestLogInput(
        user=UserInput(user_id=user_id, default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="csv", content=csv_content),
        options=IngestOptions(session_date_hint=session_date),
    )
    result = ingest_log_impl(payload, storage=storage)
    assert result.status == "ok", result.issues
    return result.user_id


def test_metrics_tonnage_and_volume_spike() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "metrics_test.db"
        storage = Storage(str(db))

        # Week 1: 100 lb * 5 + 100 * 5 = 1000 tonnage, 2 sets
        uid = _ingest_csv(
            storage,
            "exercise,weight,reps\nSquat,100,5\nSquat,100,5",
            "2025-01-06",  # Monday
        )
        # Week 2: 200*5 + 200*5 = 2000 tonnage (>25% spike) and 2 more hard sets (same user)
        _ingest_csv(
            storage,
            "exercise,weight,reps\nSquat,200,5\nSquat,200,5",
            "2025-01-13",
            user_id=uid,
        )

        payload = ComputeMetricsInput(
            user_id=uid,
            range=DateRange(start="2025-01-01", end="2025-01-31"),
        )
        result = compute_metrics_impl(payload, storage=storage)
        storage.close()
        assert result.status == "ok"
        assert len(result.weekly) >= 2
        # Find week 2025-01-06 and 2025-01-13
        w1 = next((w for w in result.weekly if w.week_start == "2025-01-06"), None)
        w2 = next((w for w in result.weekly if w.week_start == "2025-01-13"), None)
        assert w1 is not None
        assert w2 is not None
        assert w1.tonnage_lb == 1000.0
        assert w2.tonnage_lb == 2000.0
        assert w1.hard_sets == 2
        assert w2.hard_sets == 2
        # Week 2 should have volume_spike flag (tonnage 1000 -> 2000 is 100% increase)
        assert "volume_spike" in w2.flags

        assert len(result.exercise_summaries) >= 1
        squat = next((e for e in result.exercise_summaries if e.exercise_id == "back_squat"), None)
        assert squat is not None
        assert squat.total_hard_sets == 4
        assert squat.sessions == 2


def test_metrics_bodyweight_excluded_from_tonnage() -> None:
    """Bodyweight sets have weight=null, load_type bodyweight; excluded from tonnage; hard_sets unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bw_metrics.db"
        storage = Storage(str(db))
        # Bench 135x5 (675 lb tonnage), Pull Ups Bodyweight x10 (excluded), Pull Ups +25 x6 (150 lb from added only)
        uid = _ingest_csv(
            storage,
            "exercise,weight,reps,unit\n"
            "Bench Press,135,5,lb\n"
            "Pull Ups,Bodyweight,10,\n"
            "Pull Ups,+25,6,lb\n",
            "2025-01-06",
        )
        payload = ComputeMetricsInput(
            user_id=uid,
            range=DateRange(start="2025-01-01", end="2025-01-31"),
        )
        result = compute_metrics_impl(payload, storage=storage)
        storage.close()
        assert result.status == "ok"
        w1 = next((w for w in result.weekly if w.week_start == "2025-01-06"), None)
        assert w1 is not None
        # Tonnage: 135*5 + 25*6 = 675 + 150 = 825 (no 0*10 for bodyweight)
        assert w1.tonnage_lb == 825.0
        assert w1.hard_sets == 3  # all three sets count as hard (working)
        assert w1.tonnage_excluded_sets == 1  # one bodyweight set
        assert w1.tonnage_unknown_sets == 0

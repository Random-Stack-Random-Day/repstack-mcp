#!/usr/bin/env python3
"""
Ingest sample CSV/JSON files then run compute_metrics. Uses repstack directly.
Usage: python scripts/test_metrics.py [sample_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repstack.ingest import ingest_log_impl
from repstack.metrics import compute_metrics_impl
from repstack.models import (
    ComputeMetricsInput,
    DateRange,
    IngestLogInput,
    IngestOptions,
    LogInput,
    UserInput,
)
from repstack.storage import Storage


SAMPLES_DIR = ROOT / "samples"
DEFAULT_DB = ROOT / "repstack_test.db"


def main() -> None:
    samples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLES_DIR
    storage = Storage(DEFAULT_DB)
    user = UserInput(default_unit="lb", timezone="UTC")

    # Ingest a couple of good logs (different dates) so we have data for metrics
    uid = None
    for name, path in [
        ("week1", samples_dir / "good_workout.csv"),
        ("week2", samples_dir / "good_workout_with_date.csv"),
    ]:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        date_hint = "2025-01-27" if name == "week1" else "2025-02-01"
        # Use same user_id for both so metrics see all logs
        payload = IngestLogInput(
            user=UserInput(user_id=uid, default_unit="lb", timezone="UTC"),
            log_input=LogInput(content_type="csv", content=content),
            options=IngestOptions(session_date_hint=date_hint),
        )
        result = ingest_log_impl(payload, storage=storage)
        uid = result.user_id
        print(f"Ingested {name}: log_id={result.log_id}  status={result.status}  sets={result.summary.sets_detected}")

    if not uid:
        print("No logs ingested (sample files missing?). Exiting.")
        storage.close()
        sys.exit(1)

    # Fetch canonical logs and compute metrics (stateless: pass logs, no storage to compute_metrics)
    range_ = DateRange(start="2025-01-01", end="2025-02-28")
    logs = storage.get_logs_for_user(uid, range_.start, range_.end)
    storage.close()
    metrics_input = ComputeMetricsInput(logs=logs, range=range_)
    metrics = compute_metrics_impl(metrics_input)

    print("\n" + "=" * 60)
    print("COMPUTE_METRICS")
    print("=" * 60)
    print(f"Status: {metrics.status}")
    print(f"User: {uid}")
    print(f"Range: {metrics.range.start} to {metrics.range.end}")
    print("\nWeekly:")
    for w in metrics.weekly:
        flags = f"  flags={w.flags}" if w.flags else ""
        print(f"  {w.week_start}: sessions={w.sessions}  hard_sets={w.hard_sets}  tonnage_lb={w.tonnage_lb}{flags}")
    print("\nExercise summaries (top 5):")
    for ex in metrics.exercise_summaries[:5]:
        print(f"  {ex.exercise_id}: sessions={ex.sessions}  best_e1rm={ex.best_e1rm}  hard_sets={ex.total_hard_sets}")

    print(f"\nDB: {DEFAULT_DB}")


if __name__ == "__main__":
    main()

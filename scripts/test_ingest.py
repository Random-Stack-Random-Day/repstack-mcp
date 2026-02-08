#!/usr/bin/env python3
"""
Run sample files through repstack ingest_log. Uses the repstack package directly.
(no MCP server needed). Usage: python scripts/test_ingest.py [sample_dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Project root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repstack.ingest import ingest_log_impl
from repstack.models import IngestLogInput, IngestOptions, LogInput, UserInput
from repstack.normalize import format_set_display
from repstack.storage import Storage


SAMPLES_DIR = ROOT / "samples"
DEFAULT_DB = ROOT / "repstack_test.db"

# Map file to content_type and optional options
SAMPLES = [
    ("good_workout.csv", "csv", {}),
    ("good_workout_with_date.csv", "csv", {"session_date_hint": "2025-02-01"}),
    ("csv_app_export.csv", "csv", {}),
    ("hevy_style_export.csv", "csv", {"session_date_hint": "2025-02-05"}),
    ("unmapped_close_match.csv", "csv", {"session_date_hint": "2025-01-15"}),
    ("bodyweight_heavy_session.csv", "csv", {}),
    ("bad_workout.csv", "csv", {}),
    ("empty_columns.csv", "csv", {}),
    ("good_workout.json", "json", {}),
    ("good_workout_sessions.json", "json", {"session_date_hint": "2025-02-01"}),
    ("json_user.js", "json", {}),
    ("bad_workout.json", "json", {}),
    ("invalid_workout.json", "json", {}),
    ("good_workout.txt", "text", {"allow_llm": False}),
    ("messy_workout.txt", "text", {"allow_llm": False}),
]


def main() -> None:
    samples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLES_DIR
    if not samples_dir.is_dir():
        print(f"Not a directory: {samples_dir}")
        sys.exit(1)

    storage = Storage(DEFAULT_DB)
    user = UserInput(default_unit="lb", timezone="America/New_York")

    for filename, content_type, opts in SAMPLES:
        path = samples_dir / filename
        if not path.exists():
            print(f"[SKIP] {filename} (file not found)")
            continue

        content = path.read_text(encoding="utf-8", errors="replace")
        options = IngestOptions(**opts) if opts else None
        payload = IngestLogInput(
            user=user,
            log_input=LogInput(content_type=content_type, content=content),
            options=options,
        )

        print(f"\n{'='*60}")
        print(f"FILE: {filename}  (content_type={content_type})")
        print("=" * 60)

        result = ingest_log_impl(payload, storage=storage)

        warnings_count = sum(1 for i in result.issues if i.severity == "warning")
        stored = result.log_id is not None
        print(f"Status: {result.status}")
        print(f"Stored: {'yes' if stored else 'no'}")
        print(f"Confidence: {result.summary.confidence:.2f}")
        print(f"Warnings: {warnings_count}")
        print(f"Log ID: {result.log_id or '(none)'}")
        print(f"Summary: sessions={result.summary.sessions_detected}  exercises={result.summary.exercises_detected}  sets={result.summary.sets_detected}")
        if result.issues:
            print("Issues:")
            for i in result.issues:
                print(f"  - [{i.severity}] {i.type}: {i.message}")
        if result.canonical_log.sessions:
            print("Canonical (first session):")
            sess = result.canonical_log.sessions[0]
            print(f"  date={sess.date}  exercises={len(sess.exercises)}")
            for ex in sess.exercises[:3]:
                sets_str = ", ".join(format_set_display(s) for s in ex.sets[:5])
                print(f"    {ex.exercise_display}: {sets_str}")
        print()

    storage.close()
    print(f"DB saved to {DEFAULT_DB}")


if __name__ == "__main__":
    main()

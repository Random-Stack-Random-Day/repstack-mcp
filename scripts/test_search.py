#!/usr/bin/env python3
"""
Run repstack.search_exercises against example payloads from samples/search_exercises_examples.json.
Usage: python scripts/test_search.py [path/to/search_exercises_examples.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repstack.models import SearchExercisesInput, SearchExercisesOutput, SearchExerciseHit
from repstack.normalize import search_exercises


SAMPLES_DIR = ROOT / "samples"
EXAMPLES_FILE = SAMPLES_DIR / "search_exercises_examples.json"


def main() -> None:
    examples_path = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES_FILE
    if not examples_path.exists():
        print(f"File not found: {examples_path}")
        sys.exit(1)

    data = json.loads(examples_path.read_text(encoding="utf-8"))
    if "examples" in data and isinstance(data["examples"], list) and data["examples"]:
        examples = data["examples"]
    else:
        examples = [{"name": "single", "payload": data}]

    for i, ex in enumerate(examples):
        name = ex.get("name", f"example_{i+1}")
        payload = ex.get("payload", ex)
        print(f"\n{'='*60}")
        print(f"Example: {name}")
        print("Payload:", json.dumps(payload, indent=2)[:200] + ("..." if len(json.dumps(payload)) > 200 else ""))
        print("=" * 60)
        try:
            inp = SearchExercisesInput.model_validate(payload)
            limit = inp.limit if inp.limit is not None else 20
            hits = search_exercises(
                query=inp.query,
                equipment=inp.equipment,
                movement_pattern=inp.movement_pattern,
                limit=max(0, min(limit, 100)),
            )
            out = SearchExercisesOutput(exercises=[SearchExerciseHit(**h) for h in hits])
            result = out.model_dump()
            print(f"Found {len(result['exercises'])} exercise(s)")
            for h in result["exercises"][:8]:
                print(f"  - {h['exercise_id']}: {h.get('display')} ({h.get('equipment')}, {h.get('movement_pattern')})")
            if len(result["exercises"]) > 8:
                print(f"  ... and {len(result['exercises']) - 8} more")
        except Exception as e:
            print(f"Error: {e}")

    print()


if __name__ == "__main__":
    main()

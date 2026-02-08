# Repstack test scripts

Run these from the **project root** (FitnessMCP) so imports and paths work.

## Prerequisites

Install dependencies first (use the same command you’ll use to run the script):

```bash
# If you use `python`:
pip install -r requirements.txt

# If you use `py` (Windows launcher):
py -m pip install -r requirements.txt
```

On Windows, `py` and `python` can point to different environments; install with the same one you use to run the scripts.

## test_ingest.py

Runs every sample file in `samples/` through `repstack.ingest_log` and prints status, issues, and a short canonical summary.

```bash
cd C:\Users\Admin\Documents\MiscProjects\MCPs\FitnessMCP
python scripts/test_ingest.py
```

Optional: pass a different samples directory:

```bash
python scripts/test_ingest.py path/to/samples
```

Uses a local DB at `repstack_test.db` in the project root.

## test_metrics.py

Ingests `good_workout.csv` and `good_workout_with_date.csv` (as two “weeks”), then runs `repstack.compute_metrics` for that range and prints weekly stats and exercise summaries.

```bash
python scripts/test_metrics.py
```

Also uses `repstack_test.db`.

## test_search.py

Runs `repstack.search_exercises` against example payloads from `samples/search_exercises_examples.json`.

```bash
python scripts/test_search.py
```

Optional: pass a different JSON file path.

## Sample files (samples/)

| File | Purpose |
|------|--------|
| `good_workout.csv` | Clean CSV: exercise, weight, reps, unit |
| `good_workout_with_date.csv` | CSV with date and set_type (warmup/working/top) |
| `csv_app_export.csv` | App-style CSV with date, set type, bodyweight, +25 lb |
| `hevy_style_export.csv` | Hevy/Strong-style CSV (date, workout name, set type) |
| `unmapped_close_match.csv` | Mix of mapped + unmapped names to test suggested_exercise_ids |
| `bodyweight_heavy_session.csv` | Bodyweight and +weight sets for tonnage/display tests |
| `bad_workout.csv` | Missing reps column, non-numeric weight |
| `empty_columns.csv` | Empty first row, then valid row |
| `good_workout.json` | Flat list of sets with exercise/weight/reps/unit |
| `good_workout_sessions.json` | Nested sessions → exercises → sets |
| `json_user.js` | User export with nested sessions and bodyweight sets |
| `bad_workout.json` | Valid JSON but wrong shape (no sessions/list) |
| `invalid_workout.json` | Invalid JSON (unquoted keys) |
| `good_workout.txt` | Simple "Exercise 135x5" lines (regex parse) |
| `messy_workout.txt` | Unstructured text (fails without LLM) |
| `search_exercises_examples.json` | Example payloads for repstack.search_exercises |
| `ingest_tool_examples.json` | Example payloads for repstack.ingest_log |

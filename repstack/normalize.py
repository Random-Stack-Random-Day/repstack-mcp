"""Normalization: units, dates, exercise_id slugging, canonical JSON hashing."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from .models import (
    AddedLoad,
    CanonicalLog,
    ExerciseRecord,
    SessionRecord,
    SetRecord,
)


PARSER_VERSION = "1.0.0"

# Exact synonym -> exercise_id only. No partial/startswith/endswith to avoid wrong mappings.
# Convention: snake_case with specificity (e.g. barbell_bench_press, back_squat, seated_row).
EXERCISE_ALIASES: dict[str, str] = {
    "barbell bench press": "barbell_bench_press",
    "bench press": "barbell_bench_press",
    "bench": "barbell_bench_press",
    "back squat": "back_squat",
    "squat": "back_squat",
    "squats": "back_squat",
    "deadlift": "deadlift",
    "romanian deadlift": "romanian_deadlift",
    "rdl": "romanian_deadlift",
    "barbell row": "barbell_row",
    "seated row": "seated_row",
    "overhead press": "overhead_press",
    "ohp": "overhead_press",
    "lat pulldown": "lat_pulldown",
    "pull up": "pull_up",
    "pull ups": "pull_up",
    "pull-up": "pull_up",
    "pullups": "pull_up",
    "pullup": "pull_up",
    "chin-up": "chin_up",
    "chin up": "chin_up",
    "dumbbell curl": "dumbbell_curl",
    "tricep pushdown": "tricep_pushdown",
    "leg press": "leg_press",
    "leg curl": "leg_curl",
    "leg extension": "leg_extension",
}


def slug_exercise(raw: str) -> str:
    """Turn exercise_raw into a slug for unmapped ids."""
    s = raw.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "_", s)
    return s or "unknown"


def resolve_exercise_id(exercise_raw: str) -> tuple[str, str]:
    """Return (exercise_id, exercise_display). Only exact/known synonyms map; else unmapped:<slug>."""
    key = exercise_raw.strip().lower()
    if key in EXERCISE_ALIASES:
        return (EXERCISE_ALIASES[key], exercise_raw.strip())
    slug = slug_exercise(exercise_raw)
    return (f"unmapped:{slug}", exercise_raw.strip())


def normalize_unit(unit: str | None, default: str) -> str:
    u = (unit or "").strip().lower()
    if u in ("lb", "lbs", "pound", "pounds"):
        return "lb"
    if u in ("kg", "kilo", "kilogram", "kilograms"):
        return "kg"
    return default


def normalize_date(value: str | None, hint: str | None) -> str | None:
    """Return YYYY-MM-DD or None if invalid."""
    if not value or not value.strip():
        return hint
    s = value.strip()
    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # Try common formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return hint


def canonical_sha256(log: CanonicalLog) -> str:
    """Stable SHA256 of canonical JSON (sorted keys)."""
    import json as _json
    blob = _json.dumps(log.model_dump(), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def normalize_set(
    raw: dict,
    set_index: int,
    default_unit: str,
) -> SetRecord:
    """Build a SetRecord from raw dict (e.g. from CSV/JSON/LLM)."""
    load_type = (raw.get("load_type") or raw.get("load type") or "weighted").strip().lower()
    if load_type not in ("weighted", "bodyweight", "bodyweight_plus", "assisted"):
        load_type = "weighted"

    reps = int(raw.get("reps", raw.get("Reps", 0)))
    rpe = raw.get("rpe") or raw.get("RPE")
    if rpe is not None:
        rpe = float(rpe)
    set_type = raw.get("set_type") or raw.get("Set Type")
    if set_type and set_type not in ("warmup", "working", "top", "backoff"):
        set_type = None
    notes = raw.get("notes") or raw.get("Notes")

    if load_type == "bodyweight":
        return SetRecord(
            set_index=set_index,
            weight=None,
            unit=None,
            reps=reps,
            load_type="bodyweight",
            rpe=rpe,
            set_type=set_type,
            notes=notes,
        )
    if load_type == "bodyweight_plus":
        added_load_raw = raw.get("added_load")
        if isinstance(added_load_raw, dict):
            u = normalize_unit(added_load_raw.get("unit"), default_unit)
            added_load = AddedLoad(
                value=round(float(added_load_raw.get("value", 0)), 2),
                unit=u if u in ("lb", "kg") else "lb",
            )
        else:
            added = raw.get("added_weight", raw.get("added weight"))
            if added is None:
                added = 0.0
            added = round(float(added), 2)
            added_unit = normalize_unit(
                raw.get("added_weight_unit") or raw.get("added weight unit") or raw.get("unit") or raw.get("Unit"),
                default_unit,
            )
            added_load = AddedLoad(value=added, unit=added_unit if added_unit in ("lb", "kg") else "lb")
        return SetRecord(
            set_index=set_index,
            weight=None,
            unit=None,
            reps=reps,
            load_type="bodyweight_plus",
            added_load=added_load,
            rpe=rpe,
            set_type=set_type,
            notes=notes,
        )
    # weighted
    w = raw.get("weight", raw.get("Weight", 0))
    weight = None if w is None else round(float(w), 2)
    unit = normalize_unit(
        raw.get("unit") or raw.get("Unit") or raw.get("units"),
        default_unit,
    )
    return SetRecord(
        set_index=set_index,
        weight=weight,
        unit=unit,
        reps=reps,
        load_type="weighted",
        rpe=rpe,
        set_type=set_type,
        notes=notes,
    )


def format_set_display(s: SetRecord) -> str:
    """Format a set for summaries; never show 0×reps for bodyweight (use BW×reps or BW+added×reps)."""
    if s.load_type == "bodyweight":
        return f"BW×{s.reps}"
    if s.load_type == "bodyweight_plus" and s.added_load:
        return f"BW+{s.added_load.value}{s.added_load.unit}×{s.reps}"
    if s.weight is not None and s.unit:
        return f"{s.weight}{s.unit}×{s.reps}"
    return f"?×{s.reps}"


def normalize_exercise(
    raw_name: str,
    raw_sets: list[dict],
    default_unit: str,
) -> ExerciseRecord:
    """Build ExerciseRecord from raw name and list of set dicts."""
    exercise_id, exercise_display = resolve_exercise_id(raw_name)
    sets = [
        normalize_set(s, i + 1, default_unit)
        for i, s in enumerate(raw_sets)
    ]
    return ExerciseRecord(
        exercise_raw=raw_name,
        exercise_id=exercise_id,
        exercise_display=exercise_display,
        sets=sets,
    )


def normalize_session(
    session_id: str,
    date: str | None,
    raw_exercises: list[tuple[str, list[dict]]],
    default_unit: str,
    title: str | None = None,
    notes: str | None = None,
) -> SessionRecord:
    """Build SessionRecord from raw (exercise_name, sets) list."""
    exercises = [
        normalize_exercise(name, sets, default_unit)
        for name, sets in raw_exercises
    ]
    return SessionRecord(
        session_id=session_id,
        date=date,
        title=title or None,
        notes=notes or None,
        exercises=exercises,
    )

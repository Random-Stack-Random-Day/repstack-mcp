"""Normalization: units, dates, exercise_id slugging, canonical JSON hashing."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    AddedLoad,
    CanonicalLog,
    ExerciseMapping,
    ExerciseRecord,
    SessionRecord,
    SetRecord,
)

# --- Search query normalization (deterministic, no fuzzy) ---

def normalize_search_query(q: str) -> str:
    """
    Normalize query for matching: lowercase, trim, collapse spaces, remove punctuation.
    Optional simple plural normalization: pushdowns->pushdown, flyes->fly.
    """
    if not q or not isinstance(q, str):
        return ""
    s = q.strip().lower()
    s = re.sub(r"[\-_/.,;:!'()\[\]{}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Simple deterministic plural -> singular for common exercise terms
    if s.endswith("pushdowns"):
        s = s[:-1]  # pushdowns -> pushdown
    elif s.endswith("flyes"):
        s = s[:-2]  # flyes -> fly (remove "es")
    return s


PARSER_VERSION = "1.0.0"

_DATA_DIR = Path(__file__).resolve().parent / "data"

# --- v2: Registry and source packs (lazy-loaded) ---
_registry: list[dict[str, Any]] | None = None
_registry_by_display: dict[str, dict[str, Any]] = {}
_registry_by_alias: dict[str, dict[str, Any]] = {}
_source_packs_cache: dict[str, dict[str, str]] = {}


def _load_registry() -> list[dict[str, Any]]:
    global _registry, _registry_by_display, _registry_by_alias
    if _registry is not None:
        return _registry
    path = _DATA_DIR / "exercise_registry.json"
    if not path.exists():
        _registry = []
        return _registry
    with open(path, encoding="utf-8") as f:
        _registry = json.load(f)
    for entry in _registry:
        display = entry.get("display", "")
        if display:
            _registry_by_display[display.strip().lower()] = entry
        for a in entry.get("aliases", []) or []:
            if a and a.strip().lower() not in _registry_by_display:
                _registry_by_alias.setdefault(a.strip().lower(), entry)
    return _registry


def _load_source_pack(source: str) -> dict[str, str]:
    """Load alias pack: exporter string (lower) -> exercise_id."""
    global _source_packs_cache
    key = (source or "").strip().lower()
    if not key or key in _source_packs_cache:
        return _source_packs_cache.get(key, {})
    path = _DATA_DIR / "aliases" / f"{key}.json"
    if not path.exists():
        _source_packs_cache[key] = {}
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {k.strip().lower(): v for k, v in raw.items()}
    _source_packs_cache[key] = out
    return out


def resolve_exercise(raw: str, source: str | None = None) -> tuple[str, str, str, float]:
    """
    Resolve raw exercise name to canonical (exercise_id, display, strategy, score).
    Precedence: source pack → global alias → registry display → registry alias → unmapped.
    """
    key = (raw or "").strip()
    key_lower = key.lower()
    display_orig = key

    # 1) Source pack (exact exporter string)
    if source:
        pack = _load_source_pack(source)
        if key_lower in pack:
            eid = pack[key_lower]
            _load_registry()
            for ent in _registry or []:
                if ent.get("exercise_id") == eid:
                    return (eid, ent.get("display", display_orig), "source_pack", 1.0)
            return (eid, display_orig, "source_pack", 1.0)

    # 2) Global aliases (legacy exact synonyms)
    if key_lower in EXERCISE_ALIASES:
        return (EXERCISE_ALIASES[key_lower], display_orig, "global_alias", 0.95)

    # 3) Registry by display
    _load_registry()
    if key_lower in _registry_by_display:
        ent = _registry_by_display[key_lower]
        return (ent["exercise_id"], ent.get("display", display_orig), "registry_display", 0.9)

    # 4) Registry by alias
    if key_lower in _registry_by_alias:
        ent = _registry_by_alias[key_lower]
        return (ent["exercise_id"], ent.get("display", display_orig), "registry_alias", 0.85)

    # 5) Unmapped
    slug = slug_exercise(raw)
    return (f"unmapped:{slug}", display_orig, "unmapped", 0.0)


# Match strategy scores (deterministic)
_SCORE_DISPLAY_EXACT = 1.0
_SCORE_ALIAS_EXACT = 0.95
_SCORE_STARTS_WITH = 0.90
_SCORE_CONTAINS = 0.85

def search_exercises(
    query: str,
    equipment: str | None = None,
    movement_pattern: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Search the local exercise registry. Returns { query, count, results }.
    Each result has exercise_id, display, aliases, equipment (list), movement_pattern,
    match { strategy, score, matched_text, normalized_query }, and is_exact_match.
    Deterministic sort: score desc, is_exact_match desc, display asc.
    """
    reg = _load_registry()
    if not reg:
        return {"query": (query or "").strip(), "count": 0, "results": []}
    raw_query = (query or "").strip()
    norm_query = normalize_search_query(raw_query)
    if not norm_query:
        return {"query": raw_query, "count": 0, "results": []}
    out: list[dict[str, Any]] = []
    for e in reg:
        display = (e.get("display") or "").strip()
        display_norm = normalize_search_query(display)
        aliases = e.get("aliases") or []
        alias_norms = [normalize_search_query(a or "") for a in aliases]
        # Apply filters first
        if equipment is not None and equipment.strip():
            eq_val = (e.get("equipment") or "").strip().lower()
            if isinstance(eq_val, str):
                eq_list = [eq_val] if eq_val else []
            else:
                eq_list = [str(x).strip().lower() for x in eq_val] if eq_val else []
            if equipment.strip().lower() not in eq_list:
                continue
        if movement_pattern is not None and movement_pattern.strip():
            if (e.get("movement_pattern") or "").strip().lower() != movement_pattern.strip().lower():
                continue
        # Determine best strategy and matched_text
        strategy: str | None = None
        score: float = 0.0
        matched_text: str = ""
        if norm_query == display_norm:
            strategy = "display_exact"
            score = _SCORE_DISPLAY_EXACT
            matched_text = display
        elif norm_query in alias_norms:
            idx = alias_norms.index(norm_query)
            strategy = "alias_exact"
            score = _SCORE_ALIAS_EXACT
            matched_text = (aliases[idx] or "").strip()
        elif display_norm.startswith(norm_query) or any(a and a.startswith(norm_query) for a in alias_norms):
            strategy = "starts_with"
            score = _SCORE_STARTS_WITH
            if display_norm.startswith(norm_query):
                matched_text = display
            else:
                for i, a in enumerate(alias_norms):
                    if a and a.startswith(norm_query):
                        matched_text = (aliases[i] or "").strip()
                        break
        elif norm_query in display_norm or any(norm_query in (a or "") for a in alias_norms):
            strategy = "contains"
            score = _SCORE_CONTAINS
            if norm_query in display_norm:
                matched_text = display
            else:
                for i, a in enumerate(alias_norms):
                    if a and norm_query in a:
                        matched_text = (aliases[i] or "").strip()
                        break
        if strategy is None:
            continue
        is_exact = strategy in ("display_exact", "alias_exact")
        eid = e.get("exercise_id", "")
        eq_raw = e.get("equipment")
        if eq_raw is None or eq_raw == "":
            equipment_list: list[str] = []
        elif isinstance(eq_raw, list):
            equipment_list = [str(x).strip() for x in eq_raw if x]
        else:
            equipment_list = [str(eq_raw).strip()] if str(eq_raw).strip() else []
        out.append({
            "exercise_id": eid,
            "display": display or None,
            "aliases": aliases if aliases else None,
            "equipment": equipment_list,
            "movement_pattern": (e.get("movement_pattern") or "").strip() or None,
            "match": {
                "strategy": strategy,
                "score": score,
                "matched_text": matched_text,
                "normalized_query": norm_query,
            },
            "is_exact_match": is_exact,
        })
    # Deterministic sort: score desc, is_exact_match desc, display asc
    out.sort(key=lambda x: (-x["match"]["score"], -x["is_exact_match"], (x["display"] or "")))
    results = out[: max(0, limit)]
    return {"query": raw_query, "count": len(results), "results": results}


def suggest_exercises_for_unmapped(raw: str, max_suggestions: int = 3) -> list[str]:
    """
    Return up to max_suggestions exercise_ids that are a close match (substring of display/alias
    or display/alias substring of raw). No fuzzy matching; exact substring only. Deterministic.
    """
    reg = _load_registry()
    if not reg:
        return []
    raw_lower = (raw or "").strip().lower()
    if not raw_lower:
        return []
    seen: set[str] = set()
    for e in reg:
        eid = e.get("exercise_id")
        if not eid or eid in seen:
            continue
        display_lower = (e.get("display") or "").lower()
        if raw_lower in display_lower or display_lower in raw_lower:
            seen.add(eid)
            continue
        for a in e.get("aliases") or []:
            a_lower = (a or "").lower()
            if raw_lower in a_lower or a_lower in raw_lower:
                seen.add(eid)
                break
    return sorted(seen)[:max_suggestions]


# Exact synonym -> exercise_id only (used when no registry; fallback in resolve_exercise).
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
    "tricep pushdown": "triceps_pushdown",
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


def resolve_exercise_id(exercise_raw: str, source: str | None = None) -> tuple[str, str]:
    """Return (exercise_id, exercise_display). Uses resolve_exercise then drops strategy/score."""
    eid, display, _, _ = resolve_exercise(exercise_raw, source)
    return (eid, display)


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
    source: str | None = None,
) -> ExerciseRecord:
    """Build ExerciseRecord from raw name and list of set dicts. Includes mapping (strategy, score)."""
    exercise_id, exercise_display, strategy, score = resolve_exercise(raw_name, source)
    sets = [
        normalize_set(s, i + 1, default_unit)
        for i, s in enumerate(raw_sets)
    ]
    mapping = ExerciseMapping(strategy=strategy, score=score)
    return ExerciseRecord(
        exercise_raw=raw_name,
        exercise_id=exercise_id,
        exercise_display=exercise_display,
        mapping=mapping,
        sets=sets,
    )


def normalize_session(
    session_id: str,
    date: str | None,
    raw_exercises: list[tuple[str, list[dict]]],
    default_unit: str,
    title: str | None = None,
    notes: str | None = None,
    source: str | None = None,
) -> SessionRecord:
    """Build SessionRecord from raw (exercise_name, sets) list. source = app name for alias pack (e.g. strong, hevy)."""
    exercises = [
        normalize_exercise(name, sets, default_unit, source=source)
        for name, sets in raw_exercises
    ]
    return SessionRecord(
        session_id=session_id,
        date=date,
        title=title or None,
        notes=notes or None,
        exercises=exercises,
    )

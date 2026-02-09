"""Ingest workflow: parse (CSV/JSON/text), normalize, validate. Stateless — no persistence."""

from __future__ import annotations

import csv
import json
import re
import uuid
from typing import Any

from .models import (
    CanonicalLog,
    IngestLogInput,
    IngestLogOutput,
    IngestOptions,
    IngestSignature,
    IngestSummary,
    IssueRecord,
    SessionRecord,
)
from .normalize import (
    PARSER_VERSION,
    canonical_sha256,
    normalize_date,
    normalize_session,
    suggest_exercises_for_unmapped,
)


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _default_options(opts: IngestOptions | None) -> IngestOptions:
    return opts or IngestOptions()


def parse_csv(content: str) -> list[tuple[str, list[dict]]]:
    """
    Parse CSV with expected columns: exercise (or Exercise), weight, reps, optional unit, rpe, set_type, date.
    Returns list of (exercise_name, [set_dict]) per logical exercise block (same name = same exercise).
    """
    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    if not lines:
        return []
    reader = csv.DictReader(lines)
    if not reader.fieldnames:
        return []
    # Normalize header names (case-insensitive)
    fieldnames = [f.strip().lower() for f in reader.fieldnames]
    col_map = {f: reader.fieldnames[i] for i, f in enumerate(fieldnames)}
    # Map common names
    ex_col = col_map.get("exercise") or col_map.get("exercise name") or next(
        (col_map[k] for k in col_map if "exercise" in k), None
    )
    weight_col = col_map.get("weight") or next((col_map[k] for k in col_map if "weight" in k), None)
    reps_col = col_map.get("reps") or col_map.get("rep") or next((col_map[k] for k in col_map if "rep" in k), None)
    unit_col = col_map.get("unit") or col_map.get("units") or None
    rpe_col = col_map.get("rpe") or None
    set_type_col = col_map.get("set_type") or col_map.get("set type") or None
    date_col = (
        col_map.get("date")
        or col_map.get("workout date")
        or next((col_map[k] for k in col_map if "date" in k), None)
    )

    if not ex_col or not weight_col or not reps_col:
        return []

    rows = list(reader)
    # Parse weight: bodyweight -> load_type bodyweight (weight null); "+25" -> bodyweight_plus; numeric -> weighted
    def _parse_weight(val: str) -> tuple[float | None, str, float | None]:
        """Return (weight_or_none, load_type, added_weight_or_none)."""
        if val is None or val == "":
            return (0.0, "weighted", None)
        v = (val or "").strip()
        v_lower = v.lower()
        if v_lower in ("bodyweight", "bw", "-", "—"):
            return (None, "bodyweight", None)
        if v.startswith("+") or v_lower.startswith("+"):
            try:
                added = float(v.replace("+", "").strip() or 0)
                return (None, "bodyweight_plus", added)
            except (TypeError, ValueError):
                return (None, "weighted", None)  # fallback
        try:
            return (float(v or 0), "weighted", None)
        except (TypeError, ValueError):
            return (None, "weighted", None)

    # Build rows with (date, exercise, set_dict)
    row_tuples: list[tuple[str | None, str, dict]] = []
    for row in rows:
        raw = {col_map[k]: row.get(col_map[k], "") for k in col_map}
        ex = (raw.get(ex_col) or "").strip()
        if not ex:
            continue
        try:
            reps = int(float(raw.get(reps_col, 0)))
        except (TypeError, ValueError):
            continue
        w, load_type, added = _parse_weight(raw.get(weight_col, ""))
        if w is None and load_type == "weighted":
            continue  # unparseable weight
        set_dict: dict[str, Any] = {"reps": reps, "load_type": load_type}
        if load_type == "weighted":
            set_dict["weight"] = w if w is not None else 0.0
        else:
            set_dict["weight"] = None
        if load_type == "bodyweight_plus" and added is not None:
            set_dict["added_weight"] = round(added, 2)
            if unit_col and raw.get(unit_col):
                u = raw[unit_col].strip().lower()
                if u and u not in ("bodyweight", "bw", "-"):
                    set_dict["added_weight_unit"] = "lb" if u in ("lb", "lbs", "pound", "pounds") else "kg"
                else:
                    set_dict["added_weight_unit"] = "lb"
            else:
                set_dict["added_weight_unit"] = "lb"
        if unit_col and raw.get(unit_col) and load_type == "weighted":
            u = raw[unit_col].strip().lower()
            if u and u not in ("bodyweight", "bw", "-"):
                set_dict["unit"] = u
        if rpe_col and raw.get(rpe_col):
            try:
                set_dict["rpe"] = float(raw[rpe_col])
            except (TypeError, ValueError):
                pass
        if set_type_col and raw.get(set_type_col):
            set_dict["set_type"] = raw[set_type_col].strip().lower()
        date_val = raw.get(date_col, "").strip() if date_col else None
        row_tuples.append((date_val or None, ex, set_dict))

    if not row_tuples:
        return []

    # If we have a date column, group by date then by exercise
    if date_col:
        by_date: dict[str, list[tuple[str, list[dict]]]] = {}
        for date_val, ex, set_dict in row_tuples:
            key = date_val or ""
            if key not in by_date:
                by_date[key] = []
            # Group by exercise within this date
            if by_date[key] and by_date[key][-1][0] == ex:
                by_date[key][-1][1].append(set_dict)
            else:
                by_date[key].append((ex, [set_dict]))
        # Return list of (date_str, [(ex, sets), ...]) for each date
        return [(d, ex_list) for d, ex_list in by_date.items() if d]

    # No date column: single session, group by exercise
    result: list[tuple[str, list[dict]]] = []
    current_exercise: str | None = None
    current_sets: list[dict] = []
    for _date, ex, set_dict in row_tuples:
        if ex != current_exercise:
            if current_exercise and current_sets:
                result.append((current_exercise, current_sets))
            current_exercise = ex
            current_sets = []
        current_sets.append(set_dict)
    if current_exercise and current_sets:
        result.append((current_exercise, current_sets))
    return [(None, result)]  # one session, no date


def parse_json(content: str) -> list[tuple[str, list[dict]]]:
    """
    Parse JSON: expect array of objects with exercise (+ weight, reps) or nested sessions.
    Accept: [ { "exercise": "X", "weight": 135, "reps": 5 }, ... ] or
            { "sessions": [ { "exercises": [ { "name": "...", "sets": [...] } ] } ] }
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        # Flat list of sets -> one session, no date
        by_exercise: dict[str, list[dict]] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            ex = (row.get("exercise") or row.get("Exercise") or row.get("name") or "").strip()
            if not ex:
                continue
            w = row.get("weight", row.get("Weight"))
            raw_lt = (row.get("load_type") or row.get("load type") or "").strip().lower()
            if not raw_lt and isinstance(row.get("type"), str) and row.get("type").strip().lower() in ("bodyweight", "bodyweight_plus", "bw"):
                raw_lt = row.get("type").strip().lower()
            if raw_lt in ("bodyweight", "bw"):
                lt = "bodyweight"
            elif raw_lt in ("bodyweight_plus", "bodyweight +", "weighted bw") or row.get("added_weight") is not None or row.get("added weight") is not None:
                lt = "bodyweight_plus"
            elif w is None:
                lt = "bodyweight"
            elif isinstance(w, (int, float)) and w == 0 and raw_lt != "weighted":
                lt = "bodyweight"
            else:
                lt = "weighted"
            if lt == "bodyweight":
                set_dict = {"weight": None, "load_type": "bodyweight", "reps": int(row.get("reps", row.get("Reps", 0)))}
            elif lt == "bodyweight_plus":
                added = row.get("added_weight", row.get("added weight")) or 0
                set_dict = {
                    "weight": None,
                    "load_type": "bodyweight_plus",
                    "added_weight": round(float(added), 2),
                    "added_weight_unit": (row.get("added_weight_unit") or row.get("added weight unit") or row.get("unit") or row.get("Unit") or "lb").strip()[:2].lower() or "lb",
                    "reps": int(row.get("reps", row.get("Reps", 0))),
                }
                if set_dict["added_weight_unit"] not in ("lb", "kg"):
                    set_dict["added_weight_unit"] = "lb"
            else:
                set_dict = {
                    "weight": float(w if w is not None else 0),
                    "load_type": "weighted",
                    "reps": int(row.get("reps", row.get("Reps", 0))),
                }
                if row.get("unit") or row.get("Unit"):
                    set_dict["unit"] = (row.get("unit") or row.get("Unit", "")).strip()
            if row.get("rpe") is not None or row.get("RPE") is not None:
                set_dict["rpe"] = float(row.get("rpe", row.get("RPE", 0)))
            if row.get("set_type") or row.get("set_type"):
                set_dict["set_type"] = (row.get("set_type") or row.get("set_type", "")).strip().lower()
            by_exercise.setdefault(ex, []).append(set_dict)
        return [(None, list(by_exercise.items()))]
    if isinstance(data, dict) and "sessions" in data:
        # Return list of (date_str|None, [(ex, sets), ...]) per session
        sessions_out: list[tuple[str | None, list[tuple[str, list[dict]]]]] = []
        for sess in data["sessions"]:
            if not isinstance(sess, dict):
                continue
            date_str = sess.get("date")
            if date_str is not None and not isinstance(date_str, str):
                date_str = None
            if date_str:
                date_str = date_str.strip() or None
            ex_list: list[tuple[str, list[dict]]] = []
            for ex_block in sess.get("exercises", []):
                if not isinstance(ex_block, dict):
                    continue
                name = (
                    ex_block.get("exercise")
                    or ex_block.get("exercise_name")
                    or ex_block.get("name")
                    or ex_block.get("exercise_raw")
                    or ""
                ).strip()
                if not name:
                    continue
                sets_raw = ex_block.get("sets", [])
                sets = []
                for s in sets_raw:
                    if isinstance(s, dict):
                        s = dict(s)
                        if "type" in s and "set_type" not in s:
                            t = s.pop("type", None)
                            if t and str(t).strip().lower() in ("warmup", "working", "top", "backoff"):
                                s["set_type"] = str(t).strip().lower()
                        w = s.get("weight")
                        lt = (s.get("load_type") or s.get("load type") or "").strip().lower()
                        if lt in ("bodyweight", "bw"):
                            s["weight"] = None
                            s["load_type"] = "bodyweight"
                        elif lt == "bodyweight_plus" or s.get("added_weight") is not None:
                            s["weight"] = None
                            s["load_type"] = "bodyweight_plus"
                            s["added_weight"] = round(float(s.get("added_weight", s.get("added weight")) or 0), 2)
                            s["added_weight_unit"] = (s.get("added_weight_unit") or s.get("added weight unit") or s.get("unit") or "lb")[:2].lower() if (s.get("added_weight_unit") or s.get("added weight unit") or s.get("unit")) else "lb"
                            if s["added_weight_unit"] not in ("lb", "kg"):
                                s["added_weight_unit"] = "lb"
                        elif w is None:
                            s["weight"] = None
                            s["load_type"] = "bodyweight"
                        else:
                            s["weight"] = float(w)
                            s["load_type"] = s.get("load_type") or "weighted"
                        sets.append(s)
                    elif isinstance(s, (int, float)):
                        sets.append({"reps": int(s), "weight": None, "load_type": "bodyweight"})
                if sets:
                    ex_list.append((name, sets))
            if ex_list:
                sessions_out.append((date_str, ex_list))
        if sessions_out:
            return sessions_out
    return []


# Words that are not valid exercise names (sentence starters, filler, etc.)
_TEXT_STOP_WORDS = frozenset({
    "maybe", "did", "not", "some", "the", "at", "then", "felt", "strong", "tomorrow",
    "i", "we", "a", "an", "and", "or", "is", "it", "to", "go", "went", "up", "no", "yes",
    "today", "sets", "set", "then", "went", "could", "not", "sure", "unknown",
})


def _is_plausible_exercise_name(name: str) -> bool:
    """True if name looks like an exercise, not a sentence fragment or stop word."""
    n = name.strip().lower()
    if not n or len(n) < 2:
        return False
    if n in _TEXT_STOP_WORDS:
        return False
    # Single word that looks like a modifier, not an exercise
    if n in ("some", "maybe", "did", "the", "at"):
        return False
    return True


def _infer_exercise_from_context(text_before: str) -> str | None:
    """Return exercise name from text_before (whole-word match); prefer the most recently mentioned."""
    from .normalize import EXERCISE_ALIASES
    t = text_before.lower()
    found: list[tuple[int, str]] = []  # (position, display_name)
    for alias, eid in EXERCISE_ALIASES.items():
        # Whole-word match so "row" doesn't match inside "tomorrow"
        pat = re.compile(r"(?<![a-z])" + re.escape(alias) + r"s?(?![a-z])")
        matches = list(pat.finditer(t))
        if matches:
            pos = matches[-1].start()
            display = eid.replace("_", " ").title()
            found.append((pos, display))
    if not found:
        return None
    # Prefer the one mentioned most recently (largest position)
    found.sort(key=lambda x: x[0])
    return found[-1][1]


def parse_text_fallback(content: str) -> tuple[list[tuple[str, list[dict]]], list[IssueRecord]]:
    """
    Parse plain text: only accept valid exercise + weight×reps, or "NxM at WEIGHT" with context.
    Returns (parsed_exercises, issues). Parsed list may be empty if nothing valid; then use needs_clarification.
    """
    issues: list[IssueRecord] = []
    result: list[tuple[str, list[dict]]] = []
    lines = content.splitlines()
    default_unit = "lb"

    # 1) "Exercise Name 135x5" with valid exercise name only
    pattern_named = re.compile(
        r"^([^0-9\n]+?)\s+(\d+(?:\.\d+)?)\s*(?:lb|kg)?\s*[x×]\s*(\d+)",
        re.IGNORECASE,
    )
    # 2) "3x5 at 225" or "3 x 5 at 225" (reps x sets? we use first number as reps for the set)
    pattern_at = re.compile(r"(\d+)\s*[x×]\s*(\d+)\s+at\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
    # 3) "sets at 135" - weight only, no reps (we will not add set; emit issue)
    pattern_weight_only = re.compile(r"(?:sets?\s+)?at\s+(\d+(?:\.\d+)?)", re.IGNORECASE)

    current_ex: str | None = None
    current_sets: list[dict] = []
    text_so_far = ""
    weights_used: set[float] = set()  # weights already captured by a full set

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        text_so_far += " " + line_stripped

        # "NxM at WEIGHT" = N sets of M reps at WEIGHT (e.g. "3x5 at 225" -> 3 sets of 225x5)
        for m in pattern_at.finditer(line_stripped):
            num_sets = int(m.group(1))
            reps = int(m.group(2))
            weight = float(m.group(3))
            weights_used.add(weight)
            ex = _infer_exercise_from_context(text_so_far[: max(0, text_so_far.find(m.group(0)))])
            if ex:
                if current_ex != ex and current_ex and current_sets:
                    result.append((current_ex, current_sets))
                    current_sets = []
                current_ex = ex
                for _ in range(max(1, num_sets)):
                    current_sets.append({"weight": weight, "reps": reps, "unit": default_unit})
            else:
                issues.append(IssueRecord(
                    severity="warning",
                    type="ambiguous_exercise",
                    location="text",
                    message=f"Found '{m.group(0).strip()}' but could not infer exercise from context. Add exercise name (e.g. 'Squat 225x5').",
                ))

        # Weight-only (no reps): record issue only if this weight wasn't already used in a set
        for m in pattern_weight_only.finditer(line_stripped):
            w = float(m.group(1))
            if w not in weights_used and not any(s.get("weight") == w and s.get("reps") for _, sets in result for s in sets):
                weights_used.add(w)  # avoid duplicate issue for same weight
                issues.append(IssueRecord(
                    severity="warning",
                    type="incomplete_set",
                    location="text",
                    message=f"Found weight {w} but reps could not be determined; set omitted. Add format like '135x5' or '3x5 at 135'.",
                ))

        # "Exercise Name 135x5"
        m = pattern_named.search(line_stripped)
        line_contributed = False
        if m:
            ex_name = m.group(1).strip()
            weight = float(m.group(2))
            reps = int(m.group(3))
            if not _is_plausible_exercise_name(ex_name):
                issues.append(IssueRecord(
                    severity="warning",
                    type="invalid_exercise_name",
                    location="text",
                    message=f"'{ex_name}' does not look like an exercise name; line ignored. Use a clear exercise name (e.g. Bench Press 135x5).",
                    raw_excerpt=line_stripped[:80],
                ))
                line_contributed = True
                continue
            if current_ex != ex_name and current_ex and current_sets:
                result.append((current_ex, current_sets))
                current_sets = []
            current_ex = ex_name
            current_sets.append({"weight": weight, "reps": reps, "unit": default_unit})
            weights_used.add(weight)
            line_contributed = True

        # Dropped line: single stop word (e.g. "Maybe") or bare number (e.g. "135") with no set added
        if not line_contributed and line_stripped:
            tokens = line_stripped.split()
            if len(tokens) == 1 and tokens[0].lower() in _TEXT_STOP_WORDS:
                issues.append(IssueRecord(
                    severity="warning",
                    type="invalid_exercise_name",
                    location="text",
                    message=f"Line dropped: '{line_stripped}' is not a valid exercise name.",
                    raw_excerpt=line_stripped[:80],
                ))
            elif len(tokens) == 1 and tokens[0].replace(".", "").isdigit():
                w = float(tokens[0])
                if w not in weights_used:
                    issues.append(IssueRecord(
                        severity="warning",
                        type="incomplete_set",
                        location="text",
                        message=f"Found weight {w} but reps could not be determined; set omitted.",
                        raw_excerpt=line_stripped[:80],
                    ))

    if current_ex and current_sets:
        result.append((current_ex, current_sets))

    return result, issues


# Confidence penalties (deterministic, from ingest_policy v1.1)
CONFIDENCE_PENALTY = {
    "missing_date": 0.15,
    "missing_date_autofilled": 0.15,
    "invalid_exercise_name": 0.20,
    "incomplete_set": 0.10,
    "ambiguous_exercise": 0.15,
    "ambiguous_set_format": 0.15,
    "unmapped_exercise": 0.10,
}
CONFIDENCE_FLOOR = 0.25
CONFIDENCE_THRESHOLD = 0.70  # status ok requires confidence >= this


def _compute_confidence(issues: list[IssueRecord]) -> float:
    """Deterministic confidence from issues only: start 1.0, apply penalties, floor 0.25, cap 1.0."""
    c = 1.0
    for i in issues:
        p = CONFIDENCE_PENALTY.get(i.type)
        if p is not None:
            c -= p
    return max(CONFIDENCE_FLOOR, min(1.0, round(c, 2)))


def build_canonical_log(
    raw_sessions: list[tuple[str | None, list[tuple[str, list[dict]]]]],
    default_unit: str,
    session_date_hint: str | None,
    source: str | None = None,
) -> tuple[CanonicalLog, list[IssueRecord]]:
    """
    raw_sessions: list of (date_YYYY_MM_DD or None, [(exercise_name, sets), ...])
    source: app name for alias pack (e.g. strong, hevy); optional.
    """
    issues: list[IssueRecord] = []
    sessions: list[SessionRecord] = []
    for i, (date_str, exercises) in enumerate(raw_sessions):
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        norm_date = normalize_date(date_str, session_date_hint) if date_str else session_date_hint
        if not norm_date:
            issues.append(IssueRecord(
                severity="warning",
                type="missing_date",
                location=f"session_{i}",
                message="Session date not provided; session stored with date null. Provide session_date_hint to set a date.",
            ))
        elif not date_str and session_date_hint:
            issues.append(IssueRecord(
                severity="warning",
                type="missing_date_autofilled",
                location=f"session_{i}",
                message="Session date was missing; used session_date_hint.",
            ))
        sess = normalize_session(
            session_id,
            norm_date,
            exercises,
            default_unit,
            source=source,
        )
        sessions.append(sess)
        for j, ex in enumerate(sess.exercises):
            if ex.exercise_id.startswith("unmapped:"):
                suggested = suggest_exercises_for_unmapped(ex.exercise_raw, max_suggestions=3)
                issues.append(IssueRecord(
                    severity="warning",
                    type="unmapped_exercise",
                    location=f"sessions[{i}].exercises[{j}]",
                    message=f"Exercise not in mapping dictionary; stored as {ex.exercise_id}. Add an exact synonym if this is a known lift.",
                    raw_excerpt=ex.exercise_raw,
                    suggested_exercise_ids=suggested if suggested else None,
                ))
    log = CanonicalLog(sessions=sessions)
    return log, issues


def ingest_log_impl(
    payload: IngestLogInput,
    llm_parser: Any = None,
) -> IngestLogOutput:
    """
    Stateless ingest: parse, normalize, validate. Does not persist.
    llm_parser: optional callable(content: str, date_hint: str | None) -> raw_sessions; used only when content_type=text and allow_llm=true.
    """
    options = _default_options(payload.options)
    user = payload.user
    log_input = payload.log_input
    default_unit = user.default_unit
    # Optional client-provided correlation id; no server-side user identity
    user_id = user.user_id or _generate_id("req")

    llm_available = llm_parser is not None
    llm_used = False

    issues: list[IssueRecord] = []
    raw_sessions: list[tuple[str, list[tuple[str, list[dict]]]]] = []

    if log_input.content_type == "csv":
        parsed = parse_csv(log_input.content)
        if not parsed:
            issues.append(IssueRecord(
                severity="blocking",
                type="parse_error",
                location="csv",
                message="CSV could not be parsed or required columns (exercise, weight, reps) missing.",
                raw_excerpt=log_input.content[:200].strip() if log_input.content else None,
            ))
        else:
            # parse_csv returns list of (date_str|None, [(ex, sets), ...])
            raw_sessions = [(d or options.session_date_hint, ex_list) for d, ex_list in parsed]
    elif log_input.content_type == "json":
        parsed = parse_json(log_input.content)
        if not parsed:
            issues.append(IssueRecord(
                severity="blocking",
                type="parse_error",
                location="json",
                message="JSON could not be parsed or has no recognized structure.",
                raw_excerpt=log_input.content[:200].strip() if log_input.content else None,
            ))
        else:
            # parse_json returns list of (date_str|None, [(ex, sets), ...])
            raw_sessions = [(d or options.session_date_hint, ex_list) for d, ex_list in parsed]
    elif log_input.content_type == "text":
        if options.allow_llm and llm_parser:
            try:
                raw_sessions = llm_parser(log_input.content, options.session_date_hint)
                llm_used = True
            except Exception as e:
                issues.append(IssueRecord(
                    severity="blocking",
                    type="llm_parse_error",
                    location="text",
                    message=str(e),
                ))
        elif options.allow_llm and not llm_parser:
            issues.append(IssueRecord(
                severity="warning",
                type="llm_unavailable",
                location="text",
                message="allow_llm=true but no LLM parser is configured; falling back to deterministic parser.",
            ))
            parsed, text_issues = parse_text_fallback(log_input.content)
            issues.extend(text_issues)
            if not parsed:
                issues.append(IssueRecord(
                    severity="blocking",
                    type="parse_error",
                    location="text",
                    message="No valid sets could be extracted from text. Use format 'Exercise 135x5' or '3x5 at 225' with exercise name in context. Use CSV/JSON or set allow_llm=true if LLM is configured.",
                    raw_excerpt=log_input.content[:200].strip() if log_input.content else None,
                ))
            else:
                raw_sessions = [(options.session_date_hint, parsed)]
        else:
            parsed, text_issues = parse_text_fallback(log_input.content)
            issues.extend(text_issues)
            if not parsed:
                issues.append(IssueRecord(
                    severity="blocking",
                    type="parse_error",
                    location="text",
                    message="No valid sets could be extracted from text. Use format 'Exercise 135x5' or '3x5 at 225' with exercise name in context. Use CSV/JSON or set allow_llm=true if LLM is configured.",
                    raw_excerpt=log_input.content[:200].strip() if log_input.content else None,
                ))
            else:
                raw_sessions = [(options.session_date_hint, parsed)]
    else:
        issues.append(IssueRecord(
            severity="blocking",
            type="unsupported_type",
            location="log_input",
            message=f"Unsupported content_type: {log_input.content_type}",
        ))

    if not raw_sessions and issues:
        status = "needs_clarification" if any(i.severity == "blocking" for i in issues) else "error"
        return IngestLogOutput(
            status=status,
            user_id=user_id,
            log_id=None,
            canonical_log=CanonicalLog(sessions=[]),
            issues=issues,
            summary=IngestSummary(confidence=0.0),
            signature=IngestSignature(canonical_sha256="", parser_version=PARSER_VERSION),
            meta={"llm_available": llm_available, "llm_used": llm_used},
        )

    source_app = (log_input.source.app if log_input.source else None) or None
    canonical_log, build_issues = build_canonical_log(
        raw_sessions,
        default_unit,
        options.session_date_hint,
        source=source_app,
    )
    issues.extend(build_issues)

    sessions_detected = len(canonical_log.sessions)
    exercises_detected = sum(len(s.exercises) for s in canonical_log.sessions)
    sets_detected = sum(
        len(ex.sets)
        for s in canonical_log.sessions
        for ex in s.exercises
    )
    unmapped = sum(
        1
        for s in canonical_log.sessions
        for ex in s.exercises
        if ex.exercise_id.startswith("unmapped:")
    )
    confidence = _compute_confidence(issues)

    # Strictness: missing_date is blocking when strict
    if options.strictness == "strict" and any(i.type == "missing_date" for i in issues):
        issues.append(IssueRecord(
            severity="blocking",
            type="missing_date",
            location="session",
            message="Session date is required when strictness=strict. Provide session_date_hint or structured input with date.",
        ))

    # Status rules (ingest_policy v1.1)
    has_blocking = any(i.severity == "blocking" for i in issues)
    if has_blocking or exercises_detected == 0 or sets_detected == 0 or confidence < CONFIDENCE_THRESHOLD:
        status = "needs_clarification"
    else:
        status = "ok"

    sha = canonical_sha256(canonical_log)
    # Return a request-scoped id when we have sessions (client may use it as storage key)
    log_id = _generate_id("log") if (status == "ok" and canonical_log.sessions) else None

    return IngestLogOutput(
        status=status,
        user_id=user_id,
        log_id=log_id,
        canonical_log=canonical_log,
        issues=issues,
        summary=IngestSummary(
            sessions_detected=sessions_detected,
            exercises_detected=exercises_detected,
            sets_detected=sets_detected,
            unmapped_exercises=unmapped,
            confidence=confidence,
        ),
        signature=IngestSignature(canonical_sha256=sha, parser_version=PARSER_VERSION),
        meta={"llm_available": llm_available, "llm_used": llm_used},
    )

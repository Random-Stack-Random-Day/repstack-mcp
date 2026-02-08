"""Deterministic metrics over canonical logs: volume, tonnage, e1rm, PRs, flags."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .models import (
    ComputeMetricsInput,
    ComputeMetricsOutput,
    DateRange,
    ExerciseSummary,
    MetricsSignature,
    PRRecord,
    TopSetRecord,
    WeeklyMetrics,
)
from .storage import Storage

METRICS_VERSION = "1.0.0"


def e1rm_epley(weight: float, reps: int) -> float:
    if reps <= 0:
        return 0.0
    if reps == 1:
        return weight
    return weight * (1 + reps / 30.0)


def e1rm_brzycki(weight: float, reps: int) -> float:
    if reps <= 0:
        return 0.0
    if reps == 1:
        return weight
    return weight * (36.0 / (37.0 - reps))


def e1rm(weight: float, reps: int, formula: str) -> float:
    if formula == "brzycki":
        return round(e1rm_brzycki(weight, reps), 2)
    return round(e1rm_epley(weight, reps), 2)


def _is_hard_set(set_type: str | None) -> bool:
    """Exclude warmup when set_type is present."""
    if set_type is None:
        return True  # count all when not specified
    return set_type.lower() in ("working", "top", "backoff")


def _week_start(date_str: str) -> str:
    """Return Monday of the week for date_str (YYYY-MM-DD)."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    # Monday = 0
    weekday = d.weekday()
    start = d - timedelta(days=weekday)
    return start.strftime("%Y-%m-%d")


def _in_range(date_str: str, start: str, end: str) -> bool:
    return start <= date_str <= end


def compute_metrics_impl(
    payload: ComputeMetricsInput,
    storage: Storage,
) -> ComputeMetricsOutput:
    """Compute weekly and exercise-level metrics from stored logs."""
    user_id = payload.user_id
    range_ = payload.range
    from .models import ComputeMetricsOptions
    opts = payload.options or ComputeMetricsOptions()
    formula = opts.e1rm_formula
    include_prs = opts.include_prs

    logs = storage.get_logs_for_user(user_id, range_.start, range_.end)
    if not logs:
        return ComputeMetricsOutput(
            status="ok",
            user_id=user_id,
            range=range_,
            weekly=[],
            exercise_summaries=[],
            signature=MetricsSignature(metrics_version=METRICS_VERSION),
        )

    # Flatten: (weight, unit, reps, set_type, e1rm, load_type, added_weight, added_unit)
    # weight/unit None for bodyweight; for bodyweight_plus use added_weight/added_unit for tonnage only
    sets_by_date_ex: dict[str, list[tuple[float | None, str | None, int, str | None, float | None, str, float | None, str | None]]] = defaultdict(list)
    for log_row in logs:
        canonical = log_row.get("canonical_json", {})
        for sess in canonical.get("sessions", []):
            date_str = sess.get("date")
            if not date_str or not _in_range(date_str, range_.start, range_.end):
                continue
            for ex in sess.get("exercises", []):
                ex_id = ex.get("exercise_id", "")
                for s in ex.get("sets", []):
                    load_type = (s.get("load_type") or "weighted").strip().lower()
                    if load_type not in ("weighted", "bodyweight", "bodyweight_plus", "assisted"):
                        load_type = "weighted"
                    reps = int(s.get("reps", 0))
                    set_type = s.get("set_type")
                    weight: float | None = s.get("weight")
                    if weight is not None:
                        weight = float(weight)
                    unit = s.get("unit") or "lb"
                    added_load_obj = s.get("added_load")
                    if isinstance(added_load_obj, dict):
                        added_weight = float(added_load_obj.get("value", 0))
                        added_unit = added_load_obj.get("unit") or "lb"
                    else:
                        added_weight = s.get("added_weight")
                        if added_weight is not None:
                            added_weight = float(added_weight)
                        added_unit = s.get("added_weight_unit") or "lb"
                    if load_type == "bodyweight":
                        e1 = None
                    elif load_type == "bodyweight_plus" and added_weight is not None:
                        e1 = e1rm(added_weight, reps, formula)
                    else:
                        e1 = e1rm(weight or 0, reps, formula) if weight is not None else None
                    sets_by_date_ex[f"{date_str}|{ex_id}"].append((weight, unit, reps, set_type, e1, load_type, added_weight, added_unit))

    # Per week
    week_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "sessions": set(),
        "hard_sets": 0,
        "tonnage_lb": 0.0,
        "tonnage_kg": 0.0,
        "tonnage_excluded_sets": 0,
        "tonnage_unknown_sets": 0,
        "top_sets": [],
        "prs": [],
    })
    # Per exercise
    ex_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "sessions": set(),
        "best_e1rm": None,
        "total_hard_sets": 0,
        "rep_ranges": defaultdict(int),
        "best_sets": [],  # (date, weight, unit, reps, e1rm)
    })

    # Best e1rm per exercise per day for PR detection
    best_per_ex_day: dict[str, dict[str, float]] = defaultdict(dict)  # ex_id -> date -> e1rm
    # Rep PR: (ex_id, weight, unit) -> best reps
    rep_prs: dict[tuple[str, float, str], int] = defaultdict(int)
    # e1rm PR per exercise
    e1rm_pr_per_ex: dict[str, float] = {}

    for key, set_list in sets_by_date_ex.items():
        date_str, ex_id = key.split("|", 1)
        week = _week_start(date_str)
        week_data[week]["sessions"].add(date_str)
        for (weight, unit, reps, set_type, e1, load_type, added_weight, added_unit) in set_list:
            is_hard = _is_hard_set(set_type)
            if is_hard:
                week_data[week]["hard_sets"] += 1
                ex_data[ex_id]["total_hard_sets"] += 1
            # Tonnage: exclude bodyweight; include weighted and bodyweight_plus (added_weight only)
            if load_type == "bodyweight":
                week_data[week]["tonnage_excluded_sets"] += 1
            elif load_type == "bodyweight_plus" and added_weight is not None:
                if (added_unit or "lb") == "lb":
                    week_data[week]["tonnage_lb"] += added_weight * reps
                else:
                    week_data[week]["tonnage_kg"] += added_weight * reps
            elif load_type == "weighted":
                if weight is not None:
                    if (unit or "lb") == "lb":
                        week_data[week]["tonnage_lb"] += weight * reps
                    else:
                        week_data[week]["tonnage_kg"] += weight * reps
                else:
                    week_data[week]["tonnage_unknown_sets"] += 1
            else:
                week_data[week]["tonnage_unknown_sets"] += 1
            ex_data[ex_id]["sessions"].add(date_str)
            if e1 and (ex_data[ex_id]["best_e1rm"] is None or e1 > ex_data[ex_id]["best_e1rm"]):
                ex_data[ex_id]["best_e1rm"] = e1
            # Bucket rep range
            if reps <= 5:
                ex_data[ex_id]["rep_ranges"]["1-5"] += 1
            elif reps <= 8:
                ex_data[ex_id]["rep_ranges"]["6-8"] += 1
            elif reps <= 12:
                ex_data[ex_id]["rep_ranges"]["9-12"] += 1
            else:
                ex_data[ex_id]["rep_ranges"]["12+"] += 1
            best_per_ex_day[ex_id][date_str] = max(
                best_per_ex_day[ex_id].get(date_str, 0), e1 or 0
            )
            # best_sets / top_sets / PRs: only weighted (or bodyweight_plus with added_weight) so we have a display weight
            if weight is not None:
                ex_data[ex_id]["best_sets"].append((date_str, weight, unit or "lb", reps, e1))
                k = (ex_id, weight, unit or "lb")
                if reps > rep_prs[k]:
                    rep_prs[k] = reps
            elif load_type == "bodyweight_plus" and added_weight is not None:
                ex_data[ex_id]["best_sets"].append((date_str, added_weight, added_unit or "lb", reps, e1))
                k = (ex_id, added_weight, added_unit or "lb")
                if reps > rep_prs[k]:
                    rep_prs[k] = reps
            if e1 and e1 > e1rm_pr_per_ex.get(ex_id, 0):
                e1rm_pr_per_ex[ex_id] = e1

    # Build weekly list with flags (volume spike >25% WoW)
    sorted_weeks = sorted(week_data.keys())
    prev_hard_sets = None
    prev_tonnage_lb = None
    prev_tonnage_kg = None
    weekly_out: list[WeeklyMetrics] = []
    for week in sorted_weeks:
        w = week_data[week]
        flags = []
        if prev_hard_sets is not None and prev_hard_sets > 0 and w["hard_sets"] > prev_hard_sets * 1.25:
            flags.append("volume_spike")
        if prev_tonnage_lb is not None and prev_tonnage_lb > 0 and w["tonnage_lb"] and w["tonnage_lb"] > prev_tonnage_lb * 1.25:
            flags.append("volume_spike")
        if prev_tonnage_kg is not None and prev_tonnage_kg > 0 and w["tonnage_kg"] and w["tonnage_kg"] > prev_tonnage_kg * 1.25:
            flags.append("volume_spike")
        prev_hard_sets = w["hard_sets"]
        prev_tonnage_lb = w["tonnage_lb"] or prev_tonnage_lb
        prev_tonnage_kg = w["tonnage_kg"] or prev_tonnage_kg

        top_sets_list: list[TopSetRecord] = []
        prs_list: list[PRRecord] = []
        if include_prs:
            for ex_id, d in ex_data.items():
                for (date_str, weight, unit, reps, e1) in d["best_sets"]:
                    if _week_start(date_str) == week:
                        top_sets_list.append(TopSetRecord(
                            exercise_id=ex_id, weight=weight, unit=unit, reps=reps, e1rm=e1, date=date_str
                        ))
                if ex_id in e1rm_pr_per_ex:
                    for (date_str, weight, unit, reps, e1) in ex_data[ex_id]["best_sets"]:
                        if _week_start(date_str) == week and e1 == e1rm_pr_per_ex[ex_id]:
                            prs_list.append(PRRecord(
                                exercise_id=ex_id, kind="e1rm_pr", weight=weight, unit=unit, reps=reps, e1rm=e1, date=date_str
                            ))
                            break

        weekly_out.append(WeeklyMetrics(
            week_start=week,
            sessions=len(w["sessions"]),
            hard_sets=w["hard_sets"],
            tonnage_lb=w["tonnage_lb"] or None,
            tonnage_kg=w["tonnage_kg"] or None,
            tonnage_excluded_sets=w.get("tonnage_excluded_sets", 0),
            tonnage_unknown_sets=w.get("tonnage_unknown_sets", 0),
            top_sets=top_sets_list,
            prs=prs_list,
            flags=flags,
        ))

    exercise_summaries = [
        ExerciseSummary(
            exercise_id=ex_id,
            sessions=len(d["sessions"]),
            best_e1rm=d["best_e1rm"],
            total_hard_sets=d["total_hard_sets"],
            rep_ranges=dict(d["rep_ranges"]) if d["rep_ranges"] else None,
        )
        for ex_id, d in sorted(ex_data.items())
    ]

    return ComputeMetricsOutput(
        status="ok",
        user_id=user_id,
        range=range_,
        weekly=weekly_out,
        exercise_summaries=exercise_summaries,
        signature=MetricsSignature(metrics_version=METRICS_VERSION),
    )

"""Tests for repstack.search_exercises: deterministic ordering, match metadata, equipment array."""

import pytest

from repstack.models import (
    SearchExerciseHit,
    SearchExercisesInput,
    SearchExercisesOutput,
)
from repstack.normalize import normalize_search_query, search_exercises


def test_search_response_shape() -> None:
    """Response has query, count, results; each result has match and is_exact_match."""
    data = search_exercises("squat", limit=5)
    assert "query" in data
    assert data["query"] == "squat"
    assert "count" in data
    assert "results" in data
    assert isinstance(data["results"], list)
    assert data["count"] == len(data["results"])
    for r in data["results"]:
        assert "exercise_id" in r
        assert "display" in r
        assert "aliases" in r
        assert "equipment" in r
        assert "movement_pattern" in r
        assert "match" in r
        assert "is_exact_match" in r
        m = r["match"]
        assert m["strategy"] in ("display_exact", "alias_exact", "starts_with", "contains")
        assert "score" in m
        assert "matched_text" in m
        assert "normalized_query" in m


def test_ordering_deterministic() -> None:
    """Results are sorted by score desc, is_exact_match desc, display asc; stable across runs."""
    data1 = search_exercises("row", limit=20)
    data2 = search_exercises("row", limit=20)
    ids1 = [r["exercise_id"] for r in data1["results"]]
    ids2 = [r["exercise_id"] for r in data2["results"]]
    assert ids1 == ids2
    for i in range(len(data1["results"]) - 1):
        a, b = data1["results"][i], data1["results"][i + 1]
        assert a["match"]["score"] >= b["match"]["score"]
        if a["match"]["score"] == b["match"]["score"]:
            assert a["is_exact_match"] >= b["is_exact_match"]
            if a["is_exact_match"] == b["is_exact_match"]:
                assert (a["display"] or "") <= (b["display"] or "")


def test_match_strategy_and_is_exact_match() -> None:
    """display_exact and alias_exact set is_exact_match=true; starts_with and contains set false."""
    exact = search_exercises("Barbell Bench Press", limit=1)
    assert len(exact["results"]) >= 1
    assert exact["results"][0]["match"]["strategy"] == "display_exact"
    assert exact["results"][0]["is_exact_match"] is True

    alias_exact = search_exercises("bench", limit=5)
    bench_hit = next((r for r in alias_exact["results"] if r["exercise_id"] == "barbell_bench_press"), None)
    assert bench_hit is not None
    assert bench_hit["match"]["strategy"] == "alias_exact"
    assert bench_hit["is_exact_match"] is True

    starts = search_exercises("Incline Dumbbell", limit=5)
    for r in starts["results"]:
        if r["match"]["strategy"] == "starts_with":
            assert r["is_exact_match"] is False
    contains = search_exercises("press", limit=5)
    for r in contains["results"]:
        if r["match"]["strategy"] == "contains":
            assert r["is_exact_match"] is False


def test_equipment_is_array() -> None:
    """Equipment is returned as list of strings."""
    data = search_exercises("Barbell Bench Press", limit=1)
    assert len(data["results"]) >= 1
    eq = data["results"][0]["equipment"]
    assert isinstance(eq, list)
    assert all(isinstance(x, str) for x in eq)
    assert "barbell" in eq or eq == ["barbell"]


def test_barbell_bench_first_display_exact() -> None:
    """Query 'Barbell Bench Press' returns barbell bench first with display_exact."""
    data = search_exercises("Barbell Bench Press", limit=5)
    assert data["count"] >= 1
    first = data["results"][0]
    assert first["exercise_id"] == "barbell_bench_press"
    assert first["display"] == "Barbell Bench Press"
    assert first["match"]["strategy"] == "display_exact"
    assert first["match"]["score"] == 1.0
    assert first["is_exact_match"] is True


def test_bench_alias_exact_is_exact_match_true() -> None:
    """Query 'bench' returns barbell_bench_press via alias_exact with is_exact_match=true."""
    data = search_exercises("bench", limit=20)
    bench = next((r for r in data["results"] if r["exercise_id"] == "barbell_bench_press"), None)
    assert bench is not None
    assert bench["match"]["strategy"] == "alias_exact"
    assert bench["is_exact_match"] is True
    assert bench["match"]["matched_text"] == "bench"


def test_incline_dumbbell_returns_bench_and_fly() -> None:
    """Query 'Incline Dumbbell' returns incline dumbbell bench and incline dumbbell fly with correct strategies/scores."""
    data = search_exercises("Incline Dumbbell", limit=10)
    ids = [r["exercise_id"] for r in data["results"]]
    assert "incline_dumbbell_bench_press" in ids
    assert "incline_dumbbell_fly" in ids
    bench = next(r for r in data["results"] if r["exercise_id"] == "incline_dumbbell_bench_press")
    fly = next(r for r in data["results"] if r["exercise_id"] == "incline_dumbbell_fly")
    assert bench["match"]["strategy"] == "starts_with"
    assert bench["match"]["score"] == 0.90
    assert fly["match"]["strategy"] == "starts_with"
    assert fly["match"]["score"] == 0.90
    # Deterministic order: same score, same is_exact_match, then by display asc -> Bench before Fly
    bench_idx = data["results"].index(bench)
    fly_idx = data["results"].index(fly)
    assert bench_idx < fly_idx


def test_triceps_pushdown_canonical() -> None:
    """Canonical ID is triceps_pushdown; search by 'tricep pushdown' or 'Triceps Pushdown' returns it."""
    by_display = search_exercises("Triceps Pushdown", limit=1)
    assert by_display["count"] >= 1
    assert by_display["results"][0]["exercise_id"] == "triceps_pushdown"
    assert by_display["results"][0]["display"] == "Triceps Pushdown"
    assert by_display["results"][0]["match"]["strategy"] == "display_exact"

    by_alias = search_exercises("tricep pushdown", limit=5)
    hit = next((r for r in by_alias["results"] if r["exercise_id"] == "triceps_pushdown"), None)
    assert hit is not None
    assert hit["match"]["strategy"] in ("alias_exact", "display_exact")


def test_normalize_search_query() -> None:
    """Normalization: lowercase, trim, collapse spaces, strip punctuation."""
    assert normalize_search_query("  Barbell Bench Press  ") == "barbell bench press"
    assert normalize_search_query("incline-dumbbell") == "incline dumbbell"
    assert normalize_search_query("pushdowns") == "pushdown"
    assert normalize_search_query("flyes") == "fly"


def test_search_filter_equipment() -> None:
    """Filter by equipment returns only matching exercises; equipment in results is array."""
    data = search_exercises("press", equipment="barbell", limit=30)
    for r in data["results"]:
        assert isinstance(r["equipment"], list)
        assert "barbell" in r["equipment"]


def test_search_filter_movement_pattern() -> None:
    """Filter by movement_pattern returns only matching exercises."""
    data = search_exercises("squat", movement_pattern="squat", limit=30)
    assert data["count"] >= 1
    for r in data["results"]:
        assert (r.get("movement_pattern") or "").lower() == "squat"


def test_search_limit_applied() -> None:
    """Limit caps the number of results."""
    data = search_exercises("e", limit=3)
    assert data["count"] <= 3
    assert len(data["results"]) <= 3


def test_search_empty_query_returns_empty() -> None:
    """Empty query returns count 0 and empty results."""
    data = search_exercises("", limit=10)
    assert data["query"] == ""
    assert data["count"] == 0
    assert data["results"] == []
    data2 = search_exercises("   ", limit=10)
    assert data2["count"] == 0
    assert data2["results"] == []


def test_search_no_match_returns_empty() -> None:
    """Query that matches nothing returns empty results."""
    data = search_exercises("xyznonexistent123", limit=10)
    assert data["count"] == 0
    assert data["results"] == []


def test_search_exercises_output_serialization() -> None:
    """SearchExercisesOutput builds from search_exercises dict and serializes for tool output."""
    inp = SearchExercisesInput(query="squat", limit=5)
    data = search_exercises(query=inp.query, limit=inp.limit or 20)
    out = SearchExercisesOutput(
        query=data["query"],
        count=data["count"],
        results=[SearchExerciseHit(**r) for r in data["results"]],
    )
    result = out.model_dump()
    assert result["query"] == "squat"
    assert "count" in result
    assert "results" in result
    for h in result["results"]:
        assert "exercise_id" in h
        assert "display" in h
        assert "equipment" in h
        assert isinstance(h["equipment"], list)
        assert "match" in h
        assert h["match"]["strategy"] in ("display_exact", "alias_exact", "starts_with", "contains")
        assert "is_exact_match" in h

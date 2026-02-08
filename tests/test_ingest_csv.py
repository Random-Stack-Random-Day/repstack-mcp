"""Deterministic CSV parse test for fitness.ingest_log."""

import tempfile
from pathlib import Path

import pytest

from repstack.ingest import ingest_log_impl, parse_csv
from repstack.models import IngestLogInput, IngestOptions, LogInput, UserInput


def test_parse_csv_deterministic() -> None:
    content = """exercise,weight,reps,unit
Bench Press,135,5,lb
Bench Press,145,4,lb
Squat,225,5,lb
"""
    parsed = parse_csv(content)  # list of (date|None, [(ex, sets), ...])
    assert len(parsed) == 1
    _, ex_list = parsed[0]
    assert len(ex_list) == 2
    assert ex_list[0][0] == "Bench Press"
    assert len(ex_list[0][1]) == 2
    assert ex_list[0][1][0]["weight"] == 135 and ex_list[0][1][0]["reps"] == 5
    assert ex_list[0][1][1]["weight"] == 145 and ex_list[0][1][1]["reps"] == 4
    assert ex_list[1][0] == "Squat"
    assert len(ex_list[1][1]) == 1
    assert ex_list[1][1][0]["weight"] == 225 and ex_list[1][1][0]["reps"] == 5


def test_parse_csv_bodyweight_and_plus() -> None:
    """Bodyweight and +25 emit load_type bodyweight/bodyweight_plus and weight None."""
    content = """exercise,weight,reps,unit
Pull Ups,Bodyweight,10,
Pull Ups,+25,6,lb
"""
    parsed = parse_csv(content)
    assert len(parsed) == 1
    _, ex_list = parsed[0]
    assert len(ex_list) == 1
    assert ex_list[0][0] == "Pull Ups"
    sets = ex_list[0][1]
    assert len(sets) == 2
    assert sets[0]["weight"] is None and sets[0]["load_type"] == "bodyweight" and sets[0]["reps"] == 10
    assert sets[1]["weight"] is None and sets[1]["load_type"] == "bodyweight_plus" and sets[1]["added_weight"] == 25 and sets[1]["reps"] == 6


def test_ingest_log_csv_returns_canonical_and_tonnage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        from repstack.storage import Storage
        storage = Storage(str(db))
        payload = IngestLogInput(
            user=UserInput(default_unit="lb", timezone="UTC"),
            log_input=LogInput(
                content_type="csv",
                content="""exercise,weight,reps
Bench Press,135,5
Bench Press,145,4
Squat,225,5
""",
            ),
        )
        result = ingest_log_impl(payload, storage=storage)
        storage.close()
        assert result.status == "ok"
        assert result.log_id is not None and result.log_id.startswith("log_")
        assert result.summary.sessions_detected == 1
        assert result.summary.exercises_detected == 2
        assert result.summary.sets_detected == 3
        assert len(result.canonical_log.sessions) == 1
        sess = result.canonical_log.sessions[0]
        assert len(sess.exercises) == 2
        bench = next(e for e in sess.exercises if "bench" in e.exercise_id.lower())
        assert len(bench.sets) == 2
        assert bench.sets[0].weight == 135 and bench.sets[0].reps == 5
        assert result.signature.canonical_sha256
        assert result.signature.parser_version


def test_seated_row_maps_to_seated_row_not_barbell_row() -> None:
    """Conservative mapping: Seated Row must map to seated_row, not barbell_row."""
    from repstack.normalize import resolve_exercise_id
    eid, _ = resolve_exercise_id("Seated Row")
    assert eid == "seated_row"
    eid2, _ = resolve_exercise_id("Barbell Row")
    assert eid2 == "barbell_row"


def test_romanian_deadlift_maps_to_romanian_deadlift_not_deadlift() -> None:
    """Conservative mapping: Romanian Deadlift maps to romanian_deadlift, not deadlift."""
    from repstack.normalize import resolve_exercise_id
    eid, _ = resolve_exercise_id("Romanian Deadlift")
    assert eid == "romanian_deadlift"
    eid2, _ = resolve_exercise_id("Deadlift")
    assert eid2 == "deadlift"


def test_unmapped_exercise_emits_issue_with_location_and_raw_excerpt() -> None:
    """Every unmapped exercise produces an unmapped_exercise warning with location and raw_excerpt."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nObscureLift XYZ,50,10\n",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    result = ingest_log_impl(payload, storage=None)
    unmapped_issues = [i for i in result.issues if i.type == "unmapped_exercise"]
    assert len(unmapped_issues) == 1
    assert unmapped_issues[0].raw_excerpt == "ObscureLift XYZ"
    assert "sessions" in unmapped_issues[0].location and "exercises" in unmapped_issues[0].location
    # No close match for obscure name -> no suggestions or empty
    assert unmapped_issues[0].suggested_exercise_ids is None or len(unmapped_issues[0].suggested_exercise_ids) == 0


def test_unmapped_exercise_includes_suggested_ids_when_close_match() -> None:
    """Unmapped exercise gets up to 3 suggested_exercise_ids when display/alias substring matches."""
    # "Seated Ro" is unmapped but "seated ro" is substring of "Seated Row" -> suggest seated_row
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nSeated Ro,50,10\n",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    result = ingest_log_impl(payload, storage=None)
    unmapped_issues = [i for i in result.issues if i.type == "unmapped_exercise"]
    assert len(unmapped_issues) == 1
    assert unmapped_issues[0].suggested_exercise_ids is not None
    assert "seated_row" in unmapped_issues[0].suggested_exercise_ids
    assert len(unmapped_issues[0].suggested_exercise_ids) <= 3


def test_swap_prevention_incline_vs_flat_bench() -> None:
    """Incline Barbell Bench Press must not map to flat barbell_bench_press."""
    from repstack.normalize import resolve_exercise
    eid, _, strategy, _ = resolve_exercise("Incline Barbell Bench Press")
    assert eid == "incline_barbell_bench_press"
    assert eid != "barbell_bench_press"
    eid_flat, _, _, _ = resolve_exercise("Barbell Bench Press")
    assert eid_flat == "barbell_bench_press"


def test_swap_prevention_smith_vs_barbell() -> None:
    """Smith Machine Bench Press must not map to barbell_bench_press."""
    from repstack.normalize import resolve_exercise
    eid, _, _, _ = resolve_exercise("Smith Machine Bench Press")
    assert eid == "smith_machine_bench_press"
    assert eid != "barbell_bench_press"


def test_swap_prevention_dumbbell_vs_barbell_bench() -> None:
    """Dumbbell Bench Press must not map to barbell_bench_press."""
    from repstack.normalize import resolve_exercise
    eid, _, _, _ = resolve_exercise("Dumbbell Bench Press")
    assert eid == "dumbbell_bench_press"
    assert eid != "barbell_bench_press"


def test_source_pack_resolution_when_source_provided() -> None:
    """When log_input.source.app is set (e.g. hevy), source pack is used first."""
    from repstack.models import LogInputSource
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nPull Ups,0,10\n",
            source=LogInputSource(app="hevy"),
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    result = ingest_log_impl(payload, storage=None)
    ex = result.canonical_log.sessions[0].exercises[0]
    assert ex.exercise_id == "pull_up"
    assert ex.mapping is not None
    assert ex.mapping.strategy == "source_pack"
    assert ex.mapping.score == 1.0


def test_canonical_exercise_includes_mapping_strategy_and_score() -> None:
    """Each exercise in canonical output has mapping.strategy and mapping.score."""
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(
            content_type="csv",
            content="exercise,weight,reps\nBench Press,135,5\n",
        ),
        options=IngestOptions(session_date_hint="2025-01-15"),
    )
    result = ingest_log_impl(payload, storage=None)
    assert result.canonical_log.sessions
    ex = result.canonical_log.sessions[0].exercises[0]
    assert ex.mapping is not None
    assert ex.mapping.strategy in ("source_pack", "global_alias", "registry_display", "registry_alias", "unmapped")
    assert 0 <= ex.mapping.score <= 1

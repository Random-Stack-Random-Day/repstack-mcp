"""Text parse test with mocked LLM output."""

import tempfile
from pathlib import Path

import pytest

from repstack.ingest import ingest_log_impl
from repstack.llm_parser import set_llm_parser
from repstack.models import IngestLogInput, IngestOptions, LogInput, UserInput


def _mock_llm_parser(content: str, session_date_hint: str | None) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """Return one session with one exercise and two sets from 'Bench 135x5 145x4' style input."""
    date = session_date_hint or "2025-01-15"
    # Simulate parsing "Bench 135x5 145x4"
    return [
        (date, [
            ("Bench Press", [
                {"weight": 135, "reps": 5, "unit": "lb"},
                {"weight": 145, "reps": 4, "unit": "lb"},
            ]),
        ]),
    ]


def test_ingest_text_with_mocked_llm() -> None:
    set_llm_parser(_mock_llm_parser)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            from repstack.storage import Storage
            storage = Storage(str(Path(tmp) / "test.db"))
            payload = IngestLogInput(
                user=UserInput(default_unit="lb", timezone="UTC"),
                log_input=LogInput(
                    content_type="text",
                    content="Bench 135x5 145x4",
                ),
                options=IngestOptions(allow_llm=True, session_date_hint="2025-01-15"),
            )
            result = ingest_log_impl(payload, storage=storage, llm_parser=_mock_llm_parser)
            storage.close()
            assert result.status == "ok"
            assert len(result.canonical_log.sessions) == 1
            sess = result.canonical_log.sessions[0]
            assert sess.date == "2025-01-15"
            assert len(sess.exercises) == 1
            assert sess.exercises[0].exercise_display == "Bench Press"
            assert len(sess.exercises[0].sets) == 2
            assert sess.exercises[0].sets[0].weight == 135 and sess.exercises[0].sets[0].reps == 5
    finally:
        set_llm_parser(None)


def test_ingest_text_without_llm_returns_issue_when_allow_llm_false() -> None:
    set_llm_parser(None)
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="text", content="some random text"),
        options=IngestOptions(allow_llm=False),
    )
    result = ingest_log_impl(payload, storage=None, llm_parser=None)
    assert result.status != "ok"
    assert any(i.type == "parse_error" or "parse" in i.message.lower() for i in result.issues)


def test_ingest_text_allow_llm_true_no_parser_returns_blocking_issue() -> None:
    payload = IngestLogInput(
        user=UserInput(default_unit="lb", timezone="UTC"),
        log_input=LogInput(content_type="text", content="workout notes"),
        options=IngestOptions(allow_llm=True),
    )
    result = ingest_log_impl(payload, storage=None, llm_parser=None)
    assert any(i.severity == "blocking" and "llm" in i.message.lower() for i in result.issues)

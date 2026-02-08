"""LLM parser interface for messy text workout logs. Stub when no LLM is configured."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .models import IngestOptions


# Type: (content: str, session_date_hint: str | None) -> list[tuple[str, list[tuple[str, list[dict]]]]]
# Each item: (session_date_YYYY_MM_DD, [(exercise_name, [set_dict, ...]), ...])
LLMParserFn = Callable[[str, Optional[str]], list[tuple[str, list[tuple[str, list[dict]]]]]]

_parser: Optional[LLMParserFn] = None


def set_llm_parser(fn: LLMParserFn | None) -> None:
    """Register the LLM parser (e.g. from env-configured OpenAI). Set to None to disable."""
    global _parser
    _parser = fn


def get_llm_parser() -> Optional[LLMParserFn]:
    """Return the configured LLM parser or None."""
    return _parser


def parse_text_with_llm(content: str, session_date_hint: str | None) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """
    Call the configured LLM parser. If none configured, raises a clear error.
    Returns list of (session_date, [(exercise_name, sets), ...]).
    """
    parser = get_llm_parser()
    if parser is None:
        raise RuntimeError(
            "LLM parser is not configured. Set REPSTACK_MCP_LLM_* env or call set_llm_parser()."
        )
    return parser(content, session_date_hint)

"""LLM parser interface for messy text workout logs. Provider-agnostic; swap via env or registration."""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .models import IngestOptions


# Parser signature: (content: str, session_date_hint: str | None) -> raw_sessions
# raw_sessions: list of (date_YYYY_MM_DD or None, [(exercise_name, [set_dict, ...]), ...])
LLMParserFn = Callable[[str, Optional[str]], list[tuple[str, list[tuple[str, list[dict]]]]]]

# Provider loader: () -> LLMParserFn | None (reads own env, returns parser or None)
LLMParserLoader = Callable[[], Optional[LLMParserFn]]

_parser: Optional[LLMParserFn] = None  # set via set_llm_parser(); takes precedence
_env_parser: Optional[LLMParserFn] = None  # lazy-loaded from REPSTACK_LLM_PROVIDER

_PROVIDERS: dict[str, LLMParserLoader] = {}


def register_llm_provider(name: str, loader: LLMParserLoader) -> None:
    """Register a provider that can be selected via REPSTACK_LLM_PROVIDER.
    loader is a no-arg callable that returns a parser (or None if env not configured)."""
    _PROVIDERS[name.strip().lower()] = loader


def set_llm_parser(fn: LLMParserFn | None) -> None:
    """Set the LLM parser directly (e.g. from your own code). Takes precedence over env/provider.
    Set to None to clear and fall back to provider from env."""
    global _parser
    _parser = fn


def get_llm_parser() -> Optional[LLMParserFn]:
    """Return the configured parser or None.
    Order: 1) set_llm_parser(), 2) env REPSTACK_LLM_PROVIDER (e.g. openai) -> that provider's loader."""
    global _env_parser
    if _parser is not None:
        return _parser
    if _env_parser is not None:
        return _env_parser
    _env_parser = _create_parser_from_env()
    return _env_parser


def _create_parser_from_env() -> Optional[LLMParserFn]:
    """If REPSTACK_LLM_PROVIDER is set, use that provider's loader. Otherwise None.
    Backward compat: if REPSTACK_OPENAI_API_KEY is set and provider unset, default to openai."""
    provider = os.environ.get("REPSTACK_LLM_PROVIDER", "").strip().lower()
    if not provider and os.environ.get("REPSTACK_OPENAI_API_KEY", "").strip():
        provider = "openai"
    if not provider:
        return None
    loader = _PROVIDERS.get(provider)
    if not loader:
        return None
    return loader()


# --- Shared contract: prompt and JSON response parsing (any provider can use same shape) ---

WORKOUT_EXTRACTION_SYSTEM = """You extract workout sessions from unstructured text. Return ONLY valid JSON, no markdown or explanation.
Format: { "sessions": [ { "date": "YYYY-MM-DD", "exercises": [ { "name": "Exercise Name", "sets": [ { "weight": number or null for bodyweight, "reps": number, "unit": "lb" or "kg", "load_type": "weighted"|"bodyweight"|"bodyweight_plus" } ] } ] } ] }
- date: use the provided hint if text has no date.
- weight: null for bodyweight; number for weighted; for "bodyweight_plus" (e.g. +25 lb) use load_type "bodyweight_plus" and put the added weight in "weight" with unit (or "added_weight").
- Each set: at least "reps"; "weight" and "unit" when not bodyweight."""


def parse_llm_workout_json(text: str) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """Parse LLM JSON response into raw_sessions. Use from any provider that returns this JSON shape."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    data = json.loads(text)
    sessions = data.get("sessions") or []
    out: list[tuple[str, list[tuple[str, list[dict]]]]] = []
    for s in sessions:
        date_str = (s.get("date") or "").strip() or None
        exercises: list[tuple[str, list[dict]]] = []
        for ex in s.get("exercises") or []:
            name = (ex.get("name") or "").strip() or "Unknown"
            sets_raw = ex.get("sets") or []
            set_list: list[dict] = []
            for st in sets_raw:
                set_list.append({
                    "weight": st.get("weight"),
                    "reps": int(st.get("reps", 0)),
                    "unit": (st.get("unit") or "lb").strip(),
                    "load_type": (st.get("load_type") or "weighted").strip().lower(),
                    "added_weight": st.get("added_weight"),
                })
            exercises.append((name, set_list))
        out.append((date_str if date_str else None, exercises))
    return out


# --- Built-in: OpenAI provider (optional dependency) ---

def _load_openai_parser() -> Optional[LLMParserFn]:
    """Load OpenAI-based parser if REPSTACK_OPENAI_API_KEY is set. Requires openai package."""
    api_key = os.environ.get("REPSTACK_OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("REPSTACK_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        from openai import OpenAI
    except ImportError:
        return None
    client = OpenAI(api_key=api_key)

    def _openai_parser(content: str, session_date_hint: Optional[str]) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
        hint = session_date_hint or "today (use YYYY-MM-DD)"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": WORKOUT_EXTRACTION_SYSTEM},
                {"role": "user", "content": f"Session date hint: {hint}\n\nExtract workout:\n\n{content}"},
            ],
            temperature=0.1,
        )
        choice = resp.choices and resp.choices[0]
        if not choice or not choice.message or not choice.message.content:
            return []
        return parse_llm_workout_json(choice.message.content)

    return _openai_parser


# Register built-in provider so REPSTACK_LLM_PROVIDER=openai works
register_llm_provider("openai", _load_openai_parser)


def parse_text_with_llm(content: str, session_date_hint: str | None) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """Call the configured LLM parser. If none configured, raises."""
    parser = get_llm_parser()
    if parser is None:
        raise RuntimeError(
            "LLM parser not configured. Set REPSTACK_LLM_PROVIDER (e.g. openai) and provider env vars, or call set_llm_parser(fn)."
        )
    return parser(content, session_date_hint)

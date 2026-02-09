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
LLMParserFn = Callable[[str, Optional[str]], list[tuple[Optional[str], list[tuple[str, list[dict]]]]]]

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
Format: { "sessions": [ { "date": "YYYY-MM-DD", "exercises": [ { "name": "Exercise Name", "sets": [ { "reps": number, "weight": number or null, "unit": "lb" or "kg" (only when weight present), "load_type": "weighted"|"bodyweight"|"bodyweight_plus", "added_weight": number only for bodyweight_plus } ] } ] } ] }
- date: use the provided hint if text has no date.
- Each set: "reps" is required. Skip sets without valid reps.
- weight: null for bodyweight; number for weighted. For bodyweight_plus (e.g. +25 lb) use weight=null and "added_weight": 25 with "unit" for the added weight; do NOT put added weight in "weight"."""


def _extract_json_object(text: str) -> dict:
    """Strip markdown/code fences and extract a single JSON object from text."""
    text = text.strip()
    # Remove markdown code block
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    # Find first { and last } to extract JSON object (handles trailing/leading text)
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError("Unbalanced braces")
    return json.loads(text[start : end + 1])


def parse_llm_workout_json(text: str) -> list[tuple[Optional[str], list[tuple[str, list[dict]]]]]:
    """Parse LLM JSON response into raw_sessions. Skips sets with missing/invalid reps; infers load_type; prefers added_weight for bodyweight_plus."""
    data = _extract_json_object(text)
    sessions = data.get("sessions") or []
    out: list[tuple[Optional[str], list[tuple[str, list[dict]]]]] = []
    for s in sessions:
        date_str = (s.get("date") or "").strip() or None
        exercises: list[tuple[str, list[dict]]] = []
        for ex in s.get("exercises") or []:
            name = (ex.get("name") or "").strip() or "Unknown"
            sets_raw = ex.get("sets") or []
            set_list: list[dict] = []
            for st in sets_raw:
                try:
                    reps_val = st.get("reps")
                    if reps_val is None:
                        continue
                    reps = int(reps_val)
                    if reps < 1:
                        continue
                except (TypeError, ValueError):
                    continue
                weight = st.get("weight")
                added_weight = st.get("added_weight")
                if added_weight is not None:
                    try:
                        added_weight = float(added_weight)
                    except (TypeError, ValueError):
                        added_weight = None
                load_type = (st.get("load_type") or "").strip().lower()
                if not load_type or load_type not in ("weighted", "bodyweight", "bodyweight_plus"):
                    if weight is not None and (isinstance(weight, (int, float)) or (isinstance(weight, str) and weight.strip())):
                        try:
                            float(weight)
                            load_type = "weighted"
                        except (TypeError, ValueError):
                            load_type = "bodyweight" if added_weight is None else "bodyweight_plus"
                    elif added_weight is not None:
                        load_type = "bodyweight_plus"
                    else:
                        load_type = "bodyweight"
                unit = (st.get("unit") or "").strip() or None
                if weight is not None and unit is None:
                    unit = "lb"
                if load_type == "bodyweight_plus" and added_weight is not None:
                    set_list.append({
                        "weight": None,
                        "reps": reps,
                        "unit": unit or "lb",
                        "load_type": "bodyweight_plus",
                        "added_weight": added_weight,
                    })
                elif load_type == "bodyweight":
                    set_list.append({
                        "weight": None,
                        "reps": reps,
                        "unit": unit,
                        "load_type": "bodyweight",
                        "added_weight": None,
                    })
                else:
                    if weight is None:
                        set_list.append({
                            "weight": None,
                            "reps": reps,
                            "unit": unit,
                            "load_type": "bodyweight",
                            "added_weight": None,
                        })
                    else:
                        try:
                            w = float(weight)
                        except (TypeError, ValueError):
                            continue
                        set_list.append({
                            "weight": w,
                            "reps": reps,
                            "unit": unit or "lb",
                            "load_type": "weighted",
                            "added_weight": None,
                        })
            if set_list:
                exercises.append((name, set_list))
        if exercises:
            out.append((date_str, exercises))
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

    def _openai_parser(content: str, session_date_hint: Optional[str]) -> list[tuple[Optional[str], list[tuple[str, list[dict]]]]]:
        hint = session_date_hint or "today (use YYYY-MM-DD)"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": WORKOUT_EXTRACTION_SYSTEM},
                {"role": "user", "content": f"Session date hint: {hint}\n\nExtract workout:\n\n{content}"},
            ],
            temperature=0.0,
        )
        choice = resp.choices and resp.choices[0]
        if not choice or not choice.message or not choice.message.content:
            return []
        return parse_llm_workout_json(choice.message.content)

    return _openai_parser


# Register built-in provider so REPSTACK_LLM_PROVIDER=openai works
register_llm_provider("openai", _load_openai_parser)


def parse_text_with_llm(content: str, session_date_hint: str | None) -> list[tuple[Optional[str], list[tuple[str, list[dict]]]]]:
    """Call the configured LLM parser. If none configured, raises."""
    parser = get_llm_parser()
    if parser is None:
        raise RuntimeError(
            "LLM parser not configured. Set REPSTACK_LLM_PROVIDER (e.g. openai) and provider env vars, or call set_llm_parser(fn)."
        )
    return parser(content, session_date_hint)

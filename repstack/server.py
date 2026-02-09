"""MCP server: repstack.ingest_log, repstack.compute_metrics, repstack.search_exercises. Tool-only, stateless."""

from __future__ import annotations

from fastmcp import FastMCP

from .ingest import ingest_log_impl
from .metrics import compute_metrics_impl
from .models import (
    ComputeMetricsInput,
    IngestLogInput,
    SearchExerciseHit,
    SearchExercisesInput,
    SearchExercisesOutput,
)
from .normalize import search_exercises

mcp = FastMCP(name="repstack")


@mcp.tool(name="repstack.ingest_log")
def repstack_ingest_log(payload: dict) -> dict:
    """
    Ingest a workout log (text, CSV, or JSON). Returns canonical structured JSON, issues, and summary.
    Stateless: does not store anything. Set allow_llm=true for text and configure an LLM parser to use it; response includes meta.llm_available and meta.llm_used.
    """
    inp = IngestLogInput.model_validate(payload)
    llm_parser = None
    try:
        from .llm_parser import get_llm_parser
        llm_parser = get_llm_parser()
    except Exception:
        pass
    result = ingest_log_impl(inp, llm_parser=llm_parser)
    return result.model_dump()


@mcp.tool(name="repstack.compute_metrics")
def repstack_compute_metrics(payload: dict) -> dict:
    """
    Compute deterministic metrics from provided canonical data (stateless).
    Provide either `sessions` (array of canonical session objects) or `logs` (array of { canonical_json: { sessions } }).
    Optional `range`: { start, end } (YYYY-MM-DD) to filter; if omitted, all provided sessions are used.
    Returns weekly volume, tonnage, e1rm, PRs, volume_spike flags. Payloads exceeding max_sessions or max_sets return needs_clarification.
    """
    inp = ComputeMetricsInput.model_validate(payload)
    result = compute_metrics_impl(inp)
    return result.model_dump()


@mcp.tool(name="repstack.search_exercises")
def repstack_search_exercises(payload: dict) -> dict:
    """
    Search the local exercise registry by query (matches display and aliases).
    Returns { query, count, results } with match metadata (strategy, score, matched_text, normalized_query)
    and is_exact_match. Optional filters: equipment, movement_pattern. Optional limit (default 20).
    """
    inp = SearchExercisesInput.model_validate(payload)
    limit = inp.limit if inp.limit is not None else 20
    data = search_exercises(
        query=inp.query,
        equipment=inp.equipment,
        movement_pattern=inp.movement_pattern,
        limit=max(0, min(limit, 100)),
    )
    out = SearchExercisesOutput(
        query=data["query"],
        count=data["count"],
        results=[SearchExerciseHit(**r) for r in data["results"]],
    )
    return out.model_dump()


def run() -> None:
    """Run the MCP server with stdio transport (default)."""
    mcp.run()

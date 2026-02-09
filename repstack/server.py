"""MCP server: repstack.ingest_log, repstack.compute_metrics, and read-only resources."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastmcp import FastMCP

from .ingest import ingest_log_impl
from .metrics import compute_metrics_impl
from .models import (
    ComputeMetricsInput,
    DateRange,
    IngestLogInput,
    SearchExerciseHit,
    SearchExercisesInput,
    SearchExercisesOutput,
)
from .normalize import search_exercises
from .storage import Storage

# Default DB next to server (or use REPSTACK_DB_PATH)
_db_path = os.environ.get("REPSTACK_DB_PATH", str(Path(__file__).parent.parent / "repstack.db"))
_storage = Storage(_db_path)

mcp = FastMCP(name="repstack")


@mcp.tool(name="repstack.ingest_log")
def repstack_ingest_log(payload: dict) -> dict:
    """
    Ingest a workout log (text, CSV, or JSON). Returns canonical structured JSON, issues, and summary.
    When content_type is text, set allow_llm=true to use LLM parsing if configured.
    """
    inp = IngestLogInput.model_validate(payload)
    llm_parser = None
    try:
        from .llm_parser import get_llm_parser
        llm_parser = get_llm_parser()
    except Exception:
        pass
    result = ingest_log_impl(inp, storage=_storage, llm_parser=llm_parser)
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


@mcp.resource("log://{log_id}/canonical", mime_type="application/json")
def resource_log_canonical(log_id: str) -> str:
    """Read-only: canonical JSON for a given log_id."""
    row = _storage.get_log(log_id)
    if not row:
        return json.dumps({"error": "log not found", "log_id": log_id})
    return json.dumps(row["canonical_json"], indent=2)


@mcp.resource("log://{log_id}/issues", mime_type="application/json")
def resource_log_issues(log_id: str) -> str:
    """Read-only: issues for a given log_id."""
    issues = _storage.get_issues(log_id)
    return json.dumps(issues, indent=2)


@mcp.resource("user://{user_id}/recent_summary", mime_type="application/json")
def resource_user_recent_summary(user_id: str) -> str:
    """Read-only: last 30 days metrics summary for user."""
    from datetime import datetime, timedelta
    end = datetime.utcnow().date()
    start = end - timedelta(days=30)
    range_ = DateRange(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    logs = _storage.get_logs_for_user(user_id, range_.start, range_.end)
    inp = ComputeMetricsInput(logs=logs, range=range_)
    result = compute_metrics_impl(inp)
    out = result.model_dump()
    out["user_id"] = user_id
    return json.dumps(out, indent=2)


def run() -> None:
    """Run the MCP server with stdio transport (default)."""
    mcp.run()

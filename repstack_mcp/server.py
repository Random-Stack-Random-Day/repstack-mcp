"""MCP server: fitness.ingest_log, fitness.compute_metrics, and read-only resources."""

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
)
from .storage import Storage

# Default DB next to server (or use REPSTACK_MCP_DB_PATH)
_db_path = os.environ.get("REPSTACK_MCP_DB_PATH", str(Path(__file__).parent.parent / "repstack.db"))
_storage = Storage(_db_path)

mcp = FastMCP(name="repstack-mcp")


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
    Compute deterministic metrics over stored logs: weekly volume, tonnage, e1rm, PRs, volume_spike flags.
    """
    inp = ComputeMetricsInput.model_validate(payload)
    result = compute_metrics_impl(inp, storage=_storage)
    return result.model_dump()


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
    inp = ComputeMetricsInput(user_id=user_id, range=range_)
    result = compute_metrics_impl(inp, storage=_storage)
    return json.dumps(result.model_dump(), indent=2)


def run() -> None:
    """Run the MCP server with stdio transport (default)."""
    mcp.run()

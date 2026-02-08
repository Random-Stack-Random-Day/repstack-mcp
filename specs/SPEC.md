# Project: Repstack MCP (v1) — Log Ingestion + Metrics

You are my build partner in Cursor. We are vibe coding, but we must stay disciplined and ship a working v1.

## Goal (v1 scope)
Build an MCP Server called **repstack-mcp** that exposes **two tools**:
1) `repstack.ingest_log` — accepts messy workout logs (text/csv/json), uses an LLM-assisted parse when needed, returns **canonical structured JSON + issues**.
2) `repstack.compute_metrics` — deterministic analytics over stored canonical logs: weekly volume, tonnage, PRs, estimated 1RM, and flags (volume spikes, missed weeks).

This MCP should be usable by multiple clients (Cursor/Claude Desktop/etc.). The MCP is the capability layer; UI is out of scope.

## Transport
Use MCP stdio transport (client spawns the server as a subprocess).

## Tech choice
Implement in **Python** (preferred for data analysis). Use:
- FastMCP / MCP Python SDK server pattern
- Pydantic models for schemas + validation
- SQLite for storage (simple local DB), with a clear path to Postgres later
- pandas or polars + duckdb optional (choose simplest)
No web UI.

## Tool contracts (must implement exactly)
### Tool: repstack.ingest_log
**Input JSON**
- user: { user_id?: string, default_unit: "lb"|"kg", timezone: string }
- log_input: { content_type: "text"|"csv"|"json", content: string, source?: { app?: string, filename?: string } }
- options?: { session_date_hint?: "YYYY-MM-DD", allow_llm?: boolean, strictness?: "normal"|"strict", dedupe_strategy?: "none"|"by_hash"|"by_date_exercise" }

**Output JSON**
- status: "ok"|"needs_clarification"|"error"
- user_id: string
- log_id: string
- canonical_log: { sessions: [...] }  (canonical schema below)
- issues: [ { severity: "warning"|"blocking", type: string, location: string, message: string, question_to_user?: string, options?: string[] } ]
- summary: { sessions_detected: int, exercises_detected: int, sets_detected: int, unmapped_exercises: int, confidence: float }
- signature: { canonical_sha256: string, parser_version: string }

### Canonical schema (internal + returned)
sessions[]:
- session_id, date (YYYY-MM-DD), title?, notes?
- exercises[]:
  - exercise_raw, exercise_id (snake_case or "unmapped:<slug>"), exercise_display
  - sets[]:
    - set_index, weight (float), unit ("lb"|"kg"), reps (int), rpe? (float), set_type? ("warmup"|"working"|"top"|"backoff"), notes?

Normalization rules:
- Standardize units and dates.
- Create stable exercise_id mapping (simple dictionary + fallback slugging).
- Never silently guess if ambiguity is high: emit `issues[]` with severity blocking when strictness=strict.
- Always compute and return a canonical_sha256 of normalized JSON.

Parsing strategy (v1):
- If content_type=csv or json and matches expected columns/shape -> parse deterministically (no LLM).
- If text or unknown structure -> call an LLM parser *only if allow_llm=true*.
- Regardless of LLM use, validate into canonical schema and normalize.

### Tool: repstack.compute_metrics
**Input JSON**
- user_id: string
- range: { start: "YYYY-MM-DD", end: "YYYY-MM-DD" }
- options?: { group_by?: string[], e1rm_formula?: "epley"|"brzycki", volume_metric?: "tonnage"|"hard_sets", include_prs?: boolean }

**Output JSON**
- status: "ok"|"error"
- user_id, range
- weekly[]:
  - week_start (YYYY-MM-DD), sessions, hard_sets, tonnage_(lb/kg), muscle_group_sets (optional v1), top_sets[], prs[], flags[]
- exercise_summaries[]:
  - exercise_id, sessions, best_e1rm, total_hard_sets, rep_ranges bucketed
- signature: { metrics_version: string }

Metrics rules (v1):
- tonnage = sum(weight * reps) per unit (no unit conversion required in v1; keep separate if mixed units).
- hard_sets = count of sets excluding warmups when set_type is present; otherwise count all sets.
- e1rm using selected formula on best set per exercise/day (simple).
- PR detection: rep PR at a given weight, and best e1rm PR.
- Flags: volume_spike if hard_sets or tonnage increases >25% week-over-week.

## MCP Resources (v1 minimal)
Implement read-only resources:
- log://{log_id}/canonical
- log://{log_id}/issues
- user://{user_id}/recent_summary (last 30 days metrics summary)

## Storage (SQLite)
Tables:
- users (user_id, default_unit, timezone, created_at)
- logs (log_id, user_id, canonical_json, canonical_sha256, created_at)
- issues (log_id, issue_json)
- metrics_cache (optional)

## Deliverables
1) A runnable MCP server with these tools + resources.
2) A `README.md` that explains how to run it and how to configure in a client via stdio.
3) A `tests/` folder with at least:
   - deterministic CSV parse test
   - text parse test with mocked LLM output
   - metrics computation test for tonnage + volume_spike flag

## Constraints
- Keep code small and readable.
- No “magic”: every tool returns validated JSON.
- No extra features beyond v1 scope.
- Prefer deterministic logic; LLM only for messy text parsing.
- If LLM integration is not configured, tool must still work with deterministic paths and return a clear error/issue when text parsing is requested.

## Step-by-step execution plan (what you should do now)
1) Propose repository structure.
2) Define Pydantic models for inputs/outputs and canonical log.
3) Implement SQLite storage layer.
4) Implement ingest_log deterministic parsers + normalization + hashing.
5) Stub LLM parser behind an interface + add mocking for tests.
6) Implement compute_metrics.
7) Implement MCP server wiring: tools + resources.
8) Write README + tests.

Start by generating the repo structure and the Pydantic models.

# RepStack

RepStack is a **stateless** MCP server that normalizes strength training logs into a deterministic canonical schema.

It does **not** store users or logs. It does **not** persist data. It does **not** act as an analytics backend.

Consumers are responsible for storing canonical output and building analytics downstream.

RepStack is designed to be embedded into larger fitness applications as a normalization layer.

---

## What RepStack Does

- **Normalize** workout logs (CSV, JSON, or freeform text) into a canonical schema
- **Optionally** use an LLM for text pre-parsing (output still goes through deterministic validation)
- **Compute** deterministic metrics over **provided** canonical sessions or logs
- **Provide** exercise search / registry lookup

RepStack is a **canonicalization + deterministic compute engine**. All storage responsibility belongs to the consuming application.

---

## Design Philosophy

RepStack is deterministic.

- **Canonical structure** is enforced via schema validation.
- **LLM parsing** (optional) is only used for text extraction; canonical correctness is never defined by LLM output.
- **All analytics** must operate on canonical sessions passed explicitly to the tool (e.g. `repstack.compute_metrics` with `sessions` or `logs` in the payload).
- **Uncertain matches** → `exercise_id: "unmapped:<slug>"`; no fuzzy auto-mapping. Candidates can be provided for partial matches.

---

## Quickstart

**Run the MCP server**

```bash
pip install -r requirements.txt
python -m repstack.server
```

Or after editable install: `repstack`

**Call the ingest tool**

Example payload (CSV):

```json
{
  "user": { "default_unit": "lb", "timezone": "UTC" },
  "log_input": {
    "content_type": "csv",
    "content": "exercise,weight,reps\nBench Press,135,5\nSquat,225,5"
  },
  "options": { "session_date_hint": "2025-01-15" }
}
```

Example output shape:

- `status`: `"ok"` | `"needs_clarification"` | `"error"`
- `log_id`: request-scoped id when ok (client may use as storage key)
- `canonical_log`: `{ "sessions": [ { "date", "exercises": [ { "exercise_id", "sets": [...] } ] } ] }`
- `issues`: list of `{ severity, type, location, message, ... }`
- `summary`: `{ sessions_detected, exercises_detected, sets_detected, confidence }`
- `meta`: `{ "llm_available": bool, "llm_used": bool }` (when LLM is relevant)

**Call compute_metrics**

Send canonical data in the request (no server-side storage):

```json
{
  "sessions": [ { "date": "2025-01-15", "exercises": [ { "exercise_id": "barbell_bench_press", "sets": [ { "weight": 135, "unit": "lb", "reps": 5, "load_type": "weighted" } ] } ] } ],
  "range": { "start": "2025-01-01", "end": "2025-01-31" }
}
```

Or send `logs`: array of `{ "canonical_json": { "sessions": [...] } }`.

Response: `status`, `range`, `weekly` (volume, tonnage, hard_sets, flags), `exercise_summaries`, `issues` (e.g. `payload_too_large` if over limits).

---

## Tool Contracts

### Tool: repstack.ingest_log

- **Input**: `user` (optional `user_id`, `default_unit`, `timezone`), `log_input` (`content_type`: `"csv"` | `"json"` | `"text"`, `content`), optional `options` (`session_date_hint`, `allow_llm`, `strictness`, …).
- **Output**: `status`, `user_id`, `log_id` (when ok), `canonical_log`, `issues`, `summary`, `signature`, `meta` (`llm_available`, `llm_used`). No persistence.

### Tool: repstack.compute_metrics

- **Input**: **either** `sessions` (array of canonical session objects) **or** `logs` (array of `{ canonical_json: { sessions } }`). Optional `range`: `{ start, end }` (YYYY-MM-DD). Optional `options` (e1rm_formula, include_prs, …).
- **Output**: Deterministic metrics only: `status`, `range`, `weekly`, `exercise_summaries`, `issues` (e.g. `payload_too_large`), `signature`. No user identity; no storage access.

### Tool: repstack.search_exercises

- **Input**: `query`, optional `equipment`, `movement_pattern`, `limit`.
- **Output**: `query`, `count`, `results` with `exercise_id`, `display`, `match` (strategy, score, matched_text, normalized_query), `is_exact_match`.

---

## MCP Surface (Tools Only)

- **repstack.ingest_log** — Normalize a workout log. Returns canonical log, issues, summary. Stateless.
- **repstack.compute_metrics** — Compute metrics from provided `sessions` or `logs`. Stateless; guardrails for payload size.
- **repstack.search_exercises** — Search exercise registry by query; optional filters.

There are **no MCP resources** (no `log://`, no `user://`). Tool-only.

---

## Canonical Data Model (Simplified)

Each **session**: `session_id`, `date` (YYYY-MM-DD), `title`, `notes`, **exercises[]**.

Each **exercise**: `exercise_raw`, `exercise_id` (snake_case or `unmapped:<slug>`), `exercise_display`, **sets[]**.

Each **set**: `set_index`, `reps`, `load_type` (`weighted` | `bodyweight` | `bodyweight_plus` | `assisted`). For `weighted`: `weight`, `unit`. For `bodyweight_plus`: `added_load: { value, unit }`. Optional: `rpe`, `set_type`, `notes`.

Unmapped or uncertain data is reported in `issues`, not silently coerced.

---

## Non-Goals (v1)

- No user identity model
- No data persistence
- No background jobs
- No automatic history tracking
- No fuzzy AI exercise mapping (exact alias/display only; otherwise `unmapped:<slug>`)

---

## Development

**Run ingestion on samples (stateless)**

```bash
python scripts/test_ingest.py
python scripts/test_ingest.py path/to/samples
```

**Run metrics on sample-derived sessions**

```bash
python scripts/test_metrics.py
```

**Run tests**

```bash
pytest
```

---

## License

MIT

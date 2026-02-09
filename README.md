# RepStack

RepStack is an MCP-compliant capability server that normalizes strength training data into a deterministic, portable schema.

It is designed for AI-native systems that treat LLMs as probabilistic input layers, not sources of truth.

RepStack ingests messy workout logs (CSV, JSON, or freeform text) and produces validated, canonical session data suitable for analytics, visualization, or downstream orchestration.

---

## Why RepStack Exists

Workout data in the real world is inconsistent:

- Different apps export different formats
- Exercise names vary wildly
- Bodyweight movements are encoded differently (e.g. "Bodyweight", "+25 lb")
- Dates are sometimes missing
- Units are mixed (lb/kg)
- Freeform text logs are common

RepStack provides a normalization layer that:

- Enforces a canonical schema
- Preserves semantic meaning (e.g., bodyweight vs weighted vs bodyweight_plus)
- Avoids silent data corruption—unmapped exercises stay unmapped and are reported
- Emits structured issues instead of guessing
- Produces deterministic outputs across model variations

---

## Architecture Philosophy

RepStack follows three core principles:

1. **LLMs assist parsing but do not define correctness.** When text parsing is used, output is still validated and normalized deterministically.
2. **Canonical validation is schema-enforced.** Pydantic models and explicit load types (weighted, bodyweight, bodyweight_plus, assisted) prevent invalid states.
3. **Imperfect data is reported explicitly, never silently coerced.** Missing dates, unmapped exercises, and dropped sets surface as issues with locations and excerpts.

This makes RepStack suitable for AI workflows, analytics engines, and multi-agent systems.

---

## Features (v1)

- **Ingestion**
  - CSV (Strong / Hevy-style exports supported)
  - JSON with flexible schema detection
  - Freeform text with optional LLM assist
- **Canonical model**
  - Session grouping by date
  - Structured load modeling: `weighted`, `bodyweight`, `bodyweight_plus`, `assisted`
  - Bodyweight sets use `weight: null` and `added_load` for added weight; no fake "0 lb"
- **Quality and storage**
  - Deterministic confidence scoring from issues (missing date, unmapped exercise, etc.)
  - Explicit issue reporting (type, location, raw_excerpt)
  - Conservative exercise mapping: exact synonyms only; uncertain names become `unmapped:<slug>`
  - Canonical SHA256 hashing for deduplication
  - SQLite-backed persistence (configurable via `REPSTACK_DB_PATH`)
- **Stateless metrics**
  - `repstack.compute_metrics` takes canonical data in the request (`sessions` or `logs`), not user id or storage. Same input always yields the same output. PR and volume-spike flags are derived only from the provided data.
  - Guardrails: payloads over `max_sessions` or `max_sets` return `needs_clarification` with a `payload_too_large` issue.
- **Exercise search**
  - `repstack.search_exercises` queries the local registry by name/alias with optional equipment and movement-pattern filters.

---

## MCP Surface

**Tools**

- `repstack.ingest_log` — Ingest a workout log (text, CSV, or JSON). Returns canonical log, issues, summary, and signature. With text, set `allow_llm: true` to use an LLM parser if configured.
- `repstack.compute_metrics` — **Stateless** deterministic metrics from canonical data you provide. Send either `sessions` (array of canonical session objects) or `logs` (array of `{ canonical_json: { sessions } }`). Optional `range` (start/end dates) filters sessions; if omitted, all provided sessions are used. Returns weekly volume, tonnage (lb/kg), hard sets, e1rm, PRs, and flags (e.g. volume spike). Bodyweight sets are excluded from tonnage; excluded/unknown counts are reported. No storage or user identity: metrics are computed only from the payload. Payloads exceeding `max_sessions` or `max_sets` return `status: "needs_clarification"` with a `payload_too_large` issue.
- `repstack.search_exercises` — Search the local exercise registry by query (matches display names and aliases). Returns results with match metadata (strategy, score, normalized_query) and optional filters: `equipment`, `movement_pattern`, `limit`.

**Resources**

- `log://{log_id}/canonical` — Canonical JSON for a log
- `log://{log_id}/issues` — Issues for a log
- `user://{user_id}/recent_summary` — Last 30 days metrics summary for a user (server fetches logs from storage, then runs stateless metrics on that data)

---

## Example Use Cases

RepStack can be connected to:

- AI assistants analyzing training history
- Analytics dashboards
- Health tracking tools
- Program generation systems
- Multi-agent orchestration pipelines

It is transport-agnostic and implemented as an MCP stdio server.

---

## Canonical Data Model (Simplified)

Each **session** contains:

- `session_id`, `date` (YYYY-MM-DD), `title`, `notes`
- **exercises[]**
  - `exercise_raw`, `exercise_id` (snake_case or `unmapped:<slug>`), `exercise_display`
  - **sets[]**
    - `set_index`, `reps`, `load_type` (`weighted` | `bodyweight` | `bodyweight_plus` | `assisted`)
    - For `weighted`: `weight`, `unit`
    - For `bodyweight_plus`: `added_load: { value, unit }` (no `weight`)
    - Optional: `rpe`, `set_type` (warmup | working | top | backoff), `notes`

RepStack does not fabricate missing data. Uncertain or unmapped values are explicitly reported in `issues` with type (e.g. `unmapped_exercise`, `missing_date`) and location.

---

## Installation

From the project root:

```bash
pip install -r requirements.txt
```

Or install in editable mode with dev deps:

```bash
pip install -e ".[dev]"
```

**Run the MCP server**

```bash
python -m repstack.server
```

Or, if installed via pip:

```bash
repstack
```

Optional: set `REPSTACK_DB_PATH` to a path for the SQLite database (default: `repstack.db` in the project root).

---

## Development

**Run ingestion on sample files**

From the project root:

```bash
python scripts/test_ingest.py
```

Optional: pass a different samples directory:

```bash
python scripts/test_ingest.py path/to/samples
```

Uses `repstack_test.db` in the project root (see `scripts/README.md` for more).

**Run metrics over sample data**

```bash
python scripts/test_metrics.py
```

**Run tests**

```bash
pytest
```

---

## Roadmap

- Expanded exercise registry (exact synonyms; no fuzzy matching without guardrails)
- Metrics coverage reporting
- HTTP transport option
- Alias resolution / taxonomy tool
- Exercise taxonomy support

---

## License

MIT

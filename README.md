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

For **text** logs you can use the LLM to pre-parse: set `content_type: "text"` and `options.allow_llm: true`. The server will use an LLM if one is configured (see [Configuring the LLM](#configuring-the-llm)); otherwise it adds a warning and falls back to the deterministic parser. The response includes `meta.llm_available` and `meta.llm_used`.

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

Example payload for **text + LLM**:

```json
{
  "user": { "default_unit": "lb", "timezone": "UTC" },
  "log_input": {
    "content_type": "text",
    "content": "Bench 135x5 145x4, Squat 225x5x3, RDL 135x8"
  },
  "options": { "session_date_hint": "2025-01-15", "allow_llm": true }
}
```

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

## Configuring the LLM

The LLM is **server-side** and **provider-agnostic**: you choose which provider to use via env or by registering a parser. The tool payload cannot pass an API key or provider.

**Option 1: Env — swappable provider**

Set **`REPSTACK_LLM_PROVIDER`** to the name of a registered provider (e.g. `openai`). The server will call that provider’s loader when the ingest tool first needs a parser.

**Built-in provider: `openai`**

- `REPSTACK_LLM_PROVIDER=openai` (or leave unset and set only the key below; it defaults to openai)
- `REPSTACK_OPENAI_API_KEY` — your API key
- `REPSTACK_OPENAI_MODEL` — optional; default `gpt-4o-mini`

Requires the `openai` package: `pip install openai` or `pip install repstack[llm]`.

**Adding another provider (e.g. Anthropic, local model)**

Register a loader that reads its own env and returns a parser (or `None`):

```python
from repstack.llm_parser import register_llm_provider, parse_llm_workout_json, WORKOUT_EXTRACTION_SYSTEM

def load_anthropic_parser():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    # ... create client, then return a function (content, session_date_hint) -> raw_sessions
    # that calls your API and returns parse_llm_workout_json(response_text)
    return my_anthropic_parser_fn

register_llm_provider("anthropic", load_anthropic_parser)
```

Then set `REPSTACK_LLM_PROVIDER=anthropic` (and the provider’s env vars). The shared contract is the JSON shape and `parse_llm_workout_json()` / `WORKOUT_EXTRACTION_SYSTEM` in `repstack.llm_parser`.

**Option 2: Embedding — `set_llm_parser(fn)`**

If you run RepStack inside your own app, you can set the parser directly (overrides env):

```python
from repstack.llm_parser import set_llm_parser

set_llm_parser(my_parser_fn)  # (content: str, session_date_hint: str | None) -> raw_sessions
```

Parser signature: return `list[tuple[str | None, list[tuple[str, list[dict]]]]]` — each tuple is `(date or None, [(exercise_name, [set_dict, ...]), ...])`; each `set_dict` has at least `weight`, `reps`, `unit`, and optionally `load_type`, `added_weight`.

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

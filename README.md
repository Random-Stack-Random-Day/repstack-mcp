# Repstack MCP

MCP server that exposes **log ingestion** and **metrics** over canonical workout logs. Usable by Cursor, Claude Desktop, and other MCP clients via stdio. Published as **repstack-mcp** (you can add repstack-core, repstack-cli, etc. under the same Repstack umbrella).

## Tools

1. **`repstack.ingest_log`** — Accepts workout logs as text, CSV, or JSON. Uses deterministic parsing for CSV/JSON; optional LLM-assisted parsing for messy text when `allow_llm=true`. Returns canonical structured JSON, issues, and a summary (sessions/exercises/sets detected, confidence, `canonical_sha256`).

2. **`repstack.compute_metrics`** — Deterministic analytics over stored logs: weekly volume, tonnage, e1RM, PRs, and flags (e.g. `volume_spike` when hard_sets or tonnage increases >25% week-over-week).

## Resources (read-only)

- `log://{log_id}/canonical` — Canonical JSON for a log.
- `log://{log_id}/issues` — Issues for a log.
- `user://{user_id}/recent_summary` — Last 30 days metrics summary for a user.

## How to run

### Install

```bash
pip install -e .
# or
pip install repstack-mcp
```

### Run the server (stdio)

Clients spawn the server as a subprocess and communicate over stdin/stdout.

**Option A — module:**

```bash
python -m repstack_mcp
```

**Option B — script (if installed):**

```bash
repstack-mcp
```

**Option C — FastMCP CLI:**

```bash
fastmcp run repstack_mcp/server.py
```

### Configure in a client (stdio)

Example **Cursor** config (e.g. in `.cursor/mcp.json` or Cursor settings):

```json
{
  "mcpServers": {
    "repstack-mcp": {
      "command": "python",
      "args": ["-m", "repstack_mcp"],
      "cwd": "C:/path/to/repstack"
    }
  }
}
```

Or with a virtualenv:

```json
{
  "mcpServers": {
    "repstack-mcp": {
      "command": "C:/path/to/repstack/.venv/Scripts/python.exe",
      "args": ["-m", "repstack_mcp"],
      "cwd": "C:/path/to/repstack"
    }
  }
}
```

**Claude Desktop** — in the config file (e.g. `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "repstack-mcp": {
      "command": "python",
      "args": ["-m", "repstack_mcp"],
      "cwd": "C:/path/to/repstack"
    }
  }
}
```

Replace `C:/path/to/repstack` with the actual path to this project.

## Environment

- **`REPSTACK_MCP_DB_PATH`** — Path to SQLite DB (default: `repstack.db` in the project root).
- **LLM parsing** — For text logs with `allow_llm=true`, an LLM parser can be registered via `repstack_mcp.llm_parser.set_llm_parser()`. If none is configured, the tool returns a clear blocking issue and does not call an LLM.

## Tool contracts

Input/output shapes follow the spec in `SPEC.md`. Both tools accept a single JSON object and return a JSON object (e.g. `IngestLogOutput` and `ComputeMetricsOutput`).

## Tests

```bash
pytest tests/ -v
```

See `tests/` for:

- Deterministic CSV parse
- Text parse with mocked LLM output
- Metrics (tonnage + `volume_spike` flag)

📘 Fitness MCP Ingestion Improvement Spec (v1.1)

You are improving the fitness.ingest_log tool to behave like a vendor-grade MCP capability layer.

The goal is to make ingestion:

Predictable

Deterministic

Honest about uncertainty

Safe for downstream analytics

Stable across LLM models

Do NOT add new features. Improve behavior only.

1️⃣ Status Rules (Critical)

The tool must return one of:

"ok" → log stored and usable

"needs_clarification" → not stored, or incomplete for analytics

"error" → invalid tool call

Status Decision Rules

Return "needs_clarification" if:

Any blocking issue exists

Canonical log has zero valid exercises

Canonical log has zero valid sets

Confidence < 0.70

Required canonical fields missing

Return "ok" only if:

No blocking issues

≥ 1 valid exercise

≥ 1 valid set

Confidence ≥ 0.70

Never store logs when status is "needs_clarification".

2️⃣ Confidence Scoring (Deterministic)

Start confidence at 1.0.

Apply penalties:

Missing date: -0.15

Invalid exercise name dropped: -0.20 per occurrence

Incomplete set dropped: -0.10 per occurrence

Ambiguous parsing: -0.15 per issue

Unmapped exercise (fallback slug): -0.10 per occurrence

Minimum confidence floor: 0.25

Cap at 1.0.

Confidence must reflect data quality realistically. Messy logs should not remain ≥ 0.85.

3️⃣ Date Handling Policy

If session date is missing:

If session_date_hint is provided:

Use it

Add warning: "missing_date_autofilled"

If not provided:

Add warning: "missing_date"

Set date = null

BUT:

If strictness == "strict":

Treat missing_date as blocking

In metrics computation:

Exclude sessions with null date by default

Never use artificial default dates (e.g., 1970-01-01).

4️⃣ Issue Structure Standardization

All issues must include:

{
  "severity": "warning|blocking",
  "type": "machine_readable_string",
  "location": "canonical.path.or.raw_line",
  "message": "Human readable explanation.",
  "raw_excerpt": "optional raw text fragment"
}


Never emit vague issue types like "parse_error" without specificity.

Preferred issue types:

parse_error

missing_date

missing_date_autofilled

invalid_exercise_name

incomplete_set

ambiguous_set_format

missing_weight

missing_reps

unsupported_unit

5️⃣ Canonical Integrity Rules

Never fabricate data.

If exercise name cannot be confidently determined:

Drop the exercise

Emit invalid_exercise_name warning

If set lacks reps or weight:

Drop the set

Emit incomplete_set warning

If no valid exercises remain:

Return needs_clarification

Do not store

6️⃣ Storage Gate

Only generate log_id and persist to database when:

Status == "ok"

Canonical schema passes validation

Confidence >= 0.70

If not stored:

log_id must be null

canonical_log may be returned for preview but not persisted

7️⃣ Dev Harness Output Improvement

Console output should:

Display confidence

Display status clearly

Display number of warnings

Indicate whether log was persisted

Example:

Status: ok
Stored: yes
Confidence: 0.78
Warnings: 2

8️⃣ Messy Input Expected Behavior

For very messy input like:

Maybe
135
Squat 225x5


Expected behavior:

Drop invalid lines

Parse Squat 225x5

Confidence < 0.70

Status: needs_clarification

Log not stored

9️⃣ No Behavior Drift

Do NOT:

Add new fields to canonical schema

Change tool input structure

Introduce randomness

Use LLM to determine confidence

Confidence must be deterministic.

10️⃣ After Changes

Add or update tests:

messy_workout should return needs_clarification

missing_date with strictness=strict should block

confidence penalties applied correctly

logs not stored when status != ok

End of spec.
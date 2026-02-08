"""Pydantic models for RepStack: tool inputs/outputs and canonical log schema."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# --- User & log input (ingest_log) ---

class UserInput(BaseModel):
    user_id: Optional[str] = None
    default_unit: Literal["lb", "kg"] = "lb"
    timezone: str = "UTC"


class LogInputSource(BaseModel):
    app: Optional[str] = None
    filename: Optional[str] = None


class LogInput(BaseModel):
    content_type: Literal["text", "csv", "json"]
    content: str
    source: Optional[LogInputSource] = None


class IngestOptions(BaseModel):
    session_date_hint: Optional[str] = None  # YYYY-MM-DD
    allow_llm: bool = False
    strictness: Literal["normal", "strict"] = "normal"
    dedupe_strategy: Literal["none", "by_hash", "by_date_exercise"] = "none"


class IngestLogInput(BaseModel):
    user: UserInput
    log_input: LogInput
    options: Optional[IngestOptions] = None


# --- Canonical log schema ---


class AddedLoad(BaseModel):
    """Single object for added load on bodyweight_plus (replaces added_weight + added_weight_unit)."""
    value: float
    unit: Literal["lb", "kg"]


class SetRecord(BaseModel):
    set_index: int
    weight: Optional[float] = None  # null for bodyweight / bodyweight_plus
    unit: Optional[Literal["lb", "kg"]] = None  # required when weight is set
    reps: int
    load_type: Literal["weighted", "bodyweight", "bodyweight_plus", "assisted"] = "weighted"
    added_load: Optional[AddedLoad] = None  # for bodyweight_plus only
    rpe: Optional[float] = None
    set_type: Optional[Literal["warmup", "working", "top", "backoff"]] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_load_type_fields(self) -> "SetRecord":
        if self.load_type == "weighted":
            if self.weight is None:
                raise ValueError("weight required when load_type is weighted")
            if self.unit is None:
                raise ValueError("unit required when load_type is weighted")
        elif self.load_type == "bodyweight":
            if self.weight is not None:
                raise ValueError("weight must be null when load_type is bodyweight")
        elif self.load_type == "bodyweight_plus":
            if self.weight is not None:
                raise ValueError("weight must be null when load_type is bodyweight_plus")
            if self.added_load is None:
                raise ValueError("added_load required when load_type is bodyweight_plus")
        elif self.load_type == "assisted":
            if self.weight is not None:
                raise ValueError("weight must be null when load_type is assisted")
        return self


class ExerciseMapping(BaseModel):
    """How this exercise was resolved; included in canonical output."""
    strategy: Literal["source_pack", "global_alias", "registry_display", "registry_alias", "unmapped"]
    score: float  # 0.0–1.0; unmapped=0


class ExerciseRecord(BaseModel):
    exercise_raw: str
    exercise_id: str  # snake_case or "unmapped:<slug>"
    exercise_display: str
    mapping: Optional[ExerciseMapping] = None  # v2: how the name was resolved
    sets: list[SetRecord] = Field(default_factory=list)


class SessionRecord(BaseModel):
    session_id: str
    date: Optional[str] = None  # YYYY-MM-DD when provided; null if not
    title: Optional[str] = None
    notes: Optional[str] = None
    exercises: list[ExerciseRecord] = Field(default_factory=list)


class CanonicalLog(BaseModel):
    sessions: list[SessionRecord] = Field(default_factory=list)


# --- Ingest output ---

class IssueRecord(BaseModel):
    severity: Literal["warning", "blocking"]
    type: str
    location: str
    message: str
    raw_excerpt: Optional[str] = None
    question_to_user: Optional[str] = None
    options: Optional[list[str]] = None
    suggested_exercise_ids: Optional[list[str]] = None  # for unmapped_exercise: up to 3 close matches


class IngestSummary(BaseModel):
    sessions_detected: int = 0
    exercises_detected: int = 0
    sets_detected: int = 0
    unmapped_exercises: int = 0
    confidence: float = 0.0


class IngestSignature(BaseModel):
    canonical_sha256: str
    parser_version: str


# --- Search exercises (registry) ---

class SearchExercisesInput(BaseModel):
    query: str
    equipment: Optional[str] = None
    movement_pattern: Optional[str] = None
    limit: Optional[int] = 20


MatchStrategy = Literal["display_exact", "alias_exact", "starts_with", "contains"]


class SearchMatchMetadata(BaseModel):
    strategy: MatchStrategy
    score: float
    matched_text: str
    normalized_query: str


class SearchExerciseHit(BaseModel):
    exercise_id: str
    display: Optional[str] = None
    aliases: Optional[list[str]] = None
    equipment: list[str] = Field(default_factory=list)  # v2: array of strings
    movement_pattern: Optional[str] = None
    match: SearchMatchMetadata
    is_exact_match: bool


class SearchExercisesOutput(BaseModel):
    query: str
    count: int
    results: list[SearchExerciseHit] = Field(default_factory=list)


class IngestLogOutput(BaseModel):
    status: Literal["ok", "needs_clarification", "error"]
    user_id: str
    log_id: Optional[str] = None  # null when not stored
    canonical_log: CanonicalLog
    issues: list[IssueRecord] = Field(default_factory=list)
    summary: IngestSummary
    signature: IngestSignature


# --- Compute metrics input ---

class DateRange(BaseModel):
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD


class ComputeMetricsOptions(BaseModel):
    group_by: Optional[list[str]] = None
    e1rm_formula: Literal["epley", "brzycki"] = "epley"
    volume_metric: Literal["tonnage", "hard_sets"] = "tonnage"
    include_prs: bool = True


class ComputeMetricsInput(BaseModel):
    user_id: str
    range: DateRange
    options: Optional[ComputeMetricsOptions] = None


# --- Compute metrics output ---

class TopSetRecord(BaseModel):
    exercise_id: str
    weight: float
    unit: str
    reps: int
    e1rm: Optional[float] = None
    date: str


class PRRecord(BaseModel):
    exercise_id: str
    kind: str  # e.g. "rep_pr", "e1rm_pr"
    weight: float
    unit: str
    reps: int
    e1rm: Optional[float] = None
    date: str


class WeeklyMetrics(BaseModel):
    week_start: str  # YYYY-MM-DD
    sessions: int = 0
    hard_sets: int = 0
    tonnage_lb: Optional[float] = None
    tonnage_kg: Optional[float] = None
    tonnage_excluded_sets: int = 0  # bodyweight sets excluded from tonnage
    tonnage_unknown_sets: int = 0  # sets with no usable weight for tonnage
    muscle_group_sets: Optional[dict[str, int]] = None
    top_sets: list[TopSetRecord] = Field(default_factory=list)
    prs: list[PRRecord] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ExerciseSummary(BaseModel):
    exercise_id: str
    sessions: int = 0
    best_e1rm: Optional[float] = None
    total_hard_sets: int = 0
    rep_ranges: Optional[dict[str, int]] = None  # bucketed


class MetricsSignature(BaseModel):
    metrics_version: str


class ComputeMetricsOutput(BaseModel):
    status: Literal["ok", "error"]
    user_id: str
    range: DateRange
    weekly: list[WeeklyMetrics] = Field(default_factory=list)
    exercise_summaries: list[ExerciseSummary] = Field(default_factory=list)
    signature: MetricsSignature

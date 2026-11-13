"""Pydantic response models (the API contract the frontend relies on)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .schemas_ops import CellMeasureBrief

SCENARIO_LABEL = "Scenario simulation, not a live forecast"
AUDIT_BANNER = "Elevated risk, recommend a site audit"
FOOTER = "WAYMARK is decision support only. Human experts decide. Indicative result: one city, one backtest."


class Health(BaseModel):
    status: str
    database: bool
    cells: int = 0
    model_loaded: Optional[bool] = None
    model_version: Optional[str] = None
    schema_version: Optional[int] = None
    feature_rows: Optional[int] = None
    active_incidents: Optional[int] = None
    condition_lift_enabled: Optional[bool] = None
    condition_lift_version: Optional[str] = None


class ModelHealth(BaseModel):
    model_loaded: bool
    model_version: Optional[str] = None
    schema_version: Optional[int] = None
    feature_rows: Optional[int] = None
    active_incidents: Optional[int] = None
    condition_lift_enabled: bool = False
    condition_lift_version: Optional[str] = None


class LiveContext(BaseModel):
    status: Literal["ok", "stale", "unavailable"]
    source: str
    scope: str
    observed_at: Optional[str] = None
    fetched_at: Optional[str] = None
    age_seconds: Optional[float] = None
    active_conditions: list[Literal["rain", "night", "rush"]] = Field(default_factory=list)
    raw_readings: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    caveat: str


class PredictionAdjustment(BaseModel):
    kind: str
    multiplier: Optional[float] = None
    points: Optional[float] = None
    reason: str
    source: str
    observed_at: Optional[str] = None


class CellLite(BaseModel):
    cell_id: str
    locality: Optional[str] = None
    street_name: Optional[str] = None
    incident_pending_count: int = 0
    incident_confirmed_count: int = 0
    lat: float
    lng: float
    n_past_crashes: int
    risk_score: Optional[float]
    risk_vs_similar_history: Optional[float]
    confidence: Optional[str]
    emerging_risk: bool
    top_overall: bool          # in the top 300 cells by risk score (the "blue" markers)
    # Additive (operations build): overlay estimate. Equal to risk_score until a measure is verified.
    adjusted_risk_score: Optional[float] = None
    # Stage-weighted implementation projection; does not replace confirmed adjusted risk.
    projected_risk_score: Optional[float] = None
    has_measures: bool = False
    measures: list[CellMeasureBrief] = []


class CellList(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[CellLite]


class ShapItem(BaseModel):
    feature: str
    label: str
    feature_value: Optional[float]
    shap_value: float
    direction: str             # "raises" or "lowers"
    rank: int


class ScenarioItem(BaseModel):
    night: bool
    rain: bool
    low_vis: bool
    probability: float


class PredictionOverrides(BaseModel):
    weather_condition: Optional[Literal["rain", "clear"]] = None
    lighting: Optional[Literal["day", "night"]] = None
    low_visibility: Optional[bool] = None


class PredictRequest(BaseModel):
    cell_id: Optional[str] = Field(default=None, min_length=1, max_length=32)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    overrides: PredictionOverrides = Field(default_factory=PredictionOverrides)
    use_live_context: bool = False


class BatchPredictRequest(BaseModel):
    cell_ids: list[str] = Field(min_length=1, max_length=10000)
    overrides: PredictionOverrides = Field(default_factory=PredictionOverrides)
    use_live_context: bool = False


class PredictionResponse(BaseModel):
    cell_id: str
    model_version: Optional[str] = None
    scored_at: Optional[str] = None
    context_source: Optional[Literal["baseline", "scenario", "live"]] = None
    stale_after_seconds: Optional[int] = None
    risk_probability: float
    risk_score: float
    live_risk_score: float
    confidence: str
    active_incident_count: int
    incident_overlay_points: float
    overlay_params: dict[str, object] = Field(default_factory=dict)
    adjustments: list[PredictionAdjustment] = Field(default_factory=list)
    condition_multiplier: float = 1.0
    active_conditions: list[Literal["rain", "night", "rush"]] = Field(default_factory=list)
    context: Optional[LiveContext] = None
    shap_contributors: list[ShapItem] = Field(default_factory=list)


class BatchPredictionItem(BaseModel):
    cell_id: str
    risk_probability: float
    risk_score: float
    live_risk_score: float
    confidence: str
    active_incident_count: int
    incident_overlay_points: float
    condition_multiplier: float = 1.0
    shap_contributors: list[ShapItem] = Field(default_factory=list)


class BatchPredictionResponse(BaseModel):
    count: int
    items: list[BatchPredictionItem]
    model_version: Optional[str] = None
    schema_version: Optional[int] = None
    scored_at: Optional[str] = None
    context_source: Optional[Literal["baseline", "scenario", "live"]] = None
    stale_after_seconds: Optional[int] = None
    overlay_params: dict[str, object] = Field(default_factory=dict)
    active_conditions: list[Literal["rain", "night", "rush"]] = Field(default_factory=list)
    condition_lift_version: Optional[str] = None
    context: Optional[LiveContext] = None


class CellDetail(BaseModel):
    cell_id: str
    locality: Optional[str] = None
    street_name: Optional[str] = None
    incident_pending_count: int = 0
    incident_confirmed_count: int = 0
    lat: float
    lng: float
    n_past_crashes: int
    risk_score: Optional[float]
    risk_vs_similar_history: Optional[float]
    history_percentile: Optional[float]
    headline: str
    confidence: Optional[str]
    confidence_explanation: str
    emerging_risk: bool
    top_overall: bool
    recommend_audit: bool
    audit_banner: Optional[str]
    baseline_prob: Optional[float]
    top_factors: list[str]
    suggested_actions: list[str]
    shap: list[ShapItem]
    shap_available: bool
    scenarios: list[ScenarioItem]
    google_maps_url: str
    # Additive (operations build)
    adjusted_risk_score: Optional[float] = None
    projected_risk_score: Optional[float] = None
    has_measures: bool = False
    measures: list[CellMeasureBrief] = []


class WhatIfItem(BaseModel):
    cell_id: str
    baseline: float
    scenario: float
    change: float
    emerging_risk: bool


class WhatIfResponse(BaseModel):
    label: str
    night: bool
    rain: bool
    low_vis: bool
    n_cells: int
    n_emerging: int
    mean_change_all: Optional[float]
    mean_change_emerging: Optional[float]
    share_cells_higher: Optional[float]
    items: list[WhatIfItem]


class QualityCheck(BaseModel):
    check_name: str
    status: str
    detail: Optional[str]


class YearTotal(BaseModel):
    year: int
    crashes: int
    months_covered: int
    partial: bool
    ratio_vs_previous: Optional[float]
    jump: bool


class MonthCount(BaseModel):
    year: int
    month: int
    crashes: int


class ValidationResponse(BaseModel):
    counts: dict[str, int]
    checks: list[QualityCheck]
    yearly_totals: list[YearTotal]
    monthly_counts: list[MonthCount]


class MetricPair(BaseModel):
    model: Optional[float]
    history: Optional[float]
    history_fair: Optional[float] = None   # history baseline with ties broken at random (use this for charts)


class MetricGroup(BaseModel):
    group_name: str
    metrics: dict[str, MetricPair]
    extra: dict[str, Any] = {}


class MetricsResponse(BaseModel):
    overall: Optional[MetricGroup]
    low_history: Optional[MetricGroup]
    robustness: list[MetricGroup]
    second_split: list[MetricGroup]
    bootstrap: Optional[dict[str, Any]]
    caveat: str


class Headline(BaseModel):
    low_history_capture_top10_model: Optional[float]
    low_history_capture_top10_history: Optional[float]
    low_history_blackspots: Optional[int]
    overall_auc_model: Optional[float]
    overall_auc_history: Optional[float]
    scoring_basis: Optional[str]


class Summary(BaseModel):
    cells_scored: int
    low_history_cells: int
    emerging_risk_cells: int
    headline: Headline
    footer: str = FOOTER
    # Additive (operations build): lets the public map label the adjusted-risk colour mode honestly.
    placeholder_weights: bool = True
    credit_cap: Optional[float] = None


class AuditItem(BaseModel):
    cell_id: str
    region: str
    locality: Optional[str] = None
    lat: float
    lng: float
    n_past_crashes: int
    risk_vs_similar_history: Optional[float]
    risk_score: Optional[float]
    confidence: Optional[str]
    top_factors: list[str]
    suggested_actions: list[str]
    google_maps_url: str


class AuditResponse(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[AuditItem]


class CrashPoint(BaseModel):
    crash_id: str
    cell_id: str
    lat: float
    lng: float
    start_time: Optional[str]
    severity: Optional[int]
    weather: Optional[str]
    night: bool


class IncidentReportIn(BaseModel):
    kind: Literal["crash", "near_miss"]
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    occurred_at: datetime
    severity: Optional[int] = Field(default=None, ge=1, le=4)
    reasons: Optional[list[str]] = Field(default=None, max_length=8)
    note: Optional[str] = Field(default=None, max_length=1000)


class IncidentReviewIn(BaseModel):
    decision: Literal["confirm", "reject"]
    note: Optional[str] = Field(default=None, max_length=1000)


class IncidentReportOut(BaseModel):
    id: int
    kind: Literal["crash", "near_miss"]
    cell_id: str
    lat: float
    lng: float
    occurred_at: str
    severity: Optional[int]
    reasons: Optional[list[str]]
    note: Optional[str]
    status: Literal["pending", "confirmed", "rejected"]
    review_note: Optional[str] = None
    created_at: str
    live_score_before: Optional[float] = None
    live_score_after: Optional[float] = None


class IncidentReportList(BaseModel):
    count: int
    items: list[IncidentReportOut]


class LiveIncidentResponse(BaseModel):
    incident: IncidentReportOut
    prediction: PredictionResponse


class CrashList(BaseModel):
    available: bool            # False until pipeline/load_crashes.py has been run
    total: int                 # crashes inside the bbox
    count: int                 # crashes returned
    truncated: bool            # True when total > limit
    items: list[CrashPoint]


class PlaceSearchItem(BaseModel):
    name: str
    kind: str
    crash_count: int
    cell_ids: list[str]
    south: float
    west: float
    north: float
    east: float


class PlaceSearchResponse(BaseModel):
    query: str
    items: list[PlaceSearchItem]

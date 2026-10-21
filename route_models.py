from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROUTE_ENGINE_VERSION = "1.0.0"
ROUTE_POLICY_VERSION = "1.0.0"


def _finite_nonnegative(value: float, name: str) -> float:
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_m: float | None = None

    @field_validator("lat", "lon", "elevation_m")
    @classmethod
    def finite_coordinates(cls, value: float | None):
        if value is not None and not isfinite(float(value)):
            raise ValueError("coordinate values must be finite")
        return value


class SecurityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    organization_id: str | None = None
    classification: str = "UNCLASSIFIED"
    compartments: list[str] = Field(default_factory=list)
    sharing_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "principal_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class PreferenceWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: float = 1.0
    energy: float = 1.0
    weather: float = 1.0
    terrain: float = 1.0
    comms: float = 1.0
    uncertainty: float = 1.0

    @field_validator("time", "energy", "weather", "terrain", "comms", "uncertainty")
    @classmethod
    def valid_weight(cls, value: float, info):
        return _finite_nonnegative(value, info.field_name)

    def normalized(self) -> dict[str, float]:
        ordered = {
            "time": self.time,
            "energy": self.energy,
            "weather": self.weather,
            "terrain": self.terrain,
            "comms": self.comms,
            "uncertainty": self.uncertainty,
        }
        total = sum(ordered.values())
        if total <= 0:
            raise ValueError("at least one preference weight must be positive")
        return {key: value / total for key, value in ordered.items()}


class VehicleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["ground", "air", "surface", "simulation"]
    max_range_km: float = Field(gt=0)
    cruise_speed_kph: float = Field(gt=0)
    energy_per_km: float = Field(gt=0)
    max_endurance_minutes: float | None = Field(default=None, gt=0)
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    max_depth_m: float | None = Field(default=None, ge=0)
    route_types: list[str] = Field(default_factory=list)
    weights: PreferenceWeights = Field(default_factory=PreferenceWeights)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_range_km", "cruise_speed_kph", "energy_per_km", "max_endurance_minutes")
    @classmethod
    def finite_positive(cls, value: float | None):
        if value is not None and (not isfinite(float(value)) or float(value) <= 0):
            raise ValueError("vehicle limits must be finite and positive")
        return value

    @model_validator(mode="after")
    def validate_elevation_bounds(self):
        if self.min_elevation_m is not None and self.max_elevation_m is not None:
            if self.min_elevation_m > self.max_elevation_m:
                raise ValueError("min_elevation_m cannot exceed max_elevation_m")
        return self


class PlanningWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    earliest_departure: datetime
    latest_departure: datetime

    @model_validator(mode="after")
    def ordered(self):
        if self.latest_departure < self.earliest_departure:
            raise ValueError("latest_departure must be on or after earliest_departure")
        return self


class HardConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forbidden_geofence_ids: list[str] = Field(default_factory=list)
    required_waypoint_ids: list[str] = Field(default_factory=list)
    allowed_route_types: list[str] = Field(default_factory=list)
    max_distance_km: float | None = Field(default=None, gt=0)
    max_endurance_minutes: float | None = Field(default=None, gt=0)
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    require_available_window: bool = True


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_id: str = Field(min_length=1)
    coordinate: Coordinate


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="allow")
    edge_id: str = Field(min_length=1)
    from_node: str = Field(min_length=1)
    to_node: str = Field(min_length=1)
    distance_km: float = Field(gt=0)
    route_type: str = "generic"
    available: bool = True
    travel_time_minutes: float | None = Field(default=None, gt=0)
    energy_cost: float | None = Field(default=None, ge=0)
    terrain_cost: float = Field(default=0, ge=0)
    weather_cost: float = Field(default=0, ge=0)
    comms_quality: float = Field(default=1, ge=0, le=1)
    geofence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("distance_km", "travel_time_minutes", "energy_cost", "terrain_cost", "weather_cost", "comms_quality")
    @classmethod
    def finite_edge_values(cls, value: float | None):
        if value is not None and not isfinite(float(value)):
            raise ValueError("edge numeric values must be finite")
        return value


class DataProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    layer: str
    source_system: str
    version: str | None = None
    content_hash: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    age_seconds: float | None = Field(default=None, ge=0)
    stale: bool = False


class PlanningDataset(BaseModel):
    model_config = ConfigDict(extra="allow")
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_system: str = Field(min_length=1)
    observed_at: datetime | None = None
    fetched_at: datetime
    graph_provenance: DataProvenance
    optional_layer_provenance: dict[str, DataProvenance] = Field(default_factory=dict)
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    missing_layers: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    uncertainty_penalty: float = Field(default=0.0, ge=0)
    security_context: SecurityContext | None = None


class RouteSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1)
    vehicle_profile_id: str = Field(min_length=1)
    origin: Coordinate
    destination: Coordinate
    waypoints: list[Coordinate] = Field(default_factory=list)
    origin_node_id: str | None = None
    destination_node_id: str | None = None
    waypoint_node_ids: list[str] = Field(default_factory=list)
    planning_window: PlanningWindow
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    soft_preferences: PreferenceWeights = Field(default_factory=PreferenceWeights)
    enabled_layers: list[str] = Field(default_factory=list)
    max_alternatives: int = Field(default=3, ge=1, le=10)
    security_context: SecurityContext
    dataset_id: str | None = None


class RouteCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")
    candidate_id: str
    candidate_index: int = Field(ge=0)
    node_sequence: list[str]
    edge_sequence: list[str]
    geometry: dict[str, Any]
    metrics: dict[str, Any]
    score: float | None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    feasibility: Literal["feasible", "rejected", "degraded"]
    rejection_reasons: list[str] = Field(default_factory=list)
    explanation: str
    data_quality: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    engine_version: str = ROUTE_ENGINE_VERSION
    policy_version: str = ROUTE_POLICY_VERSION
    profile_version: str | None = None


class RouteWindowResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    departure_time: datetime
    candidate_id: str | None = None
    feasibility: str
    score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    data_version: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    freshness: dict[str, Any] = Field(default_factory=dict)
    recommended: bool = False


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_type: Literal["select", "reject", "note"]
    candidate_id: str | None = None
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_rationale(self):
        if self.decision_type in {"select", "reject"} and not self.rationale.strip():
            raise ValueError("rationale is required for select/reject decisions")
        return self


class RouteReplayBundle(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_id: str
    replay_mode: Literal["exact", "approximate"] = "exact"
    normalized_request: dict[str, Any]
    security_context: SecurityContext
    engine_version: str
    policy_version: str
    profile_id: str
    profile_version: str
    dataset_refs: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[RouteCandidate] = Field(default_factory=list)
    windows: list[RouteWindowResult] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

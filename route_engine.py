from __future__ import annotations

import hashlib
import heapq
from typing import Iterable

from route_models import (
    GraphEdge,
    GraphNode,
    PlanningDataset,
    PreferenceWeights,
    RouteCandidate,
    RouteSessionCreate,
    VehicleProfile,
)


class RouteDataUnavailable(RuntimeError):
    pass


def _subsequence(sequence: list[str], required: list[str]) -> bool:
    if not required:
        return True
    pos = 0
    for item in sequence:
        if item == required[pos]:
            pos += 1
            if pos == len(required):
                return True
    return False


def _path_metrics(edges: list[GraphEdge], profile: VehicleProfile) -> dict[str, float]:
    distance = sum(e.distance_km for e in edges)
    eta = sum(e.travel_time_minutes if e.travel_time_minutes is not None else e.distance_km / profile.cruise_speed_kph * 60 for e in edges)
    energy = sum(e.energy_cost if e.energy_cost is not None else e.distance_km * profile.energy_per_km for e in edges)
    count = max(1, len(edges))
    terrain = sum(e.terrain_cost for e in edges) / count
    weather = sum(e.weather_cost for e in edges) / count
    comms_quality = sum(e.comms_quality for e in edges) / count
    return {
        "distance_km": round(distance, 9),
        "eta_minutes": round(eta, 9),
        "energy_estimate": round(energy, 9),
        "terrain_cost": round(terrain, 9),
        "weather_cost": round(weather, 9),
        "comms_quality": round(comms_quality, 9),
    }


def evaluate_hard_constraints(
    request: RouteSessionCreate,
    dataset: PlanningDataset,
    profile: VehicleProfile,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    metrics: dict[str, float],
) -> list[str]:
    reasons: list[str] = []
    hard = request.hard_constraints

    forbidden = set(hard.forbidden_geofence_ids)
    if forbidden and any(forbidden.intersection(e.geofence_ids) for e in edges):
        reasons.append("forbidden_geofence")

    max_distance = profile.max_range_km
    if hard.max_distance_km is not None:
        max_distance = min(max_distance, hard.max_distance_km)
    if metrics["distance_km"] > max_distance:
        reasons.append("range_limit")

    endurance_limits = [v for v in (profile.max_endurance_minutes, hard.max_endurance_minutes) if v is not None]
    if endurance_limits and metrics["eta_minutes"] > min(endurance_limits):
        reasons.append("endurance_limit")

    for edge in edges:
        modes = edge.metadata.get("modes")
        if modes and profile.mode not in modes:
            reasons.append("vehicle_mode_mismatch")
            break

    allowed_types = set(profile.route_types)
    if hard.allowed_route_types:
        allowed_types = allowed_types.intersection(hard.allowed_route_types) if allowed_types else set(hard.allowed_route_types)
    if allowed_types and any(e.route_type not in allowed_types for e in edges):
        reasons.append("route_type_mismatch")

    required = hard.required_waypoint_ids or request.waypoint_node_ids
    if required and not _subsequence([n.node_id for n in nodes], list(required)):
        reasons.append("required_waypoint_order")

    elevations = [n.coordinate.elevation_m for n in nodes if n.coordinate.elevation_m is not None]
    min_elevs = [v for v in (profile.min_elevation_m, hard.min_elevation_m) if v is not None]
    max_elevs = [v for v in (profile.max_elevation_m, hard.max_elevation_m) if v is not None]
    if elevations:
        if min_elevs and min(elevations) < max(min_elevs):
            reasons.append("elevation_limit")
        if max_elevs and max(elevations) > min(max_elevs):
            reasons.append("elevation_limit")

    if hard.require_available_window and any(e.metadata.get("departure_available") is False for e in edges):
        reasons.append("departure_window_unavailable")

    return list(dict.fromkeys(reasons))


def _score_breakdown(metrics: dict[str, float], dataset: PlanningDataset, profile: VehicleProfile, weights: PreferenceWeights) -> dict[str, float]:
    w = weights.normalized()
    endurance_basis = profile.max_endurance_minutes or max(60.0, metrics["eta_minutes"])
    energy_basis = max(profile.max_range_km * profile.energy_per_km, 1e-9)
    costs = {
        "time": min(1.0, metrics["eta_minutes"] / endurance_basis),
        "energy": min(1.0, metrics["energy_estimate"] / energy_basis),
        "weather": min(1.0, max(0.0, metrics["weather_cost"])),
        "terrain": min(1.0, max(0.0, metrics["terrain_cost"])),
        "comms": min(1.0, max(0.0, 1.0 - metrics["comms_quality"])),
        "uncertainty": min(1.0, max(0.0, dataset.uncertainty_penalty)),
    }
    return {key: round(w[key] * costs[key], 12) for key in w}


def explain_candidate(candidate: RouteCandidate) -> str:
    if candidate.rejection_reasons:
        return "Rejected by hard constraints: " + ", ".join(candidate.rejection_reasons) + "."
    if not candidate.score_breakdown:
        return "Feasible route with no weighted score components."
    ordered = sorted(candidate.score_breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
    strongest = ordered[0][0]
    missing = candidate.data_quality.get("missing_layers") or []
    suffix = f" Missing optional layers: {', '.join(missing)}." if missing else ""
    return f"Feasible route; largest weighted cost is {strongest}." + suffix


def score_candidate(
    candidate: RouteCandidate,
    dataset: PlanningDataset,
    profile: VehicleProfile,
    weights: PreferenceWeights,
) -> RouteCandidate:
    breakdown = _score_breakdown(candidate.metrics, dataset, profile, weights)
    candidate.score_breakdown = breakdown
    candidate.score = round(sum(breakdown.values()), 12)
    candidate.explanation = explain_candidate(candidate)
    return candidate


def _enumerate_paths(dataset: PlanningDataset, start: str, goal: str, limit: int) -> list[tuple[list[str], list[str]]]:
    node_ids = {n.node_id for n in dataset.nodes}
    if start not in node_ids or goal not in node_ids:
        raise RouteDataUnavailable("origin_or_destination_not_in_graph")
    adjacency: dict[str, list[GraphEdge]] = {nid: [] for nid in node_ids}
    for edge in dataset.edges:
        if edge.available and edge.from_node in adjacency and edge.to_node in node_ids:
            adjacency[edge.from_node].append(edge)
    for values in adjacency.values():
        values.sort(key=lambda e: (e.edge_id, e.to_node))

    heap: list[tuple[float, tuple[str, ...], tuple[str, ...], str]] = [(0.0, (start,), (), start)]
    found: list[tuple[list[str], list[str]]] = []
    safety = 0
    while heap and len(found) < limit:
        cost, nodes, edges, current = heapq.heappop(heap)
        safety += 1
        if safety > 100000:
            raise RouteDataUnavailable("graph_search_limit_exceeded")
        if current == goal:
            found.append((list(nodes), list(edges)))
            continue
        for edge in adjacency.get(current, []):
            if edge.to_node in nodes:
                continue
            heapq.heappush(
                heap,
                (cost + edge.distance_km, nodes + (edge.to_node,), edges + (edge.edge_id,), edge.to_node),
            )
    return found


def _stable_candidate_id(dataset: PlanningDataset, profile: VehicleProfile, nodes: list[str]) -> str:
    material = f"{dataset.dataset_id}|{dataset.version}|{profile.profile_id}|{profile.version}|{'/'.join(nodes)}"
    return "route-" + hashlib.sha256(material.encode()).hexdigest()[:20]


def generate_candidates(
    request: RouteSessionCreate,
    dataset: PlanningDataset,
    profile: VehicleProfile,
    max_alternatives: int,
) -> list[RouteCandidate]:
    if not dataset.nodes or not dataset.edges:
        raise RouteDataUnavailable("required_route_dataset_unavailable")
    if not request.origin_node_id or not request.destination_node_id:
        raise RouteDataUnavailable("origin_destination_node_ids_required")

    node_map = {n.node_id: n for n in dataset.nodes}
    edge_map = {e.edge_id: e for e in dataset.edges}
    paths = _enumerate_paths(dataset, request.origin_node_id, request.destination_node_id, max(1, max_alternatives))
    if not paths:
        raise RouteDataUnavailable("no_route_available")

    candidates: list[RouteCandidate] = []
    for idx, (node_seq, edge_seq) in enumerate(paths):
        nodes = [node_map[n] for n in node_seq]
        edges = [edge_map[e] for e in edge_seq]
        metrics = _path_metrics(edges, profile)
        rejection = evaluate_hard_constraints(request, dataset, profile, nodes, edges, metrics)
        degraded = bool(dataset.missing_layers or dataset.uncertainty_penalty > 0 or dataset.confidence < 1)
        feasibility = "rejected" if rejection else ("degraded" if degraded else "feasible")
        geometry = {
            "type": "LineString",
            "coordinates": [[n.coordinate.lon, n.coordinate.lat] for n in nodes],
        }
        cand = RouteCandidate(
            candidate_id=_stable_candidate_id(dataset, profile, node_seq),
            candidate_index=idx,
            node_sequence=node_seq,
            edge_sequence=edge_seq,
            geometry=geometry,
            metrics=metrics,
            score=None,
            feasibility=feasibility,
            rejection_reasons=rejection,
            explanation="",
            data_quality={
                "confidence": dataset.confidence,
                "missing_layers": dataset.missing_layers,
                "uncertainty_penalty": dataset.uncertainty_penalty,
            },
            provenance={
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.version,
                "graph_version": dataset.graph_provenance.version,
            },
            profile_version=profile.version,
        )
        score_candidate(cand, dataset, profile, request.soft_preferences)
        if rejection:
            cand.explanation = explain_candidate(cand)
        candidates.append(cand)

    candidates.sort(key=lambda c: (1 if c.feasibility == "rejected" else 0, float("inf") if c.score is None else c.score, tuple(c.node_sequence)))
    for index, cand in enumerate(candidates):
        cand.candidate_index = index
    return candidates

# Observability wrappers keep the deterministic core pure while measuring calls.
_generate_candidates_core = generate_candidates
_score_candidate_core = score_candidate

def score_candidate(candidate, dataset, profile, weights):
    from route_observability import timed_operation
    with timed_operation('route_scoring_latency'):
        return _score_candidate_core(candidate, dataset, profile, weights)

def generate_candidates(request, dataset, profile, max_alternatives):
    from route_observability import metrics, timed_operation
    with timed_operation('route_generation_latency'):
        try:
            result = _generate_candidates_core(request, dataset, profile, max_alternatives)
        except RouteDataUnavailable:
            metrics.increment('route_generation_failure', reason='data_unavailable')
            raise
        metrics.increment('route_candidates_generated', len(result))
        for c in result:
            if c.feasibility == 'rejected':
                metrics.increment('route_candidates_rejected')
                for reason in c.rejection_reasons: metrics.increment('route_constraint_rejection', reason=reason)
        return result

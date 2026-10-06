from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from route_models import (
    Coordinate, DataProvenance, GraphEdge, GraphNode, HardConstraints,
    PlanningDataset, PlanningWindow, PreferenceWeights, RouteSessionCreate,
    SecurityContext, VehicleProfile,
)
from route_engine import RouteDataUnavailable, evaluate_hard_constraints, generate_candidates


def dataset(edges_patch=None, missing_graph=False):
    raw = json.loads(Path('tests/fixtures/route_graph.json').read_text())
    edges = [GraphEdge.model_validate(e) for e in raw['edges']]
    if edges_patch:
        for e in edges:
            edges_patch(e)
    now = datetime.now(timezone.utc)
    return PlanningDataset(
        dataset_id='ds', version='1', source_system='TEST', fetched_at=now,
        graph_provenance=DataProvenance(layer='graph', source_system='TEST', version='1', fetched_at=now),
        nodes=[] if missing_graph else [GraphNode.model_validate(n) for n in raw['nodes']],
        edges=[] if missing_graph else edges,
        security_context=SecurityContext(tenant_id='t', principal_id='p'),
    )


def profile(**overrides):
    data=dict(profile_id='v', version='1', name='Vehicle', mode='ground', max_range_km=100,
              cruise_speed_kph=60, energy_per_km=1, route_types=['road'])
    data.update(overrides)
    return VehicleProfile(**data)


def request(**overrides):
    now=datetime.now(timezone.utc)
    data=dict(
        plan_id='plan', vehicle_profile_id='v', origin=Coordinate(lat=0,lon=0),
        destination=Coordinate(lat=1,lon=1), origin_node_id='A', destination_node_id='D',
        planning_window=PlanningWindow(earliest_departure=now, latest_departure=now+timedelta(hours=1)),
        security_context=SecurityContext(tenant_id='t', principal_id='p'), max_alternatives=3,
        soft_preferences=PreferenceWeights(time=1,energy=1,weather=1,terrain=1,comms=1,uncertainty=1),
    )
    data.update(overrides)
    return RouteSessionCreate(**data)


def test_generates_distinct_deterministic_alternatives():
    first=generate_candidates(request(), dataset(), profile(), 3)
    second=generate_candidates(request(), dataset(), profile(), 3)
    assert len(first) >= 2
    assert [c.node_sequence for c in first] == [c.node_sequence for c in second]
    assert [c.candidate_id for c in first] == [c.candidate_id for c in second]
    assert [c.score_breakdown for c in first] == [c.score_breakdown for c in second]
    assert len({tuple(c.node_sequence) for c in first}) == len(first)


def test_tie_breaking_uses_canonical_node_sequence():
    ds=dataset(lambda e: setattr(e,'weather_cost',0.1))
    cands=generate_candidates(request(soft_preferences=PreferenceWeights(time=1,energy=0,weather=0,terrain=0,comms=0,uncertainty=0)), ds, profile(), 3)
    equal=[c for c in cands if c.metrics['distance_km']==18]
    assert equal
    assert equal == sorted(equal, key=lambda c: tuple(c.node_sequence))


def test_forbidden_geofence_is_hard_failure():
    ds=dataset(lambda e: e.geofence_ids.append('NOPE') if e.edge_id=='AB' else None)
    req=request(hard_constraints=HardConstraints(forbidden_geofence_ids=['NOPE']))
    cands=generate_candidates(req, ds, profile(), 3)
    bad=[c for c in cands if 'AB' in c.edge_sequence][0]
    assert bad.feasibility=='rejected'
    assert any('forbidden_geofence' in r for r in bad.rejection_reasons)


def test_range_and_endurance_are_hard_failures():
    cands=generate_candidates(request(), dataset(), profile(max_range_km=15,max_endurance_minutes=15), 3)
    assert all(c.feasibility=='rejected' for c in cands)
    assert all(any('range' in r or 'endurance' in r for r in c.rejection_reasons) for c in cands)


def test_mode_and_route_type_mismatch_rejected():
    ds=dataset(lambda e: e.metadata.update({'modes':['air']}) if e.edge_id=='AC' else None)
    cands=generate_candidates(request(), ds, profile(mode='ground'), 3)
    bad=[c for c in cands if 'AC' in c.edge_sequence][0]
    assert bad.feasibility=='rejected'
    assert 'vehicle_mode_mismatch' in bad.rejection_reasons


def test_required_waypoint_order_is_enforced():
    req=request(hard_constraints=HardConstraints(required_waypoint_ids=['B']))
    cands=generate_candidates(req, dataset(), profile(), 3)
    for cand in cands:
        if 'B' not in cand.node_sequence:
            assert 'required_waypoint_order' in cand.rejection_reasons


def test_elevation_limit_is_enforced():
    req=request(hard_constraints=HardConstraints(max_elevation_m=18))
    cands=generate_candidates(req, dataset(), profile(), 3)
    assert any('elevation_limit' in c.rejection_reasons for c in cands)


def test_departure_unavailable_edge_is_rejected():
    ds=dataset(lambda e: e.metadata.update({'departure_available':False}) if e.edge_id=='AE' else None)
    cands=generate_candidates(request(), ds, profile(), 3)
    bad=[c for c in cands if 'AE' in c.edge_sequence][0]
    assert 'departure_window_unavailable' in bad.rejection_reasons


def test_blocked_edge_not_used():
    ds=dataset(lambda e: setattr(e,'available',False) if e.edge_id=='AB' else None)
    cands=generate_candidates(request(), ds, profile(), 3)
    assert all('AB' not in c.edge_sequence for c in cands)


def test_missing_required_graph_raises():
    with pytest.raises(RouteDataUnavailable):
        generate_candidates(request(), dataset(missing_graph=True), profile(), 3)


def test_rejected_route_cannot_rank_ahead_of_feasible_route():
    ds=dataset(lambda e: e.geofence_ids.append('NOPE') if e.edge_id=='AC' else None)
    req=request(hard_constraints=HardConstraints(forbidden_geofence_ids=['NOPE']), soft_preferences=PreferenceWeights(time=100,energy=0,weather=0,terrain=0,comms=0,uncertainty=0))
    cands=generate_candidates(req, ds, profile(), 3)
    feasible=[i for i,c in enumerate(cands) if c.feasibility!='rejected']
    rejected=[i for i,c in enumerate(cands) if c.feasibility=='rejected']
    assert feasible and rejected
    assert max(feasible) < min(rejected)

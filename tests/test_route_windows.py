from datetime import datetime, timedelta, timezone
from route_models import DataProvenance, PlanningDataset, RouteCandidate, SecurityContext
from route_windows import evaluate_departure_windows


def candidate(cid, score, feasible='feasible'):
    return RouteCandidate(candidate_id=cid,candidate_index=0,node_sequence=['A','D'],edge_sequence=['AD'],geometry={'type':'LineString','coordinates':[[0,0],[1,1]]},metrics={'distance_km':10,'eta_minutes':12,'energy_estimate':8,'weather_cost':0.2,'terrain_cost':0.1,'comms_quality':0.9},score=score,score_breakdown={'time':score},feasibility=feasible,rejection_reasons=[],explanation='ok')


def dataset():
    now=datetime.now(timezone.utc)
    return PlanningDataset(dataset_id='d',version='v1',source_system='TEST',fetched_at=now,graph_provenance=DataProvenance(layer='graph',source_system='TEST',version='g1',fetched_at=now),nodes=[{'node_id':'A','coordinate':{'lat':0,'lon':0}},{'node_id':'D','coordinate':{'lat':1,'lon':1}}],edges=[{'edge_id':'AD','from_node':'A','to_node':'D','distance_km':10}],confidence=0.9,security_context=SecurityContext(tenant_id='t',principal_id='p'))


def test_departure_windows_cover_horizon_and_choose_deterministic_best():
    start=datetime(2026,10,6,12,0,tzinfo=timezone.utc)
    session={'request':{'planning_window':{'earliest_departure':start.isoformat(),'latest_departure':(start+timedelta(hours=1)).isoformat()}}}
    out=evaluate_departure_windows(session,[candidate('b',0.3),candidate('a',0.3)],dataset(),30)
    assert [x.departure_time for x in out]==[start,start+timedelta(minutes=30),start+timedelta(hours=1)]
    assert all(x.candidate_id=='a' for x in out)
    assert sum(1 for x in out if x.recommended)==1 and out[0].recommended is True
    for x in out:
        assert x.data_version=='v1' and x.metrics['eta_minutes']==12 and x.metrics['energy_estimate']==8
        assert 'weather_margin' in x.metrics and x.confidence==0.9


def test_rejected_candidates_are_not_recommended():
    start=datetime(2026,10,6,12,0,tzinfo=timezone.utc)
    session={'request':{'planning_window':{'earliest_departure':start.isoformat(),'latest_departure':start.isoformat()}}}
    out=evaluate_departure_windows(session,[candidate('x',0.1,'rejected')],dataset(),30)
    assert out[0].feasibility=='unavailable' and out[0].candidate_id is None and not out[0].recommended

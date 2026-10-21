from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from route_models import Coordinate, DataProvenance, HardConstraints, PlanningDataset, PlanningWindow, PreferenceWeights, RouteSessionCreate, SecurityContext, VehicleProfile
from route_engine import RouteDataUnavailable, generate_candidates
from route_dataset_adapter import build_planning_dataset
from route_store import security_context_allows
import route_events
from route_observability import metrics, timed_operation


def profile():
    return VehicleProfile(profile_id='v',version='1',name='V',mode='ground',max_range_km=100,cruise_speed_kph=60,energy_per_km=1,route_types=['road'])


def request(**kw):
    now=datetime.now(timezone.utc)
    d=dict(plan_id='plan',vehicle_profile_id='v',origin=Coordinate(lat=0,lon=0),destination=Coordinate(lat=1,lon=1),origin_node_id='A',destination_node_id='D',planning_window=PlanningWindow(earliest_departure=now,latest_departure=now+timedelta(hours=1)),security_context=SecurityContext(tenant_id='t',principal_id='p'),soft_preferences=PreferenceWeights(),max_alternatives=3)
    d.update(kw); return RouteSessionCreate(**d)


def dataset():
    raw=json.loads(Path('tests/fixtures/route_graph.json').read_text()); now=datetime.now(timezone.utc)
    return PlanningDataset(dataset_id='d',version='1',source_system='TEST',fetched_at=now,graph_provenance=DataProvenance(layer='graph',source_system='TEST',version='1',fetched_at=now),nodes=raw['nodes'],edges=raw['edges'],security_context=SecurityContext(tenant_id='t',principal_id='p'))


def test_end_to_end_engine_acceptance_and_hard_gate():
    c=generate_candidates(request(),dataset(),profile(),3)
    assert len(c)>=2
    for x in c:
        for k in ['distance_km','eta_minutes','energy_estimate','terrain_cost','weather_cost','comms_quality']: assert k in x.metrics
        assert x.score_breakdown and x.explanation
    blocked=generate_candidates(request(hard_constraints=HardConstraints(max_distance_km=15)),dataset(),profile(),3)
    assert all(x.feasibility=='rejected' for x in blocked)


def test_required_graph_failure_and_stale_optional_degradation():
    with pytest.raises(RouteDataUnavailable): generate_candidates(request(),PlanningDataset(dataset_id='d',version='1',source_system='X',fetched_at=datetime.now(timezone.utc),graph_provenance=DataProvenance(layer='graph',source_system='X'),nodes=[],edges=[]),profile(),3)
    old=datetime.now(timezone.utc)-timedelta(days=1)
    graph={'dataset_id':'d','source_system':'MAP','version':'1','observed_at':datetime.now(timezone.utc).isoformat(),'fetched_at':datetime.now(timezone.utc).isoformat(),'nodes':[{'node_id':'A','coordinate':{'lat':0,'lon':0}},{'node_id':'D','coordinate':{'lat':1,'lon':1}}],'edges':[{'edge_id':'AD','from_node':'A','to_node':'D','distance_km':1}]}
    weather={'source_system':'WX','version':'1','observed_at':old.isoformat(),'fetched_at':datetime.now(timezone.utc).isoformat(),'edge_values':{'AD':0.2}}
    ds=build_planning_dataset(graph,{'weather':weather},VehicleProfile(profile_id='v',version='1',name='V',mode='simulation',max_range_km=100,cruise_speed_kph=10,energy_per_km=1,metadata={'optional_layers':['weather'],'layer_stale_after_seconds':3600}))
    assert ds.optional_layer_provenance['weather'].stale and ds.uncertainty_penalty>0 and ds.confidence<1


def test_security_boundary_and_weapon_fields_rejected():
    assert not security_context_allows(SecurityContext(tenant_id='other',principal_id='p',classification='TOP_SECRET',compartments=['A']),SecurityContext(tenant_id='t',principal_id='q',classification='SECRET',compartments=['A']))
    payload=request().model_dump(mode='json'); payload['weapon_target']='x'
    with pytest.raises(ValidationError): RouteSessionCreate.model_validate(payload)


def test_offline_outbox_failure_preserves_local_event(monkeypatch):
    local=[]; queued=[]
    monkeypatch.setattr(route_events,'append_route_event',lambda *a,**k:local.append((a,k)) or {'id':'e1'})
    monkeypatch.setattr(route_events,'enqueue_outbox',lambda **k:queued.append(k) or {'id':'o1','status':'pending'})
    ctx=SecurityContext(tenant_id='t',principal_id='p')
    route_events.queue_route_event('s','APOLLO.ROUTE.DECISION_RECORDED',{'rationale':'human choice'},ctx,'decision-1')
    assert local and queued and local[0][0][-1]=='event:decision-1'
    assert queued[0]['payload']['rationale']=='human choice'


def test_observability_counts_and_timing_without_payloads():
    before=metrics.snapshot()['counters'].get('acceptance_probe',0)
    with timed_operation('acceptance_latency'):
        metrics.increment('acceptance_probe')
    snap=metrics.snapshot()
    assert snap['counters']['acceptance_probe']==before+1
    assert snap['timings']['acceptance_latency']['count']>=1


def test_generated_route_events_are_system_or_human_classified(monkeypatch):
    local=[]; queued=[]
    monkeypatch.setattr(route_events,'append_route_event',lambda *a,**k:local.append((a,k)) or {'id':'e'})
    monkeypatch.setattr(route_events,'enqueue_outbox',lambda **k:queued.append(k) or {'id':'o'})
    ctx=SecurityContext(tenant_id='t',principal_id='p')
    route_events.queue_route_event('s','APOLLO.ROUTE.CANDIDATES_GENERATED',{'candidate_count':2},ctx,'gen-1')
    route_events.queue_route_event('s','APOLLO.ROUTE.DECISION_RECORDED',{'rationale':'keep this'},ctx,'dec-1')
    assert queued[0]['payload']['decision_source']=='system_recommendation'
    assert queued[1]['payload']['decision_source']=='human_decision'


def test_offline_human_decision_survives_later_system_update_and_flush(monkeypatch):
    human={'id':'d1','decision_type':'select','candidate_id':'c2','rationale':'operator chose c2','created_at':'2026-10-06T00:00:00Z'}
    system_event={'event_type':'APOLLO.ROUTE.CANDIDATES_GENERATED','payload':{'top_candidate_id':'c1'},'created_at':'2026-10-06T00:05:00Z'}
    pending=[{'id':'o1','target_system':'UNG-SENTINEL','message_type':'APOLLO.ROUTE.DECISION_RECORDED','payload':{'rationale':human['rationale']}}]
    delivered=[]
    monkeypatch.setattr(route_events,'list_pending_outbox',lambda limit=100:pending)
    monkeypatch.setattr(route_events,'mark_outbox_delivered',lambda oid:delivered.append(oid))
    monkeypatch.setattr(route_events,'mark_outbox_failed',lambda *a,**k:None)
    monkeypatch.setattr(route_events,'_publish',lambda *a,**k:{'accepted':True})
    assert route_events.flush_route_outbox(None)['delivered']==1
    assert delivered==['o1']
    assert human['rationale']=='operator chose c2' and human['created_at']=='2026-10-06T00:00:00Z'
    assert system_event['payload']['top_candidate_id']=='c1' and human['candidate_id']=='c2'

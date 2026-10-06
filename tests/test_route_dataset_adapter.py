from datetime import datetime, timedelta, timezone
import pytest

from route_dataset_adapter import build_planning_dataset
from route_engine import RouteDataUnavailable
from route_models import VehicleProfile


def profile(**metadata):
    return VehicleProfile(profile_id='v1',version='1',name='V',mode='ground',max_range_km=100,cruise_speed_kph=60,energy_per_km=1,route_types=['road'],metadata=metadata)


def graph(now=None):
    now=now or datetime.now(timezone.utc)
    return {
        'dataset_id':'region-1','source_system':'MAP','version':'g1','content_hash':'abc',
        'observed_at':now.isoformat(),'fetched_at':now.isoformat(),'confidence':0.98,
        'security_context':{'tenant_id':'t1','principal_id':'nexus','classification':'SECRET','compartments':['ALPHA']},
        'nodes':[{'node_id':'A','coordinate':{'lat':0,'lon':0}},{'node_id':'D','coordinate':{'lat':1,'lon':1}}],
        'edges':[{'edge_id':'AD','from_node':'A','to_node':'D','distance_km':10,'route_type':'road'}],
    }


def optional(layer, values, observed=None, confidence=0.9):
    now=datetime.now(timezone.utc); observed=observed or now
    return {'layer':layer,'source_system':layer.upper(),'version':layer+'-1','content_hash':layer+'hash',
            'observed_at':observed.isoformat(),'fetched_at':now.isoformat(),'confidence':confidence,'edge_values':values}


def test_missing_graph_raises():
    with pytest.raises(RouteDataUnavailable): build_planning_dataset(None, {}, profile())


def test_normalizes_graph_and_optional_layers_with_provenance():
    opts={
        'weather':optional('weather',{'AD':0.3}),
        'terrain':optional('terrain',{'AD':0.2}),
        'comms':optional('comms',{'AD':0.75}),
        'geofence':optional('geofence',{'AD':['G1']}),
    }
    ds=build_planning_dataset(graph(),opts,profile())
    e=ds.edges[0]
    assert e.weather_cost==0.3 and e.terrain_cost==0.2 and e.comms_quality==0.75 and e.geofence_ids==['G1']
    assert set(ds.optional_layer_provenance)==set(opts)
    assert ds.graph_provenance.content_hash=='abc'
    assert ds.security_context.tenant_id=='t1'
    assert ds.missing_layers==[]


def test_missing_optional_layers_degrade_confidence():
    ds=build_planning_dataset(graph(),{'weather':optional('weather',{'AD':0.1})},profile())
    assert set(ds.missing_layers)=={'terrain','geofence','comms'}
    assert ds.confidence < 1
    assert ds.uncertainty_penalty > 0


def test_stale_layer_records_age_and_penalty():
    old=datetime.now(timezone.utc)-timedelta(hours=10)
    ds=build_planning_dataset(graph(),{'weather':optional('weather',{'AD':0.1},old)},profile(layer_stale_after_seconds=3600, optional_layers=['weather']))
    prov=ds.optional_layer_provenance['weather']
    assert prov.stale is True and prov.age_seconds >= 36000-5
    assert ds.uncertainty_penalty > 0

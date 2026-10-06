import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import route_api


def principal(perms=None, **overrides):
    data = {
        'id': 'p1', 'tenant_id': 't1', 'organization_id': 'org1',
        'classification': 'SECRET', 'compartments': ['ALPHA'],
        'permissions': perms or [],
    }
    data.update(overrides)
    return data


def client(monkeypatch, principal_value=None, auth_error=None):
    os.environ['APOLLO_ROUTE_PLANNING_ENABLED'] = '1'
    app = FastAPI()
    app.include_router(route_api.router)
    if auth_error:
        def fake_auth(permission, authorization): raise auth_error
    else:
        def fake_auth(permission, authorization):
            if not authorization:
                raise HTTPException(401, 'JANUS bearer token required')
            p = principal_value or principal(['ung.admin'])
            if permission not in set(p.get('permissions') or []) and 'ung.admin' not in set(p.get('permissions') or []):
                raise HTTPException(403, f'Missing JANUS permission: {permission}')
            return p
    monkeypatch.setattr(route_api, '_authorize', fake_auth)
    return TestClient(app)


def body():
    now = datetime.now(timezone.utc)
    return {
        'plan_id': '11111111-1111-1111-1111-111111111111',
        'vehicle_profile_id': 'v1',
        'origin': {'lat': 0, 'lon': 0}, 'destination': {'lat': 1, 'lon': 1},
        'origin_node_id': 'A', 'destination_node_id': 'D',
        'planning_window': {'earliest_departure': now.isoformat(), 'latest_departure': (now+timedelta(hours=1)).isoformat()},
        'security_context': {'tenant_id': 't1', 'principal_id': 'p1', 'classification': 'SECRET', 'compartments': ['ALPHA']},
    }


def test_missing_token_401(monkeypatch):
    c=client(monkeypatch, principal(['apollo.routes.write']))
    assert c.post('/v1/routes/sessions', json=body()).status_code == 401


def test_missing_permission_403(monkeypatch):
    c=client(monkeypatch, principal([]))
    assert c.post('/v1/routes/sessions', json=body(), headers={'Authorization':'Bearer x'}).status_code == 403


def test_janus_unavailable_503(monkeypatch):
    c=client(monkeypatch, auth_error=HTTPException(503,'JANUS authorization unavailable'))
    assert c.post('/v1/routes/sessions', json=body(), headers={'Authorization':'Bearer x'}).status_code == 503


def test_valid_session_creation_201(monkeypatch):
    monkeypatch.setattr(route_api, 'plan_exists', lambda plan_id: True)
    monkeypatch.setattr(route_api, 'get_vehicle_profile', lambda *a, **k: {'payload': {'profile_id':'v1','version':'1','name':'V','mode':'ground','max_range_km':100,'cruise_speed_kph':60,'energy_per_km':1}})
    monkeypatch.setattr(route_api, 'create_session', lambda req, created_by, profile_version=None: {'id':'s1','plan_id':req.plan_id,'status':'draft'})
    c=client(monkeypatch, principal(['apollo.routes.write']))
    r=c.post('/v1/routes/sessions',json=body(),headers={'Authorization':'Bearer x'})
    assert r.status_code==201
    assert r.json()['id']=='s1'


def test_unknown_plan_404(monkeypatch):
    monkeypatch.setattr(route_api, 'plan_exists', lambda plan_id: False)
    c=client(monkeypatch, principal(['apollo.routes.write']))
    assert c.post('/v1/routes/sessions',json=body(),headers={'Authorization':'Bearer x'}).status_code==404


def test_security_label_mismatch_denied_even_with_permission(monkeypatch):
    b=body(); b['security_context']['tenant_id']='other'
    c=client(monkeypatch, principal(['apollo.routes.write']))
    assert c.post('/v1/routes/sessions',json=b,headers={'Authorization':'Bearer x'}).status_code==403


def test_generation_missing_dataset_returns_clear_error(monkeypatch):
    monkeypatch.setattr(route_api, 'get_session', lambda *a, **k: {'id':'s1','request': body(), 'vehicle_profile_id':'v1','vehicle_profile_version':'1'})
    monkeypatch.setattr(route_api, 'get_dataset_snapshot', lambda *a, **k: None)
    c=client(monkeypatch, principal(['apollo.routes.generate']))
    r=c.post('/v1/routes/sessions/s1/generate',headers={'Authorization':'Bearer x'})
    assert r.status_code in (409,422)
    assert 'required_route_dataset_unavailable' in r.text


def test_generation_contract(monkeypatch):
    candidate = {
        'candidate_id':'c1','candidate_index':0,'node_sequence':['A','D'],'edge_sequence':['AD'],
        'geometry':{'type':'LineString','coordinates':[[0,0],[1,1]]},
        'metrics':{'distance_km':1,'eta_minutes':1,'energy_estimate':1,'terrain_cost':0,'weather_cost':0,'comms_quality':1},
        'score':0.1,'score_breakdown':{'time':0.1},'feasibility':'feasible','rejection_reasons':[],
        'explanation':'ok','data_quality':{'confidence':1},'provenance':{'dataset_id':'d'},
        'engine_version':'1.0.0','policy_version':'1.0.0','profile_version':'1'
    }
    monkeypatch.setattr(route_api, 'get_session', lambda *a, **k: {'id':'s1','request': body(), 'vehicle_profile_id':'v1','vehicle_profile_version':'1'})
    monkeypatch.setattr(route_api, 'get_dataset_snapshot', lambda *a, **k: {'payload': route_api.synthetic_test_dataset_payload()})
    monkeypatch.setattr(route_api, 'get_vehicle_profile', lambda *a, **k: {'payload': {'profile_id':'v1','version':'1','name':'V','mode':'ground','max_range_km':100,'cruise_speed_kph':60,'energy_per_km':1}})
    monkeypatch.setattr(route_api, 'generate_candidates', lambda *a, **k: [route_api.RouteCandidate.model_validate(candidate)])
    monkeypatch.setattr(route_api, 'save_candidates', lambda *a, **k: [])
    c=client(monkeypatch, principal(['apollo.routes.generate']))
    r=c.post('/v1/routes/sessions/s1/generate',headers={'Authorization':'Bearer x'})
    assert r.status_code==200
    got=r.json()['candidates'][0]
    for key in ['geometry','metrics','score','score_breakdown','feasibility','rejection_reasons','explanation','data_quality','engine_version','profile_version','policy_version','provenance']:
        assert key in got


def test_feature_flag_disabled(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','0')
    app=FastAPI(); app.include_router(route_api.router); c=TestClient(app)
    r=c.post('/v1/routes/sessions',json=body(),headers={'Authorization':'Bearer x'})
    assert r.status_code==503

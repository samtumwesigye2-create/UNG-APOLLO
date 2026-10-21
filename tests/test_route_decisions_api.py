import os
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import route_api


def _principal(perms):
    return {'id':'p1','tenant_id':'t1','organization_id':'org1','classification':'SECRET','compartments':['ALPHA'],'permissions':perms}


def _client(monkeypatch, perms):
    os.environ['APOLLO_ROUTE_PLANNING_ENABLED']='1'
    app=FastAPI(); app.include_router(route_api.router)
    def fake_auth(permission, authorization):
        if not authorization: raise HTTPException(401,'JANUS bearer token required')
        p=_principal(perms)
        if permission not in perms and 'ung.admin' not in perms: raise HTTPException(403,'missing permission')
        return p
    monkeypatch.setattr(route_api,'_authorize',fake_auth)
    return TestClient(app)


def _candidate(cid,index,score):
    return {'payload':{'candidate_id':cid,'candidate_index':index,'node_sequence':['A','D'],'edge_sequence':['AD'],'geometry':{'type':'LineString','coordinates':[]},'metrics':{},'score':score,'score_breakdown':{},'feasibility':'feasible','rejection_reasons':[],'explanation':cid}}


def test_decision_preserves_manual_override(monkeypatch):
    session={'id':'s1','request':{},'vehicle_profile_id':'v1','vehicle_profile_version':'1'}
    monkeypatch.setattr(route_api,'get_session',lambda *a,**k:session)
    monkeypatch.setattr(route_api,'list_candidates',lambda *a,**k:[_candidate('top',0,0.1),_candidate('other',1,0.2)])
    recorded=[]; events=[]
    monkeypatch.setattr(route_api,'record_decision',lambda *a,**k:recorded.append((a,k)) or {'id':'d1','decision_type':'select','candidate_id':'other'})
    monkeypatch.setattr(route_api,'queue_route_event',lambda *a,**k:events.append((a,k)) or {'id':'e'})
    c=_client(monkeypatch,['apollo.routes.decide'])
    r=c.post('/v1/routes/sessions/s1/decisions',json={'decision_type':'select','candidate_id':'other','rationale':'operator preference'},headers={'Authorization':'Bearer x'})
    assert r.status_code==201 and recorded
    assert any('MANUAL_OVERRIDE' in args[1] for args,kwargs in events)


def test_replay_endpoint_uses_replay_permission(monkeypatch):
    bundle=type('B',(),{'replay_mode':'exact','model_dump':lambda self,mode=None:{'session_id':'s1','replay_mode':'exact'}})()
    monkeypatch.setattr(route_api,'build_replay_bundle',lambda *a,**k:bundle)
    monkeypatch.setattr(route_api,'queue_route_event',lambda *a,**k:{})
    c=_client(monkeypatch,['apollo.routes.replay'])
    r=c.get('/v1/routes/sessions/s1/replay',headers={'Authorization':'Bearer x'})
    assert r.status_code==200 and r.json()['replay_mode']=='exact'

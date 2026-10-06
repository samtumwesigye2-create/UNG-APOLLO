from route_models import SecurityContext
import route_replay


def ctx(): return SecurityContext(tenant_id='t',principal_id='p')


def base_session():
    return {'id':'s1','request':{'plan_id':'p','security_context':ctx().model_dump(mode='json')},'security_context':ctx().model_dump(mode='json'),'engine_version':'1.0.0','policy_version':'1.0.0','vehicle_profile_id':'v','vehicle_profile_version':'2','data_snapshot':{'dataset_id':'d','version':'1'}}


def test_replay_bundle_exact_contains_versions_outputs_and_decisions(monkeypatch):
    monkeypatch.setattr(route_replay,'get_session',lambda *a,**k:base_session())
    monkeypatch.setattr(route_replay,'get_dataset_snapshot',lambda *a,**k:{'dataset_id':'d','version':'1','payload':{'dataset_id':'d','version':'1','graph_provenance':{'content_hash':'h'}}})
    monkeypatch.setattr(route_replay,'list_candidates',lambda *a,**k:[{'payload':{'candidate_id':'c','candidate_index':0,'node_sequence':['A','D'],'edge_sequence':['AD'],'geometry':{'type':'LineString','coordinates':[]},'metrics':{},'score':0.1,'score_breakdown':{},'feasibility':'feasible','rejection_reasons':[],'explanation':'ok'}}])
    monkeypatch.setattr(route_replay,'list_windows',lambda *a,**k:[])
    monkeypatch.setattr(route_replay,'list_decisions',lambda *a,**k:[{'decision_type':'select','rationale':'operator choice'}])
    monkeypatch.setattr(route_replay,'list_route_events',lambda *a,**k:[])
    b=route_replay.build_replay_bundle('s1',ctx())
    assert b.replay_mode=='exact' and b.profile_version=='2'
    assert b.decisions[0]['rationale']=='operator choice'
    assert b.dataset_refs[0]['content_hash']=='h'


def test_missing_snapshot_marks_replay_approximate(monkeypatch):
    monkeypatch.setattr(route_replay,'get_session',lambda *a,**k:base_session())
    monkeypatch.setattr(route_replay,'get_dataset_snapshot',lambda *a,**k:None)
    monkeypatch.setattr(route_replay,'list_candidates',lambda *a,**k:[])
    monkeypatch.setattr(route_replay,'list_windows',lambda *a,**k:[])
    monkeypatch.setattr(route_replay,'list_decisions',lambda *a,**k:[])
    monkeypatch.setattr(route_replay,'list_route_events',lambda *a,**k:[])
    b=route_replay.build_replay_bundle('s1',ctx())
    assert b.replay_mode=='approximate'
    assert any('snapshot' in x.lower() for x in b.limitations)

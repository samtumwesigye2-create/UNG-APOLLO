from datetime import datetime, timezone

import route_events
from route_models import SecurityContext


def ctx(): return SecurityContext(tenant_id='t1',principal_id='p1',classification='SECRET',compartments=['ALPHA'])


def test_duplicate_message_id_is_idempotent(monkeypatch):
    seen=set(); events=[]
    monkeypatch.setattr(route_events,'route_event_exists',lambda key:key in seen)
    def append(session_id,event_type,payload,source_system,security_context,idempotency_key=None):
        seen.add(idempotency_key); events.append(idempotency_key); return {'id':'e'}
    monkeypatch.setattr(route_events,'append_route_event',append)
    payload={'source_system':'MAP','version':'1','nodes':[],'edges':[],'security_context':ctx().model_dump(mode='json')}
    first=route_events.ingest_route_snapshot('APOLLO.ROUTE.GRAPH_SNAPSHOT',payload,'m1',ctx())
    second=route_events.ingest_route_snapshot('APOLLO.ROUTE.GRAPH_SNAPSHOT',payload,'m1',ctx())
    assert first['duplicate'] is False and second['duplicate'] is True
    assert events==['nexus:m1']


def test_vehicle_profile_update_is_versioned(monkeypatch):
    saved=[]
    monkeypatch.setattr(route_events,'route_event_exists',lambda key:False)
    monkeypatch.setattr(route_events,'append_route_event',lambda *a,**k:{'id':'e'})
    monkeypatch.setattr(route_events,'upsert_vehicle_profile',lambda profile,security_context,source_message_id=None:saved.append((profile.profile_id,profile.version)) or {'id':'p'})
    p={'profile_id':'v1','version':'2','name':'V','mode':'ground','max_range_km':100,'cruise_speed_kph':60,'energy_per_km':1}
    out=route_events.ingest_route_snapshot('APOLLO.ROUTE.VEHICLE_PROFILE_UPDATED',p,'m2',ctx())
    assert saved==[('v1','2')] and out['kind']=='profile'


def test_queue_event_never_persists_authorization(monkeypatch):
    captured={}
    monkeypatch.setattr(route_events,'append_route_event',lambda *a,**k:{'id':'e'})
    monkeypatch.setattr(route_events,'enqueue_outbox',lambda **k:captured.update(k) or {'id':'o'})
    route_events.queue_route_event('s1','APOLLO.ROUTE.SESSION_CREATED',{'authorization':'Bearer secret','x':1},ctx(),'k1')
    assert 'authorization' not in captured['payload']
    assert 'Bearer secret' not in str(captured)


def test_failed_publish_stays_pending_then_success_marks_delivered(monkeypatch):
    rows=[{'id':'o1','target_system':'UNG-SENTINEL','message_type':'APOLLO.ROUTE.DECISION_RECORDED','payload':{'x':1}}]
    monkeypatch.setattr(route_events,'list_pending_outbox',lambda limit=100:rows)
    delivered=[]; failed=[]
    monkeypatch.setattr(route_events,'mark_outbox_delivered',lambda oid:delivered.append(oid))
    monkeypatch.setattr(route_events,'mark_outbox_failed',lambda oid,error:failed.append(oid))
    monkeypatch.setattr(route_events,'_publish',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('down')))
    out=route_events.flush_route_outbox('Bearer x')
    assert out['failed']==1 and delivered==[] and failed==['o1']
    failed.clear()
    monkeypatch.setattr(route_events,'_publish',lambda *a,**k:{'accepted':True})
    out=route_events.flush_route_outbox('Bearer x')
    assert out['delivered']==1 and delivered==['o1']

from __future__ import annotations

from typing import Any

from route_dataset_adapter import build_planning_dataset
from route_models import SecurityContext, VehicleProfile
from route_store import (
    append_route_event, enqueue_outbox, list_pending_outbox, mark_outbox_delivered,
    mark_outbox_failed, save_dataset_snapshot, upsert_vehicle_profile,
)
import route_store


def route_event_exists(idempotency_key: str) -> bool:
    with route_store._connection_factory() as c:
        return bool(c.execute('SELECT id FROM apollo_route_events WHERE idempotency_key=%s', (idempotency_key,)).fetchone())


ROUTE_SNAPSHOT_TYPES = {
    'APOLLO.ROUTE.GRAPH_SNAPSHOT', 'APOLLO.ROUTE.WEATHER_SNAPSHOT',
    'APOLLO.ROUTE.TERRAIN_SNAPSHOT', 'APOLLO.ROUTE.GEOFENCE_SNAPSHOT',
    'APOLLO.ROUTE.COMMS_SNAPSHOT', 'APOLLO.ROUTE.VEHICLE_PROFILE_UPDATED',
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items() if k.lower() not in {'authorization','bearer_token','token','access_token'}}
    if isinstance(value, list): return [_sanitize(v) for v in value]
    return value


def ingest_route_snapshot(message_type: str, payload: dict[str, Any], message_id: str, security_context: SecurityContext) -> dict[str, Any]:
    if message_type not in ROUTE_SNAPSHOT_TYPES:
        return {'accepted': False, 'reason': 'unsupported_route_snapshot'}
    key = f'nexus:{message_id}'
    if route_event_exists(key):
        return {'accepted': True, 'duplicate': True, 'message_id': message_id}
    clean = _sanitize(payload)
    append_route_event(None, message_type, clean, str(payload.get('source_system') or 'UNG-NEXUS'), security_context, key)
    if message_type == 'APOLLO.ROUTE.VEHICLE_PROFILE_UPDATED':
        profile = VehicleProfile.model_validate(clean)
        row = upsert_vehicle_profile(profile, security_context, source_message_id=message_id)
        return {'accepted': True, 'duplicate': False, 'kind': 'profile', 'record': row}
    if message_type == 'APOLLO.ROUTE.GRAPH_SNAPSHOT' and clean.get('nodes') and clean.get('edges'):
        profile_payload = clean.get('vehicle_profile')
        if profile_payload:
            profile = VehicleProfile.model_validate(profile_payload)
        else:
            profile = VehicleProfile(profile_id='adapter-default',version='1',name='Adapter',mode='simulation',max_range_km=1e9,cruise_speed_kph=1,energy_per_km=1,metadata={'optional_layers': list((clean.get('optional_snapshots') or {}).keys())})
        dataset = build_planning_dataset(clean, clean.get('optional_snapshots') or {}, profile)
        row = save_dataset_snapshot(dataset, source_message_id=message_id)
        return {'accepted': True, 'duplicate': False, 'kind': 'dataset', 'record': row, 'dataset_id': dataset.dataset_id, 'version': dataset.version}
    return {'accepted': True, 'duplicate': False, 'kind': 'layer', 'message_id': message_id}


def queue_route_event(session_id: str | None, event_type: str, payload: dict[str, Any], security_context: SecurityContext, idempotency_key: str) -> dict[str, Any]:
    clean = _sanitize(payload)
    event_payload = dict(clean)
    if event_type.endswith('DECISION_RECORDED'):
        event_payload.setdefault('decision_source', 'human_decision')
    elif event_type.endswith('CANDIDATES_GENERATED'):
        event_payload.setdefault('decision_source', 'system_recommendation')
    append_route_event(session_id, event_type, event_payload, 'UNG-APOLLO', security_context, f'event:{idempotency_key}')
    return enqueue_outbox(
        session_id=session_id, target_system='UNG-SENTINEL', message_type=event_type,
        payload=event_payload, security_context=security_context, idempotency_key=idempotency_key,
    )


def _publish(envelope: dict[str, Any], authorization: str | None):
    from nexus_bridge import publish_envelope
    return publish_envelope(envelope, authorization)


def flush_route_outbox(authorization: str | None, limit: int = 100) -> dict[str, int]:
    delivered = failed = 0
    for row in list_pending_outbox(limit):
        envelope = {'source_system':'UNG-APOLLO','target_system':row['target_system'],'message_type':row['message_type'],'payload':_sanitize(row['payload'])}
        try:
            _publish(envelope, authorization)
        except Exception as exc:
            mark_outbox_failed(row['id'], type(exc).__name__)
            failed += 1
        else:
            mark_outbox_delivered(row['id'])
            delivered += 1
    return {'delivered': delivered, 'failed': failed}

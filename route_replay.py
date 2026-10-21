from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from route_models import RouteCandidate, RouteReplayBundle, RouteWindowResult, SecurityContext
from route_store import get_dataset_snapshot, get_session, list_candidates, list_decisions, list_route_events, list_windows


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value=row.get('payload')
    return value if isinstance(value,dict) else dict(value or {})


def build_replay_bundle(session_id: str, security_context: SecurityContext) -> RouteReplayBundle:
    session=get_session(session_id, security_context)
    if not session: raise HTTPException(404,'route_session_not_found')
    candidate_rows=list_candidates(session_id,security_context)
    candidates=[RouteCandidate.model_validate(_payload(r)) for r in candidate_rows]
    snapshot_ref=session.get('data_snapshot') or {}
    dataset_id=snapshot_ref.get('dataset_id') or (session.get('request') or {}).get('dataset_id')
    version=snapshot_ref.get('version')
    if candidates and (not dataset_id or not version):
        provenance=candidates[0].provenance or {}
        dataset_id=dataset_id or provenance.get('dataset_id')
        version=version or provenance.get('dataset_version')
    dataset=get_dataset_snapshot(dataset_id,security_context,version) if dataset_id else None
    mode='exact'; limitations=[]; refs=[]
    if dataset:
        payload=_payload(dataset)
        gp=payload.get('graph_provenance') or {}
        refs.append({'dataset_id':payload.get('dataset_id',dataset_id),'version':payload.get('version',version),'content_hash':gp.get('content_hash')})
    else:
        mode='approximate'; limitations.append('Exact immutable data snapshot is unavailable; replay cannot reproduce external data exactly.')
    windows=[RouteWindowResult.model_validate(_payload(r)) for r in list_windows(session_id,security_context)]
    return RouteReplayBundle(
        session_id=session_id,replay_mode=mode,
        normalized_request=session.get('request') or {},
        security_context=SecurityContext.model_validate(session.get('security_context') or security_context),
        engine_version=session.get('engine_version') or 'unknown',
        policy_version=session.get('policy_version') or 'unknown',
        profile_id=session.get('vehicle_profile_id') or 'unknown',
        profile_version=session.get('vehicle_profile_version') or 'unknown',
        dataset_refs=refs,candidates=candidates,windows=windows,
        decisions=list_decisions(session_id,security_context),
        events=list_route_events(session_id,security_context),limitations=limitations,
    )

_build_replay_bundle_core = build_replay_bundle

def build_replay_bundle(session_id, security_context):
    from route_observability import metrics, timed_operation
    with timed_operation('route_replay_latency'):
        bundle=_build_replay_bundle_core(session_id,security_context)
    metrics.increment('route_replay_result', mode=bundle.replay_mode)
    return bundle

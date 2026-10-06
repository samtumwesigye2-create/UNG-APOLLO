from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from route_engine import RouteDataUnavailable, generate_candidates
from route_models import PlanningDataset, RouteCandidate, RouteSessionCreate, SecurityContext, VehicleProfile
from route_store import (
    create_session, get_candidate, get_dataset_snapshot, get_session, get_vehicle_profile,
    list_candidates, list_vehicle_profiles, save_candidates, security_context_allows,
)

router = APIRouter(prefix='/v1/routes', tags=['Route Planning'])


def route_feature_enabled() -> bool:
    return os.getenv('APOLLO_ROUTE_PLANNING_ENABLED', '').strip().lower() in {'1','true','yes','on'}


def _require_feature() -> None:
    if not route_feature_enabled():
        raise HTTPException(503, 'route_planning_feature_disabled')


def _authorize(permission: str, authorization: str | None):
    from app import auth
    return auth(permission, authorization)


def _principal_security(principal: dict[str, Any]) -> SecurityContext:
    principal_id = str(principal.get('id') or principal.get('principal_id') or '').strip()
    tenant_id = str(principal.get('tenant_id') or principal.get('organization_id') or '').strip()
    if not principal_id or not tenant_id:
        raise HTTPException(403, 'janus_security_context_incomplete')
    return SecurityContext(
        tenant_id=tenant_id,
        principal_id=principal_id,
        organization_id=principal.get('organization_id'),
        classification=principal.get('classification') or 'UNCLASSIFIED',
        compartments=list(principal.get('compartments') or []),
        sharing_policy=dict(principal.get('sharing_policy') or {}),
    )


def _authorized_context(permission: str, authorization: str | None) -> tuple[dict[str, Any], SecurityContext]:
    principal = _authorize(permission, authorization)
    return principal, _principal_security(principal)


def plan_exists(plan_id: str) -> bool:
    from app import conn
    with conn() as c:
        return bool(c.execute('SELECT id FROM apollo_plans WHERE id=%s', (plan_id,)).fetchone())


def synthetic_test_dataset_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        'dataset_id':'d','version':'1','source_system':'TEST','fetched_at':now,
        'graph_provenance':{'layer':'graph','source_system':'TEST','version':'1','fetched_at':now},
        'nodes':[{'node_id':'A','coordinate':{'lat':0,'lon':0}},{'node_id':'D','coordinate':{'lat':1,'lon':1}}],
        'edges':[{'edge_id':'AD','from_node':'A','to_node':'D','distance_km':1,'route_type':'road'}],
        'security_context':{'tenant_id':'t1','principal_id':'p1','classification':'SECRET','compartments':['ALPHA']},
    }


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('payload')
    return value if isinstance(value, dict) else dict(value or {})


@router.post('/sessions', status_code=201)
def create_route_session(body: RouteSessionCreate, authorization: str | None = Header(None)):
    _require_feature()
    principal, actor = _authorized_context('apollo.routes.write', authorization)
    if not security_context_allows(actor, body.security_context):
        raise HTTPException(403, 'route_security_context_denied')
    if not plan_exists(body.plan_id):
        raise HTTPException(404, 'plan_not_found')
    profile_row = get_vehicle_profile(body.vehicle_profile_id, actor)
    if not profile_row:
        raise HTTPException(404, 'route_profile_not_found')
    profile = VehicleProfile.model_validate(_payload(profile_row))
    return create_session(body, actor.principal_id, profile.version)


@router.get('/sessions/{session_id}')
def route_session(session_id: str, authorization: str | None = Header(None)):
    _require_feature()
    _, actor = _authorized_context('apollo.routes.read', authorization)
    row = get_session(session_id, actor)
    if not row:
        raise HTTPException(404, 'route_session_not_found')
    return row


@router.post('/sessions/{session_id}/generate')
def generate_route_session(session_id: str, authorization: str | None = Header(None)):
    _require_feature()
    _, actor = _authorized_context('apollo.routes.generate', authorization)
    session = get_session(session_id, actor)
    if not session:
        raise HTTPException(404, 'route_session_not_found')
    request = RouteSessionCreate.model_validate(session['request'])
    dataset_id = request.dataset_id or (session.get('data_snapshot') or {}).get('dataset_id')
    dataset_row = get_dataset_snapshot(dataset_id, actor) if dataset_id else get_dataset_snapshot('', actor)
    if not dataset_row:
        raise HTTPException(409, 'required_route_dataset_unavailable')
    profile_row = get_vehicle_profile(session['vehicle_profile_id'], actor, session.get('vehicle_profile_version'))
    if not profile_row:
        raise HTTPException(409, 'route_profile_unavailable')
    dataset = PlanningDataset.model_validate(_payload(dataset_row))
    profile = VehicleProfile.model_validate(_payload(profile_row))
    try:
        candidates = generate_candidates(request, dataset, profile, request.max_alternatives)
    except RouteDataUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    save_candidates(session_id, candidates, actor)
    return {'session_id': session_id, 'candidates': [c.model_dump(mode='json') for c in candidates]}


@router.get('/sessions/{session_id}/candidates')
def candidates(session_id: str, authorization: str | None = Header(None)):
    _require_feature()
    _, actor = _authorized_context('apollo.routes.read', authorization)
    rows = list_candidates(session_id, actor)
    return [_payload(row) for row in rows]


@router.get('/sessions/{session_id}/candidates/{candidate_id}')
def candidate(session_id: str, candidate_id: str, authorization: str | None = Header(None)):
    _require_feature()
    _, actor = _authorized_context('apollo.routes.read', authorization)
    row = get_candidate(session_id, candidate_id, actor)
    if not row:
        raise HTTPException(404, 'route_candidate_not_found')
    return _payload(row)


@router.get('/profiles')
def profiles(authorization: str | None = Header(None)):
    _require_feature()
    _, actor = _authorized_context('apollo.routes.read', authorization)
    return [_payload(row) for row in list_vehicle_profiles(actor)]

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import auth, conn

router = APIRouter(prefix='/v1/nexus', tags=['NEXUS Integration'])
NEXUS_BASE_URL = os.getenv('NEXUS_BASE_URL', 'https://ung-nexus-production.up.railway.app').rstrip('/')


class NexusEnvelope(BaseModel):
    message_id: str | None = None
    source_system: str
    target_system: str
    message_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: str | None = None
    principal_id: str | None = None


class PublishRequest(BaseModel):
    target_system: str
    message_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


def _json_safe(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _ensure_integration_table():
    with conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_integration_events(
                id UUID PRIMARY KEY,
                nexus_message_id TEXT,
                source_system TEXT,
                target_system TEXT,
                message_type TEXT,
                payload JSONB,
                status TEXT,
                created_at TIMESTAMPTZ
            )
        ''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_apollo_integration_nexus_message ON apollo_integration_events(nexus_message_id) WHERE nexus_message_id IS NOT NULL')


def publish_envelope(envelope: dict[str, Any], authorization: str | None) -> dict[str, Any]:
    req = urllib.request.Request(
        NEXUS_BASE_URL + '/v1/messages',
        data=json.dumps(envelope, default=_json_safe).encode(),
        method='POST',
        headers={
            'Authorization': authorization or '',
            'Content-Type': 'application/json',
            'User-Agent': 'UNG-APOLLO/0.5',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode() or '{}')
            return {'accepted': True, 'nexus_status': response.status, 'message': result}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:500]
        raise HTTPException(exc.code, f'nexus_publish_failed:{detail}')
    except Exception as exc:
        raise HTTPException(503, f'nexus_unavailable:{type(exc).__name__}')


def _route_security_context(principal: dict[str, Any]):
    from route_models import SecurityContext
    tenant_id = principal.get('tenant_id') or principal.get('organization_id')
    principal_id = principal.get('id') or principal.get('principal_id')
    if not tenant_id or not principal_id:
        raise HTTPException(403, 'janus_security_context_incomplete')
    return SecurityContext(
        tenant_id=str(tenant_id), principal_id=str(principal_id),
        organization_id=principal.get('organization_id'),
        classification=principal.get('classification') or 'UNCLASSIFIED',
        compartments=list(principal.get('compartments') or []),
        sharing_policy=dict(principal.get('sharing_policy') or {}),
    )


@router.get('/status')
def nexus_status():
    try:
        _ensure_integration_table()
        with conn() as c:
            total = c.execute('SELECT COUNT(*) n FROM apollo_integration_events').fetchone()['n']
            last_probe = c.execute(
                "SELECT nexus_message_id,status,created_at FROM apollo_integration_events WHERE message_type='APOLLO.ACCEPTANCE.PING' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            'status': 'ready', 'service': 'UNG-APOLLO', 'nexus': NEXUS_BASE_URL,
            'inbound': '/v1/nexus/inbound', 'outbound': '/v1/nexus/publish',
            'acceptance': '/v1/nexus/acceptance', 'events': total, 'last_acceptance': last_probe,
        }
    except Exception as exc:
        raise HTTPException(503, f'nexus_bridge_unavailable:{type(exc).__name__}')


@router.post('/acceptance', status_code=202)
def nexus_acceptance_probe():
    _ensure_integration_table()
    mid = str(uuid4()); now = datetime.now(timezone.utc)
    with conn() as c:
        event = c.execute(
            '''INSERT INTO apollo_integration_events
               (id,nexus_message_id,source_system,target_system,message_type,payload,status,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), mid, 'UNG-NEXUS', 'UNG-APOLLO', 'APOLLO.ACCEPTANCE.PING',
             psycopg.types.json.Jsonb({'probe': True}), 'accepted', now),
        ).fetchone()
    return {'accepted': True, 'message_id': mid, 'event': event}


@router.post('/inbound', status_code=202)
def nexus_inbound(body: NexusEnvelope, authorization: str | None = Header(None)):
    principal = auth('nexus.messages.write', authorization)
    if body.target_system != 'UNG-APOLLO':
        raise HTTPException(409, 'wrong_target_system')
    _ensure_integration_table()
    mid = body.message_id or str(uuid4()); now = datetime.now(timezone.utc)
    with conn() as c:
        existing = c.execute('SELECT * FROM apollo_integration_events WHERE nexus_message_id=%s', (mid,)).fetchone()
        if existing:
            return {'accepted': True, 'duplicate': True, 'event': existing}

        status = 'accepted'
        route_result = None
        if body.message_type == 'APOLLO.INTELLIGENCE.INGEST':
            p = body.payload; plan_id = p.get('plan_id')
            if not plan_id: raise HTTPException(422, 'payload.plan_id_required')
            if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (plan_id,)).fetchone():
                raise HTTPException(404, 'plan_not_found')
            confidence = float(p.get('confidence', 0.5))
            if confidence < 0 or confidence > 1: raise HTTPException(422, 'confidence_must_be_0_to_1')
            c.execute(
                '''INSERT INTO apollo_intelligence
                   (id,plan_id,source_system,intelligence_type,title,summary,severity,confidence,observed_at,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (str(uuid4()), plan_id, body.source_system, p.get('intelligence_type', 'nexus-event'),
                 p.get('title', body.message_type), p.get('summary', ''), p.get('severity', 'informational'),
                 confidence, now, now),
            )
            status = 'ingested'
        elif body.message_type.startswith('APOLLO.ROUTE.'):
            from route_events import ingest_route_snapshot
            route_result = ingest_route_snapshot(body.message_type, body.payload, mid, _route_security_context(principal))
            status = 'ingested' if route_result.get('accepted') else 'rejected'

        event = c.execute(
            '''INSERT INTO apollo_integration_events
               (id,nexus_message_id,source_system,target_system,message_type,payload,status,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), mid, body.source_system, body.target_system, body.message_type,
             psycopg.types.json.Jsonb(body.payload), status, now),
        ).fetchone()
    return {'accepted': True, 'duplicate': False, 'principal_id': principal.get('id'), 'event': event, 'route': route_result}


@router.post('/publish', status_code=202)
def nexus_publish(body: PublishRequest, authorization: str | None = Header(None)):
    principal = auth('apollo.plans.read', authorization)
    envelope = {'source_system':'UNG-APOLLO','target_system':body.target_system,'message_type':body.message_type,'payload':body.payload}
    result = publish_envelope(envelope, authorization)
    result['principal_id'] = principal.get('id')
    return result

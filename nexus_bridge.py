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
    if isinstance(value, (datetime,)):
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


@router.get('/status')
def nexus_status():
    try:
        _ensure_integration_table()
        with conn() as c:
            total = c.execute('SELECT COUNT(*) n FROM apollo_integration_events').fetchone()['n']
        return {
            'status': 'ready',
            'service': 'UNG-APOLLO',
            'nexus': NEXUS_BASE_URL,
            'inbound': '/v1/nexus/inbound',
            'outbound': '/v1/nexus/publish',
            'events': total,
        }
    except Exception as exc:
        raise HTTPException(503, f'nexus_bridge_unavailable:{type(exc).__name__}')


@router.post('/inbound', status_code=202)
def nexus_inbound(body: NexusEnvelope, authorization: str | None = Header(None)):
    principal = auth('nexus.messages.write', authorization)
    if body.target_system != 'UNG-APOLLO':
        raise HTTPException(409, 'wrong_target_system')

    _ensure_integration_table()
    mid = body.message_id or str(uuid4())
    now = datetime.now(timezone.utc)

    with conn() as c:
        existing = c.execute(
            'SELECT * FROM apollo_integration_events WHERE nexus_message_id=%s',
            (mid,),
        ).fetchone()
        if existing:
            return {'accepted': True, 'duplicate': True, 'event': existing}

        status = 'accepted'
        if body.message_type == 'APOLLO.INTELLIGENCE.INGEST':
            p = body.payload
            plan_id = p.get('plan_id')
            if not plan_id:
                raise HTTPException(422, 'payload.plan_id_required')
            if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (plan_id,)).fetchone():
                raise HTTPException(404, 'plan_not_found')
            confidence = float(p.get('confidence', 0.5))
            if confidence < 0 or confidence > 1:
                raise HTTPException(422, 'confidence_must_be_0_to_1')
            c.execute(
                '''INSERT INTO apollo_intelligence
                   (id,plan_id,source_system,intelligence_type,title,summary,severity,confidence,observed_at,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (
                    str(uuid4()), plan_id, body.source_system,
                    p.get('intelligence_type', 'nexus-event'),
                    p.get('title', body.message_type),
                    p.get('summary', ''),
                    p.get('severity', 'informational'),
                    confidence,
                    now,
                    now,
                ),
            )
            status = 'ingested'

        event = c.execute(
            '''INSERT INTO apollo_integration_events
               (id,nexus_message_id,source_system,target_system,message_type,payload,status,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (
                str(uuid4()), mid, body.source_system, body.target_system,
                body.message_type, psycopg.types.json.Jsonb(body.payload), status, now,
            ),
        ).fetchone()

    return {
        'accepted': True,
        'duplicate': False,
        'principal_id': principal.get('id'),
        'event': event,
    }


@router.post('/publish', status_code=202)
def nexus_publish(body: PublishRequest, authorization: str | None = Header(None)):
    principal = auth('apollo.plans.read', authorization)
    envelope = {
        'source_system': 'UNG-APOLLO',
        'target_system': body.target_system,
        'message_type': body.message_type,
        'payload': body.payload,
    }
    req = urllib.request.Request(
        NEXUS_BASE_URL + '/v1/messages',
        data=json.dumps(envelope, default=_json_safe).encode(),
        method='POST',
        headers={
            'Authorization': authorization or '',
            'Content-Type': 'application/json',
            'User-Agent': 'UNG-APOLLO/0.4',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode())
            return {
                'accepted': True,
                'nexus_status': response.status,
                'principal_id': principal.get('id'),
                'message': result,
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:500]
        raise HTTPException(exc.code, f'nexus_publish_failed:{detail}')
    except Exception as exc:
        raise HTTPException(503, f'nexus_unavailable:{type(exc).__name__}')

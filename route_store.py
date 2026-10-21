from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from fastapi import HTTPException

from route_models import (
    PlanningDataset,
    RouteCandidate,
    RouteSessionCreate,
    RouteWindowResult,
    SecurityContext,
    VehicleProfile,
    ROUTE_ENGINE_VERSION,
    ROUTE_POLICY_VERSION,
)

_CLASSIFICATION_RANK = {
    "UNCLASSIFIED": 0,
    "PUBLIC": 0,
    "CUI": 1,
    "CONFIDENTIAL": 2,
    "SECRET": 3,
    "TOP_SECRET": 4,
    "TOP SECRET": 4,
}


def _connection_factory():
    from app import conn
    return conn()


def _jsonb(value: Any):
    try:
        import psycopg
        return psycopg.types.json.Jsonb(value)
    except Exception:
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ctx(value: SecurityContext | dict[str, Any]) -> SecurityContext:
    return value if isinstance(value, SecurityContext) else SecurityContext.model_validate(value)


def security_context_allows(actor: SecurityContext | dict[str, Any], stored: SecurityContext | dict[str, Any]) -> bool:
    actor_ctx = _ctx(actor)
    stored_ctx = _ctx(stored)
    same_tenant = actor_ctx.tenant_id == stored_ctx.tenant_id
    shared_tenants = set(stored_ctx.sharing_policy.get("allowed_tenants") or [])
    if not same_tenant and actor_ctx.tenant_id not in shared_tenants:
        return False
    actor_rank = _CLASSIFICATION_RANK.get(actor_ctx.classification.upper(), -1)
    stored_rank = _CLASSIFICATION_RANK.get(stored_ctx.classification.upper(), 10**6)
    if actor_rank < stored_rank:
        return False
    if not set(stored_ctx.compartments).issubset(set(actor_ctx.compartments)):
        return False
    return True


def _require_security(actor: SecurityContext | dict[str, Any], stored: SecurityContext | dict[str, Any]) -> None:
    if not security_context_allows(actor, stored):
        raise HTTPException(403, "route_security_context_denied")


def ensure_route_schema() -> None:
    with _connection_factory() as c:
        statements = [
            '''CREATE TABLE IF NOT EXISTS apollo_route_sessions(
                id UUID PRIMARY KEY,
                plan_id UUID NOT NULL,
                vehicle_profile_id TEXT NOT NULL,
                vehicle_profile_version TEXT,
                status TEXT NOT NULL,
                request JSONB NOT NULL,
                engine_version TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                security_context JSONB NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_candidates(
                id UUID PRIMARY KEY,
                session_id UUID NOT NULL,
                candidate_index INT NOT NULL,
                geometry JSONB NOT NULL,
                metrics JSONB NOT NULL,
                score DOUBLE PRECISION,
                score_breakdown JSONB NOT NULL,
                feasibility TEXT NOT NULL,
                rejection_reasons JSONB NOT NULL,
                explanation TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_windows(
                id UUID PRIMARY KEY,
                session_id UUID NOT NULL,
                departure_time TIMESTAMPTZ NOT NULL,
                candidate_id UUID,
                feasibility TEXT NOT NULL,
                metrics JSONB NOT NULL,
                score DOUBLE PRECISION,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_decisions(
                id UUID PRIMARY KEY,
                session_id UUID NOT NULL,
                candidate_id UUID,
                actor_id TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                rationale TEXT NOT NULL,
                metadata JSONB NOT NULL,
                security_context JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_events(
                id UUID PRIMARY KEY,
                session_id UUID,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                source_system TEXT NOT NULL,
                security_context JSONB NOT NULL,
                idempotency_key TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_datasets(
                id UUID PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                version TEXT NOT NULL,
                payload JSONB NOT NULL,
                security_context JSONB NOT NULL,
                source_message_id TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(dataset_id, version)
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_profiles(
                id UUID PRIMARY KEY,
                profile_id TEXT NOT NULL,
                version TEXT NOT NULL,
                payload JSONB NOT NULL,
                security_context JSONB NOT NULL,
                source_message_id TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(profile_id, version)
            )''',
            '''CREATE TABLE IF NOT EXISTS apollo_route_outbox(
                id UUID PRIMARY KEY,
                session_id UUID,
                target_system TEXT NOT NULL,
                message_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                security_context JSONB NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INT NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                delivered_at TIMESTAMPTZ
            )''',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_sessions_plan_id ON apollo_route_sessions(plan_id)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_sessions_created_at ON apollo_route_sessions(created_at)',
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_apollo_route_candidates_lookup ON apollo_route_candidates(session_id,candidate_index)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_candidates_session_id ON apollo_route_candidates(session_id)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_candidates_created_at ON apollo_route_candidates(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_windows_session_id ON apollo_route_windows(session_id)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_windows_created_at ON apollo_route_windows(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_decisions_session_id ON apollo_route_decisions(session_id)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_decisions_created_at ON apollo_route_decisions(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_events_session_id ON apollo_route_events(session_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_apollo_route_events_idempotency_key ON apollo_route_events(idempotency_key) WHERE idempotency_key IS NOT NULL',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_datasets_dataset_id_version ON apollo_route_datasets(dataset_id,version)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_profiles_profile_id_version ON apollo_route_profiles(profile_id,version)',
            'CREATE INDEX IF NOT EXISTS idx_apollo_route_outbox_created_at ON apollo_route_outbox(created_at)',
        ]
        for statement in statements:
            c.execute(statement)


def create_session(request: RouteSessionCreate, created_by: str, profile_version: str | None = None) -> dict[str, Any]:
    now = _now()
    session_id = str(uuid4())
    payload = request.model_dump(mode="json")
    with _connection_factory() as c:
        row = c.execute(
            '''INSERT INTO apollo_route_sessions
               (id,plan_id,vehicle_profile_id,vehicle_profile_version,status,request,engine_version,policy_version,data_snapshot,security_context,created_by,created_at,updated_at)
               VALUES(%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (session_id, request.plan_id, request.vehicle_profile_id, profile_version,
             _jsonb(payload), ROUTE_ENGINE_VERSION, ROUTE_POLICY_VERSION, _jsonb({}),
             _jsonb(request.security_context.model_dump(mode="json")), created_by, now, now),
        ).fetchone()
    return row


def get_session(session_id: str, actor_security: SecurityContext) -> dict[str, Any] | None:
    with _connection_factory() as c:
        row = c.execute('SELECT * FROM apollo_route_sessions WHERE id=%s', (session_id,)).fetchone()
    if not row:
        return None
    _require_security(actor_security, row['security_context'])
    return row


def save_candidates(session_id: str, candidates: Iterable[RouteCandidate], actor_security: SecurityContext) -> list[dict[str, Any]]:
    session = get_session(session_id, actor_security)
    if not session:
        raise HTTPException(404, 'route_session_not_found')
    rows = []
    now = _now()
    with _connection_factory() as c:
        c.execute('DELETE FROM apollo_route_candidates WHERE session_id=%s', (session_id,))
        for cand in candidates:
            data = cand.model_dump(mode='json')
            row = c.execute(
                '''INSERT INTO apollo_route_candidates
                   (id,session_id,candidate_index,geometry,metrics,score,score_breakdown,feasibility,rejection_reasons,explanation,payload,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (cand.candidate_id, session_id, cand.candidate_index, _jsonb(cand.geometry), _jsonb(cand.metrics), cand.score,
                 _jsonb(cand.score_breakdown), cand.feasibility, _jsonb(cand.rejection_reasons), cand.explanation,
                 _jsonb(data), now),
            ).fetchone()
            rows.append(row)
        c.execute("UPDATE apollo_route_sessions SET status='generated',updated_at=%s WHERE id=%s", (now, session_id))
    return rows


def list_candidates(session_id: str, actor_security: SecurityContext) -> list[dict[str, Any]]:
    if not get_session(session_id, actor_security):
        raise HTTPException(404, 'route_session_not_found')
    with _connection_factory() as c:
        return c.execute('SELECT * FROM apollo_route_candidates WHERE session_id=%s ORDER BY candidate_index', (session_id,)).fetchall()


def get_candidate(session_id: str, candidate_id: str, actor_security: SecurityContext) -> dict[str, Any] | None:
    if not get_session(session_id, actor_security):
        return None
    with _connection_factory() as c:
        return c.execute('SELECT * FROM apollo_route_candidates WHERE session_id=%s AND id=%s', (session_id, candidate_id)).fetchone()


def save_windows(session_id: str, windows: Iterable[RouteWindowResult], actor_security: SecurityContext) -> list[dict[str, Any]]:
    if not get_session(session_id, actor_security):
        raise HTTPException(404, 'route_session_not_found')
    now = _now()
    rows = []
    with _connection_factory() as c:
        c.execute('DELETE FROM apollo_route_windows WHERE session_id=%s', (session_id,))
        for item in windows:
            payload = item.model_dump(mode='json')
            row = c.execute(
                '''INSERT INTO apollo_route_windows
                   (id,session_id,departure_time,candidate_id,feasibility,metrics,score,payload,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (str(uuid4()), session_id, item.departure_time, item.candidate_id, item.feasibility,
                 _jsonb(item.metrics), item.score, _jsonb(payload), now),
            ).fetchone()
            rows.append(row)
    return rows


def list_windows(session_id: str, actor_security: SecurityContext) -> list[dict[str, Any]]:
    if not get_session(session_id, actor_security):
        return []
    with _connection_factory() as c:
        return c.execute('SELECT * FROM apollo_route_windows WHERE session_id=%s ORDER BY departure_time', (session_id,)).fetchall()


def record_decision(session_id: str, candidate_id: str | None, actor_id: str, decision_type: str, rationale: str,
                    metadata: dict[str, Any], actor_security: SecurityContext) -> dict[str, Any]:
    if not get_session(session_id, actor_security):
        raise HTTPException(404, 'route_session_not_found')
    with _connection_factory() as c:
        return c.execute(
            '''INSERT INTO apollo_route_decisions
               (id,session_id,candidate_id,actor_id,decision_type,rationale,metadata,security_context,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), session_id, candidate_id, actor_id, decision_type, rationale,
             _jsonb(metadata), _jsonb(actor_security.model_dump(mode='json')), _now()),
        ).fetchone()


def list_decisions(session_id: str, actor_security: SecurityContext) -> list[dict[str, Any]]:
    if not get_session(session_id, actor_security):
        return []
    with _connection_factory() as c:
        return c.execute('SELECT * FROM apollo_route_decisions WHERE session_id=%s ORDER BY created_at', (session_id,)).fetchall()


def append_route_event(session_id: str | None, event_type: str, payload: dict[str, Any], source_system: str,
                       security_context: SecurityContext, idempotency_key: str | None = None) -> dict[str, Any]:
    with _connection_factory() as c:
        if idempotency_key:
            existing = c.execute('SELECT * FROM apollo_route_events WHERE idempotency_key=%s', (idempotency_key,)).fetchone()
            if existing:
                return existing
        return c.execute(
            '''INSERT INTO apollo_route_events
               (id,session_id,event_type,payload,source_system,security_context,idempotency_key,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), session_id, event_type, _jsonb(payload), source_system,
             _jsonb(security_context.model_dump(mode='json')), idempotency_key, _now()),
        ).fetchone()


def list_route_events(session_id: str, actor_security: SecurityContext) -> list[dict[str, Any]]:
    if not get_session(session_id, actor_security):
        return []
    with _connection_factory() as c:
        return c.execute('SELECT * FROM apollo_route_events WHERE session_id=%s ORDER BY created_at', (session_id,)).fetchall()


def save_dataset_snapshot(dataset: PlanningDataset, source_message_id: str | None = None) -> dict[str, Any]:
    security = dataset.security_context or SecurityContext(tenant_id='system', principal_id='UNG-NEXUS')
    with _connection_factory() as c:
        existing = c.execute('SELECT * FROM apollo_route_datasets WHERE dataset_id=%s AND version=%s', (dataset.dataset_id, dataset.version)).fetchone()
        if existing:
            return existing
        return c.execute(
            '''INSERT INTO apollo_route_datasets
               (id,dataset_id,version,payload,security_context,source_message_id,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), dataset.dataset_id, dataset.version, _jsonb(dataset.model_dump(mode='json')),
             _jsonb(security.model_dump(mode='json')), source_message_id, _now()),
        ).fetchone()


def get_dataset_snapshot(dataset_id: str, actor_security: SecurityContext, version: str | None = None) -> dict[str, Any] | None:
    with _connection_factory() as c:
        if version:
            row = c.execute('SELECT * FROM apollo_route_datasets WHERE dataset_id=%s AND version=%s', (dataset_id, version)).fetchone()
        else:
            row = c.execute('SELECT * FROM apollo_route_datasets WHERE dataset_id=%s ORDER BY created_at DESC LIMIT 1', (dataset_id,)).fetchone()
    if row:
        _require_security(actor_security, row['security_context'])
    return row


def upsert_vehicle_profile(profile: VehicleProfile, security_context: SecurityContext, source_message_id: str | None = None) -> dict[str, Any]:
    with _connection_factory() as c:
        existing = c.execute('SELECT * FROM apollo_route_profiles WHERE profile_id=%s AND version=%s', (profile.profile_id, profile.version)).fetchone()
        if existing:
            return existing
        return c.execute(
            '''INSERT INTO apollo_route_profiles
               (id,profile_id,version,payload,security_context,source_message_id,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (str(uuid4()), profile.profile_id, profile.version, _jsonb(profile.model_dump(mode='json')),
             _jsonb(security_context.model_dump(mode='json')), source_message_id, _now()),
        ).fetchone()


def get_vehicle_profile(profile_id: str, actor_security: SecurityContext, version: str | None = None) -> dict[str, Any] | None:
    with _connection_factory() as c:
        if version:
            row = c.execute('SELECT * FROM apollo_route_profiles WHERE profile_id=%s AND version=%s', (profile_id, version)).fetchone()
        else:
            row = c.execute('SELECT * FROM apollo_route_profiles WHERE profile_id=%s ORDER BY created_at DESC LIMIT 1', (profile_id,)).fetchone()
    if row:
        _require_security(actor_security, row['security_context'])
    return row


def list_vehicle_profiles(actor_security: SecurityContext) -> list[dict[str, Any]]:
    with _connection_factory() as c:
        rows = c.execute('SELECT * FROM apollo_route_profiles ORDER BY profile_id,created_at DESC').fetchall()
    return [row for row in rows if security_context_allows(actor_security, row['security_context'])]


def enqueue_outbox(session_id: str | None, target_system: str, message_type: str, payload: dict[str, Any],
                   security_context: SecurityContext, idempotency_key: str) -> dict[str, Any]:
    with _connection_factory() as c:
        existing = c.execute('SELECT * FROM apollo_route_outbox WHERE idempotency_key=%s', (idempotency_key,)).fetchone()
        if existing:
            return existing
        return c.execute(
            '''INSERT INTO apollo_route_outbox
               (id,session_id,target_system,message_type,payload,security_context,idempotency_key,status,attempts,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',0,%s) RETURNING *''',
            (str(uuid4()), session_id, target_system, message_type, _jsonb(payload),
             _jsonb(security_context.model_dump(mode='json')), idempotency_key, _now()),
        ).fetchone()


def list_pending_outbox(limit: int = 100) -> list[dict[str, Any]]:
    with _connection_factory() as c:
        return c.execute("SELECT * FROM apollo_route_outbox WHERE status='pending' ORDER BY created_at LIMIT %s", (limit,)).fetchall()


def mark_outbox_delivered(outbox_id: str) -> None:
    with _connection_factory() as c:
        c.execute("UPDATE apollo_route_outbox SET status='delivered',delivered_at=%s,attempts=attempts+1 WHERE id=%s", (_now(), outbox_id))


def mark_outbox_failed(outbox_id: str, error: str) -> None:
    with _connection_factory() as c:
        c.execute("UPDATE apollo_route_outbox SET attempts=attempts+1,last_error=%s WHERE id=%s", (error[:500], outbox_id))

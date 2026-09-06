from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime, timezone
import json, os, psycopg, urllib.error, urllib.request
from psycopg.rows import dict_row

app = FastAPI(title='UNG-APOLLO', version='0.3.0')
DB = os.getenv('DATABASE_URL', '')
JANUS_BASE_URL = os.getenv('JANUS_BASE_URL', 'https://ung-iam-production.up.railway.app').rstrip('/')


def conn():
    return psycopg.connect(DB, row_factory=dict_row)


def auth(permission, authorization):
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401, 'JANUS bearer token required')
    req = urllib.request.Request(
        JANUS_BASE_URL + '/v1/auth/introspect',
        data=b'',
        method='POST',
        headers={'Authorization': authorization},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HTTPException(401, 'JANUS token invalid or expired')
        raise HTTPException(503, 'JANUS authorization unavailable')
    except Exception:
        raise HTTPException(503, 'JANUS authorization unavailable')

    principal = data.get('principal') or {}
    perms = set(principal.get('permissions') or [])
    if permission not in perms and 'ung.admin' not in perms:
        raise HTTPException(403, f'Missing JANUS permission: {permission}')
    return principal


@app.on_event('startup')
def init():
    if not DB:
        return
    with conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_plans(
                id UUID PRIMARY KEY,
                title TEXT,
                objective TEXT,
                status TEXT,
                priority TEXT,
                owner_system TEXT,
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_assessments(
                id UUID PRIMARY KEY,
                plan_id UUID,
                assessment_type TEXT,
                summary TEXT,
                confidence DOUBLE PRECISION,
                created_at TIMESTAMPTZ
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_intelligence(
                id UUID PRIMARY KEY,
                plan_id UUID,
                source_system TEXT,
                intelligence_type TEXT,
                title TEXT,
                summary TEXT,
                severity TEXT,
                confidence DOUBLE PRECISION,
                observed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_scenarios(
                id UUID PRIMARY KEY,
                plan_id UUID,
                name TEXT,
                description TEXT,
                probability DOUBLE PRECISION,
                impact TEXT,
                recommended_action TEXT,
                status TEXT,
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS apollo_recommendations(
                id UUID PRIMARY KEY,
                plan_id UUID,
                title TEXT,
                rationale TEXT,
                recommended_action TEXT,
                urgency TEXT,
                status TEXT,
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            )
        ''')


class PlanIn(BaseModel):
    title: str
    objective: str
    priority: str = 'normal'
    owner_system: str = 'UNG-APOLLO'


class AssessmentIn(BaseModel):
    plan_id: str
    assessment_type: str
    summary: str
    confidence: float


class IntelligenceIn(BaseModel):
    plan_id: str
    source_system: str
    intelligence_type: str
    title: str
    summary: str
    severity: str = 'informational'
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime | None = None


class ScenarioIn(BaseModel):
    plan_id: str
    name: str
    description: str
    probability: float = Field(ge=0, le=1)
    impact: str = 'moderate'
    recommended_action: str


class RecommendationIn(BaseModel):
    plan_id: str
    title: str
    rationale: str
    recommended_action: str
    urgency: str = 'normal'


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'UNG-APOLLO', 'version': '0.3.0'}


@app.get('/ready')
def ready():
    try:
        with conn() as c:
            c.execute('SELECT 1')
        return {'status': 'ready', 'database': 'connected', 'janus': JANUS_BASE_URL}
    except Exception:
        return {'status': 'degraded', 'database': 'unavailable', 'janus': JANUS_BASE_URL}


@app.get('/v1/system')
def system():
    return {
        'system_id': 'UNG-APOLLO',
        'domain': 'planning-intelligence',
        'capabilities': [
            'plans',
            'assessments',
            'intelligence-ingestion',
            'scenario-analysis',
            'recommendations',
            'decision-briefs',
            'prioritization',
            'janus-bearer-auth',
        ],
    }


@app.get('/v1/plans')
def plans(authorization: str | None = Header(None)):
    auth('apollo.plans.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM apollo_plans ORDER BY created_at DESC').fetchall()


@app.post('/v1/plans', status_code=201)
def create_plan(b: PlanIn, authorization: str | None = Header(None)):
    auth('apollo.plans.write', authorization)
    now = datetime.now(timezone.utc)
    with conn() as c:
        return c.execute(
            'INSERT INTO apollo_plans VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
            (str(uuid4()), b.title, b.objective, 'draft', b.priority, b.owner_system, now, now),
        ).fetchone()


@app.post('/v1/plans/{plan_id}/activate')
def activate(plan_id: str, authorization: str | None = Header(None)):
    auth('apollo.plans.write', authorization)
    with conn() as c:
        row = c.execute(
            "UPDATE apollo_plans SET status='active',updated_at=%s WHERE id=%s RETURNING *",
            (datetime.now(timezone.utc), plan_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, 'plan_not_found')
        return row


@app.get('/v1/assessments')
def assessments(authorization: str | None = Header(None)):
    auth('apollo.assessments.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM apollo_assessments ORDER BY created_at DESC').fetchall()


@app.post('/v1/assessments', status_code=201)
def create_assessment(b: AssessmentIn, authorization: str | None = Header(None)):
    auth('apollo.assessments.write', authorization)
    if not 0 <= b.confidence <= 1:
        raise HTTPException(422, 'confidence_must_be_0_to_1')
    with conn() as c:
        if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (b.plan_id,)).fetchone():
            raise HTTPException(404, 'plan_not_found')
        return c.execute(
            'INSERT INTO apollo_assessments VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
            (str(uuid4()), b.plan_id, b.assessment_type, b.summary, b.confidence, datetime.now(timezone.utc)),
        ).fetchone()


@app.get('/v1/intelligence')
def intelligence(plan_id: str | None = None, authorization: str | None = Header(None)):
    auth('apollo.intelligence.read', authorization)
    with conn() as c:
        if plan_id:
            return c.execute(
                'SELECT * FROM apollo_intelligence WHERE plan_id=%s ORDER BY observed_at DESC',
                (plan_id,),
            ).fetchall()
        return c.execute('SELECT * FROM apollo_intelligence ORDER BY observed_at DESC').fetchall()


@app.post('/v1/intelligence', status_code=201)
def create_intelligence(b: IntelligenceIn, authorization: str | None = Header(None)):
    auth('apollo.intelligence.write', authorization)
    now = datetime.now(timezone.utc)
    with conn() as c:
        if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (b.plan_id,)).fetchone():
            raise HTTPException(404, 'plan_not_found')
        return c.execute(
            '''INSERT INTO apollo_intelligence
               (id,plan_id,source_system,intelligence_type,title,summary,severity,confidence,observed_at,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (
                str(uuid4()), b.plan_id, b.source_system, b.intelligence_type, b.title,
                b.summary, b.severity, b.confidence, b.observed_at or now, now,
            ),
        ).fetchone()


@app.get('/v1/scenarios')
def scenarios(plan_id: str | None = None, authorization: str | None = Header(None)):
    auth('apollo.scenarios.read', authorization)
    with conn() as c:
        if plan_id:
            return c.execute(
                'SELECT * FROM apollo_scenarios WHERE plan_id=%s ORDER BY probability DESC',
                (plan_id,),
            ).fetchall()
        return c.execute('SELECT * FROM apollo_scenarios ORDER BY probability DESC').fetchall()


@app.post('/v1/scenarios', status_code=201)
def create_scenario(b: ScenarioIn, authorization: str | None = Header(None)):
    auth('apollo.scenarios.write', authorization)
    now = datetime.now(timezone.utc)
    with conn() as c:
        if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (b.plan_id,)).fetchone():
            raise HTTPException(404, 'plan_not_found')
        return c.execute(
            '''INSERT INTO apollo_scenarios
               (id,plan_id,name,description,probability,impact,recommended_action,status,created_at,updated_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,'open',%s,%s) RETURNING *''',
            (
                str(uuid4()), b.plan_id, b.name, b.description, b.probability,
                b.impact, b.recommended_action, now, now,
            ),
        ).fetchone()


@app.post('/v1/scenarios/{scenario_id}/close')
def close_scenario(scenario_id: str, authorization: str | None = Header(None)):
    auth('apollo.scenarios.write', authorization)
    with conn() as c:
        row = c.execute(
            "UPDATE apollo_scenarios SET status='closed',updated_at=%s WHERE id=%s RETURNING *",
            (datetime.now(timezone.utc), scenario_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, 'scenario_not_found')
        return row


@app.get('/v1/recommendations')
def recommendations(plan_id: str | None = None, authorization: str | None = Header(None)):
    auth('apollo.recommendations.read', authorization)
    with conn() as c:
        if plan_id:
            return c.execute(
                'SELECT * FROM apollo_recommendations WHERE plan_id=%s ORDER BY created_at DESC',
                (plan_id,),
            ).fetchall()
        return c.execute('SELECT * FROM apollo_recommendations ORDER BY created_at DESC').fetchall()


@app.post('/v1/recommendations', status_code=201)
def create_recommendation(b: RecommendationIn, authorization: str | None = Header(None)):
    auth('apollo.recommendations.write', authorization)
    now = datetime.now(timezone.utc)
    with conn() as c:
        if not c.execute('SELECT id FROM apollo_plans WHERE id=%s', (b.plan_id,)).fetchone():
            raise HTTPException(404, 'plan_not_found')
        return c.execute(
            '''INSERT INTO apollo_recommendations
               (id,plan_id,title,rationale,recommended_action,urgency,status,created_at,updated_at)
               VALUES(%s,%s,%s,%s,%s,%s,'proposed',%s,%s) RETURNING *''',
            (str(uuid4()), b.plan_id, b.title, b.rationale, b.recommended_action, b.urgency, now, now),
        ).fetchone()


@app.post('/v1/recommendations/{recommendation_id}/accept')
def accept_recommendation(recommendation_id: str, authorization: str | None = Header(None)):
    auth('apollo.recommendations.write', authorization)
    with conn() as c:
        row = c.execute(
            "UPDATE apollo_recommendations SET status='accepted',updated_at=%s WHERE id=%s RETURNING *",
            (datetime.now(timezone.utc), recommendation_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, 'recommendation_not_found')
        return row


@app.get('/v1/plans/{plan_id}/brief')
def decision_brief(plan_id: str, authorization: str | None = Header(None)):
    auth('apollo.briefs.read', authorization)
    with conn() as c:
        plan = c.execute('SELECT * FROM apollo_plans WHERE id=%s', (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, 'plan_not_found')
        assessments = c.execute(
            'SELECT * FROM apollo_assessments WHERE plan_id=%s ORDER BY created_at DESC',
            (plan_id,),
        ).fetchall()
        intel = c.execute(
            'SELECT * FROM apollo_intelligence WHERE plan_id=%s ORDER BY observed_at DESC LIMIT 20',
            (plan_id,),
        ).fetchall()
        scenarios = c.execute(
            "SELECT * FROM apollo_scenarios WHERE plan_id=%s AND status='open' ORDER BY probability DESC",
            (plan_id,),
        ).fetchall()
        recs = c.execute(
            "SELECT * FROM apollo_recommendations WHERE plan_id=%s AND status IN ('proposed','accepted') ORDER BY created_at DESC",
            (plan_id,),
        ).fetchall()

    avg_confidence = 0.0
    confidence_values = [x['confidence'] for x in assessments] + [x['confidence'] for x in intel]
    if confidence_values:
        avg_confidence = round(sum(confidence_values) / len(confidence_values), 3)

    return {
        'plan': plan,
        'decision_context': {
            'assessment_count': len(assessments),
            'intelligence_count': len(intel),
            'open_scenarios': len(scenarios),
            'recommendations': len(recs),
            'average_confidence': avg_confidence,
        },
        'assessments': assessments,
        'intelligence': intel,
        'scenarios': scenarios,
        'recommendations': recs,
        'generated_at': datetime.now(timezone.utc),
    }


@app.get('/v1/summary')
def summary(authorization: str | None = Header(None)):
    auth('apollo.plans.read', authorization)
    with conn() as c:
        return {
            'plans': c.execute('SELECT COUNT(*) n FROM apollo_plans').fetchone()['n'],
            'active': c.execute("SELECT COUNT(*) n FROM apollo_plans WHERE status='active'").fetchone()['n'],
            'assessments': c.execute('SELECT COUNT(*) n FROM apollo_assessments').fetchone()['n'],
            'intelligence': c.execute('SELECT COUNT(*) n FROM apollo_intelligence').fetchone()['n'],
            'open_scenarios': c.execute("SELECT COUNT(*) n FROM apollo_scenarios WHERE status='open'").fetchone()['n'],
            'proposed_recommendations': c.execute("SELECT COUNT(*) n FROM apollo_recommendations WHERE status='proposed'").fetchone()['n'],
        }

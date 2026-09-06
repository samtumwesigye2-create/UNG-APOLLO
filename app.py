from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime, timezone
import os, psycopg
from psycopg.rows import dict_row

app=FastAPI(title='UNG-APOLLO',version='0.1.0')
DB=os.getenv('DATABASE_URL','')
def conn(): return psycopg.connect(DB,row_factory=dict_row)
def auth(permission, header):
    perms={x.strip() for x in (header or '').split(',')}
    if permission not in perms and 'ung.admin' not in perms: raise HTTPException(403,'JANUS permission required')
@app.on_event('startup')
def init():
    if DB:
        with conn() as c:
            c.execute('CREATE TABLE IF NOT EXISTS apollo_plans(id UUID PRIMARY KEY,title TEXT,objective TEXT,status TEXT,priority TEXT,owner_system TEXT,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS apollo_assessments(id UUID PRIMARY KEY,plan_id UUID,assessment_type TEXT,summary TEXT,confidence DOUBLE PRECISION,created_at TIMESTAMPTZ)')
class PlanIn(BaseModel): title:str; objective:str; priority:str='normal'; owner_system:str='UNG-APOLLO'
class AssessmentIn(BaseModel): plan_id:str; assessment_type:str; summary:str; confidence:float
@app.get('/health')
def health(): return {'status':'ok','service':'UNG-APOLLO','version':'0.1.0'}
@app.get('/ready')
def ready():
    try:
        with conn() as c:c.execute('SELECT 1')
        return {'status':'ready','database':'connected'}
    except Exception:return {'status':'degraded','database':'unavailable'}
@app.get('/v1/system')
def system(): return {'system_id':'UNG-APOLLO','domain':'planning-intelligence','capabilities':['plans','assessments','prioritization','decision-support']}
@app.get('/v1/plans')
def plans(x_ung_permissions:str|None=Header(None)):
    auth('apollo.plans.read',x_ung_permissions)
    with conn() as c:return c.execute('SELECT * FROM apollo_plans ORDER BY created_at DESC').fetchall()
@app.post('/v1/plans',status_code=201)
def create_plan(b:PlanIn,x_ung_permissions:str|None=Header(None)):
    auth('apollo.plans.write',x_ung_permissions); now=datetime.now(timezone.utc)
    with conn() as c:return c.execute('INSERT INTO apollo_plans VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.title,b.objective,'draft',b.priority,b.owner_system,now,now)).fetchone()
@app.post('/v1/plans/{plan_id}/activate')
def activate(plan_id:str,x_ung_permissions:str|None=Header(None)):
    auth('apollo.plans.write',x_ung_permissions)
    with conn() as c:
        row=c.execute("UPDATE apollo_plans SET status='active',updated_at=%s WHERE id=%s RETURNING *",(datetime.now(timezone.utc),plan_id)).fetchone()
        if not row: raise HTTPException(404,'plan_not_found')
        return row
@app.get('/v1/assessments')
def assessments(x_ung_permissions:str|None=Header(None)):
    auth('apollo.assessments.read',x_ung_permissions)
    with conn() as c:return c.execute('SELECT * FROM apollo_assessments ORDER BY created_at DESC').fetchall()
@app.post('/v1/assessments',status_code=201)
def create_assessment(b:AssessmentIn,x_ung_permissions:str|None=Header(None)):
    auth('apollo.assessments.write',x_ung_permissions)
    if not 0 <= b.confidence <= 1: raise HTTPException(422,'confidence_must_be_0_to_1')
    with conn() as c:
        if not c.execute('SELECT id FROM apollo_plans WHERE id=%s',(b.plan_id,)).fetchone(): raise HTTPException(404,'plan_not_found')
        return c.execute('INSERT INTO apollo_assessments VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.plan_id,b.assessment_type,b.summary,b.confidence,datetime.now(timezone.utc))).fetchone()
@app.get('/v1/summary')
def summary(x_ung_permissions:str|None=Header(None)):
    auth('apollo.plans.read',x_ung_permissions)
    with conn() as c:return {'plans':c.execute('SELECT COUNT(*) n FROM apollo_plans').fetchone()['n'],'active':c.execute("SELECT COUNT(*) n FROM apollo_plans WHERE status='active'").fetchone()['n'],'assessments':c.execute('SELECT COUNT(*) n FROM apollo_assessments').fetchone()['n']}

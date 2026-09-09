from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
import json, os, urllib.error, urllib.request
from app import auth, conn

router=APIRouter(prefix='/v1/kpis',tags=['Planning KPIs'])
NOVA_BASE_URL=os.getenv('NOVA_BASE_URL','https://ung-nova-production.up.railway.app').rstrip('/')
def now(): return datetime.now(timezone.utc)
def ensure_schema():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS apollo_forecast_observations(id UUID PRIMARY KEY,entity_id TEXT NOT NULL,forecast_value DOUBLE PRECISION NOT NULL,actual_value DOUBLE PRECISION NOT NULL,planned_supply DOUBLE PRECISION NULL,actual_supply DOUBLE PRECISION NULL,measured_at TIMESTAMPTZ NOT NULL)''')
class ForecastIn(BaseModel):
    entity_id:str=Field(min_length=1,max_length=120);forecast_value:float;actual_value:float;planned_supply:float|None=None;actual_supply:float|None=None;measured_at:datetime|None=None
@router.post('/forecast-observations',status_code=201)
def record(b:ForecastIn,authorization:str|None=Header(None)):
    auth('apollo.assessments.write',authorization);ensure_schema()
    with conn() as c:return c.execute('INSERT INTO apollo_forecast_observations VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.entity_id,b.forecast_value,b.actual_value,b.planned_supply,b.actual_supply,b.measured_at or now())).fetchone()
@router.get('/supply-chain')
def snapshot(authorization:str|None=Header(None)):
    auth('apollo.assessments.read',authorization);ensure_schema()
    with conn() as c: rows=c.execute('SELECT * FROM apollo_forecast_observations ORDER BY measured_at DESC LIMIT 1000').fetchall()
    if not rows:return {'source_system':'UNG-APOLLO','observations':[],'status':'no-data','generated_at':now()}
    actual=sum(abs(float(r['actual_value'])) for r in rows); abs_err=sum(abs(float(r['forecast_value'])-float(r['actual_value'])) for r in rows); signed_err=sum(float(r['forecast_value'])-float(r['actual_value']) for r in rows)
    obs=[]
    if actual>0:
        obs.append({'kpi_key':'forecast_accuracy','value':max(0.0,100*(1-abs_err/actual)),'entity_id':'enterprise','source_system':'UNG-APOLLO'})
        obs.append({'kpi_key':'forecast_bias','value':100*signed_err/actual,'entity_id':'enterprise','source_system':'UNG-APOLLO'})
    supply=[r for r in rows if r['planned_supply'] is not None and r['actual_supply'] is not None]
    planned=sum(abs(float(r['planned_supply'])) for r in supply)
    if planned>0: obs.append({'kpi_key':'supply_plan_adherence','value':max(0.0,100*(1-sum(abs(float(r['actual_supply'])-float(r['planned_supply'])) for r in supply)/planned)),'entity_id':'enterprise','source_system':'UNG-APOLLO'})
    return {'source_system':'UNG-APOLLO','records':len(rows),'observations':obs,'generated_at':now()}
@router.post('/supply-chain/publish')
def publish(authorization:str|None=Header(None)):
    snap=snapshot(authorization)
    observations=snap.get('observations') or []
    if not observations:return {'status':'no-data','inserted':0,'snapshot':snap}
    req=urllib.request.Request(NOVA_BASE_URL+'/v1/supply-chain/observations/bulk',data=json.dumps({'observations':observations},default=str).encode(),method='POST',headers={'Content-Type':'application/json','X-UNG-Permissions':'nova.datasets.write','User-Agent':'UNG-APOLLO/0.4.1'})
    try:
        with urllib.request.urlopen(req,timeout=8) as r:return {'status':'published','response_code':r.status,'nova':json.loads(r.read().decode() or '{}'),'snapshot':snap}
    except urllib.error.HTTPError as e: raise HTTPException(502,f'nova_http_{e.code}')
    except Exception as e: raise HTTPException(503,f'nova_unavailable:{type(e).__name__}')

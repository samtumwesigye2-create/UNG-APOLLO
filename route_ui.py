from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from route_api import route_feature_enabled

router=APIRouter(tags=['Route Planning UI'])
ROOT=Path(__file__).resolve().parent/'web'/'routes'
ASSETS={'styles.css','app.js'}
JANUS_BASE_URL=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')
COOKIE_NAME='apollo_janus_session'


class WorkspaceLogin(BaseModel):
    email: str=Field(min_length=3,max_length=320)
    password: str=Field(min_length=1,max_length=1024)


def _janus_json(path: str, *, method: str='GET', body: dict[str, Any] | None=None, token: str | None=None) -> dict[str, Any]:
    headers={'Accept':'application/json','User-Agent':'UNG-APOLLO/0.6'}
    data=None
    if body is not None:
        headers['Content-Type']='application/json'
        data=json.dumps(body).encode()
    if token:
        headers['Authorization']=f'Bearer {token}'
    req=urllib.request.Request(JANUS_BASE_URL+path,data=data,method=method,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=8) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        detail=''
        try:
            detail=exc.read().decode(errors='replace')[:300]
        except Exception:
            pass
        if exc.code in (401,403):
            raise HTTPException(exc.code,'JANUS credentials rejected') from None
        raise HTTPException(503,f'JANUS unavailable ({exc.code})') from None
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,f'JANUS unavailable ({type(exc).__name__})') from None


@router.get('/routes',response_class=HTMLResponse)
def route_workspace():
    if not route_feature_enabled(): raise HTTPException(404,'route_planning_feature_disabled')
    return HTMLResponse((ROOT/'index.html').read_text(encoding='utf-8'))


@router.get('/routes/assets/{name}')
def route_asset(name:str):
    if not route_feature_enabled(): raise HTTPException(404,'route_planning_feature_disabled')
    if name not in ASSETS: raise HTTPException(404,'asset_not_found')
    path=ROOT/name
    return Response(path.read_bytes(),media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')


@router.post('/routes/auth/login')
def workspace_login(body: WorkspaceLogin):
    if not route_feature_enabled(): raise HTTPException(404,'route_planning_feature_disabled')
    result=_janus_json('/v1/auth/login',method='POST',body={'email':body.email.strip().lower(),'password':body.password})
    token=str(result.get('access_token') or '')
    identity=result.get('identity') or {}
    if not token:
        raise HTTPException(502,'JANUS login response missing session token')
    response=JSONResponse({'authenticated':True,'identity':identity,'expires_in':result.get('expires_in')})
    response.set_cookie(COOKIE_NAME,token,max_age=int(result.get('expires_in') or 28800),httponly=True,secure=True,samesite='lax',path='/')
    return response


@router.get('/routes/auth/session')
def workspace_session(apollo_janus_session: str | None=Cookie(default=None)):
    if not route_feature_enabled(): raise HTTPException(404,'route_planning_feature_disabled')
    if not apollo_janus_session:
        return {'authenticated':False,'identity':None}
    try:
        identity=_janus_json('/v1/me',token=apollo_janus_session)
    except HTTPException as exc:
        if exc.status_code in (401,403):
            response=JSONResponse({'authenticated':False,'identity':None},status_code=200)
            response.delete_cookie(COOKIE_NAME,path='/')
            return response
        raise
    return {'authenticated':True,'identity':identity}


@router.post('/routes/auth/logout')
def workspace_logout(apollo_janus_session: str | None=Cookie(default=None)):
    if apollo_janus_session:
        try:
            _janus_json('/v1/auth/logout',method='POST',token=apollo_janus_session)
        except HTTPException:
            pass
    response=JSONResponse({'authenticated':False})
    response.delete_cookie(COOKIE_NAME,path='/')
    return response

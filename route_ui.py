from __future__ import annotations

import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from route_api import route_feature_enabled

router=APIRouter(tags=['Route Planning UI'])
ROOT=Path(__file__).resolve().parent/'web'/'routes'
ASSETS={'styles.css','app.js'}

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

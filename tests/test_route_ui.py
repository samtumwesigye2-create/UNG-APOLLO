from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import route_ui


def test_workspace_feature_flag_disabled(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','0')
    app=FastAPI(); app.include_router(route_ui.router)
    assert TestClient(app).get('/routes').status_code==404


def test_workspace_contains_plan_compare_replay_and_explainability(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','1')
    app=FastAPI(); app.include_router(route_ui.router)
    r=TestClient(app).get('/routes')
    assert r.status_code==200
    for text in ['Plan','Compare','Replay','WHY THIS ROUTE?','Terrain','Weather','Geofences','Comms','Departure windows']:
        assert text in r.text


def test_ui_assets_support_geojson_routes_and_touch_responsive_layout():
    js=Path('web/routes/app.js').read_text(); css=Path('web/routes/styles.css').read_text()
    assert 'LineString' in js and '/v1/routes/sessions' in js and 'pointerdown' in js
    assert '@media' in css and 'touch-action' in css


def test_workspace_uses_signin_not_raw_token_and_has_real_resource_selectors(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','1')
    app=FastAPI(); app.include_router(route_ui.router)
    html=TestClient(app).get('/routes').text
    assert 'id="loginEmail"' in html
    assert 'id="loginPassword"' in html
    assert 'id="loginButton"' in html
    assert 'id="token"' not in html
    assert '<select id="planId"' in html
    assert '<select id="profileId"' in html
    assert '<select id="datasetId"' in html
    assert 'id="compareGrid"' in html


def test_route_login_sets_secure_httponly_cookie_without_returning_token(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','1')
    monkeypatch.setattr(route_ui, '_janus_json', lambda *a, **k: {
        'access_token':'iam_secret_value','expires_in':3600,
        'identity':{'id':'p1','display_name':'Operator'}
    })
    app=FastAPI(); app.include_router(route_ui.router); client=TestClient(app)
    r=client.post('/routes/auth/login',json={'email':'operator@example.org','password':'correct horse battery staple'})
    assert r.status_code==200
    assert r.json()['authenticated'] is True
    assert 'access_token' not in r.json()
    cookie=r.headers.get('set-cookie','')
    assert 'apollo_janus_session=' in cookie
    assert 'HttpOnly' in cookie
    assert 'Secure' in cookie


def test_route_session_endpoint_reads_identity_through_janus(monkeypatch):
    monkeypatch.setenv('APOLLO_ROUTE_PLANNING_ENABLED','1')
    monkeypatch.setattr(route_ui, '_janus_json', lambda *a, **k: {'id':'p1','display_name':'Operator'})
    app=FastAPI(); app.include_router(route_ui.router); client=TestClient(app)
    client.cookies.set('apollo_janus_session','iam_x')
    r=client.get('/routes/auth/session')
    assert r.status_code==200
    assert r.json()['authenticated'] is True
    assert r.json()['identity']['id']=='p1'

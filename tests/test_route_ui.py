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

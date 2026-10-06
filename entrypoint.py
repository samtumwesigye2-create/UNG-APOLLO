import app as app_module
from app import app
from nexus_bridge import router as nexus_router
from planning_kpis import router as planning_kpis_router
from route_api import route_feature_enabled, route_storage_status, router as route_router
from route_ui import router as route_ui_router
from route_store import ensure_route_schema

app.include_router(nexus_router)
app.include_router(planning_kpis_router)
app.include_router(route_router)
app.include_router(route_ui_router)

@app.on_event('startup')
def init_route_planning():
    if route_feature_enabled() and app_module.DB:
        ensure_route_schema()

_original_system = app_module.system
_original_ready = app_module.ready

def _system_with_route_capability():
    payload = _original_system()
    capabilities = list(payload.get('capabilities') or [])
    if route_feature_enabled() and 'route-planning' not in capabilities:
        capabilities.append('route-planning')
    payload['capabilities'] = capabilities
    return payload

def _ready_with_route_status():
    payload = _original_ready()
    if route_feature_enabled():
        payload['route_planning'] = route_storage_status()
        if payload['route_planning'].get('status') != 'ready':
            payload['status'] = 'degraded'
    return payload

for _route in app.routes:
    if getattr(_route, 'path', None) == '/v1/system':
        _route.endpoint = _system_with_route_capability
        if getattr(_route, 'dependant', None) is not None:
            _route.dependant.call = _system_with_route_capability
    elif getattr(_route, 'path', None) == '/ready':
        _route.endpoint = _ready_with_route_status
        if getattr(_route, 'dependant', None) is not None:
            _route.dependant.call = _ready_with_route_status

@app.get('/')
def root_status():
    return {
        'service': 'UNG-APOLLO',
        'name': 'Uganda National Grid Planning & Intelligence Platform',
        'status': 'online',
        'version': '0.5.0',
        'health': '/health',
        'readiness': '/ready',
        'system': '/v1/system',
        'nexus': '/v1/nexus/status',
        'routes': '/v1/routes/profiles' if route_feature_enabled() else None,
        'route_workspace': '/routes' if route_feature_enabled() else None,
        'docs': '/docs',
    }

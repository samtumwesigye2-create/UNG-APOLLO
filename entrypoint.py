import app as app_module
from app import app
from nexus_bridge import router as nexus_router
from planning_kpis import router as planning_kpis_router


@app.get('/')
def root_status():
    return {
        'service': 'UNG-APOLLO',
        'name': 'Uganda National Grid Planning & Intelligence Platform',
        'status': 'online',

        'health': '/health',
        'readiness': '/ready',
        'system': '/v1/system',
        'nexus': '/v1/nexus/status',
        'routes': '/v1/routes/profiles' if route_feature_enabled() else None,
        'route_workspace': '/routes' if route_feature_enabled() else None,
        'docs': '/docs',
    }

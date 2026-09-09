from app import app
from nexus_bridge import router as nexus_router
from planning_kpis import router as planning_kpis_router

app.include_router(nexus_router)
app.include_router(planning_kpis_router)

@app.get('/')
def root_status():
    return {
        'service': 'UNG-APOLLO',
        'name': 'Uganda National Grid Planning & Intelligence Platform',
        'status': 'online',
        'version': '0.4.1',
        'health': '/health',
        'readiness': '/ready',
        'system': '/v1/system',
        'nexus': '/v1/nexus/status',
        'docs': '/docs',
    }

from app import app

@app.get('/')
def root_status():
    return {
        'service': 'UNG-APOLLO',
        'name': 'Uganda National Grid Planning & Intelligence Platform',
        'status': 'online',
        'version': '0.3.0',
        'health': '/health',
        'readiness': '/ready',
        'system': '/v1/system',
        'docs': '/docs',
    }

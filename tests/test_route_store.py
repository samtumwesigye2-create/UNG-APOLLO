from route_models import SecurityContext
from route_store import ensure_route_schema, security_context_allows


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
    def fetchone(self):
        return self.row
    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.sql = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        return Result()


def test_schema_contains_required_route_tables_and_indexes(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr('route_store._connection_factory', lambda: fake)
    ensure_route_schema()
    joined = "\n".join(s for s, _ in fake.sql)
    for table in [
        'apollo_route_sessions', 'apollo_route_candidates', 'apollo_route_windows',
        'apollo_route_decisions', 'apollo_route_events', 'apollo_route_datasets',
        'apollo_route_profiles', 'apollo_route_outbox'
    ]:
        assert table in joined
    for marker in ['plan_id', 'session_id', 'created_at', 'candidate_index', 'dataset_id', 'profile_id', 'idempotency_key']:
        assert marker in joined


def ctx(tenant='t1', classification='UNCLASSIFIED', compartments=None, principal='p1'):
    return SecurityContext(
        tenant_id=tenant,
        principal_id=principal,
        classification=classification,
        compartments=compartments or [],
    )


def test_cross_tenant_session_read_denied():
    assert not security_context_allows(ctx('t2'), ctx('t1'))


def test_compartment_mismatch_denied():
    stored = ctx(compartments=['ALPHA'])
    actor = ctx(compartments=['BRAVO'])
    assert not security_context_allows(actor, stored)


def test_classification_mismatch_denied():
    stored = ctx(classification='SECRET')
    actor = ctx(classification='UNCLASSIFIED')
    assert not security_context_allows(actor, stored)


def test_higher_classification_and_required_compartment_allowed():
    stored = ctx(classification='CONFIDENTIAL', compartments=['ALPHA'])
    actor = ctx(classification='SECRET', compartments=['ALPHA', 'BRAVO'])
    assert security_context_allows(actor, stored)

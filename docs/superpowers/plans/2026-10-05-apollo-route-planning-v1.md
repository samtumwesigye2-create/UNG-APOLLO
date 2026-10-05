# APOLLO Route Planning V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, explainable, non-weapon geospatial route-planning workspace to the existing UNG-APOLLO service, including secure persistence, route generation, departure-window analysis, replay, NEXUS/SENTINEL integration, and a map-first operator UI.

**Architecture:** Keep APOLLO authoritative for planning and reuse the existing FastAPI/PostgreSQL/JANUS/NEXUS patterns. Route generation is a pure deterministic graph engine separated from scoring; persistence stores normalized inputs, security context, data provenance, candidate outputs, decisions, and replay material. The browser UI consumes GeoJSON-like API payloads and remains usable with locally available data when upstream services are unavailable.

**Tech Stack:** Python 3.12, FastAPI 0.116.1, Pydantic 2.11.7, psycopg 3.2.9/PostgreSQL, vanilla HTML/CSS/JavaScript, pytest/httpx for tests.

**Spec:** `docs/superpowers/specs/2026-10-05-apollo-route-planning-design.md`

## Global Constraints

- V1 supports benign/non-weapon mission planning only; no target engagement, weapon release, hostile-force optimization, strike routing, autonomous attack behavior, direct autopilot command/control, or real-time vehicle piloting.
- APOLLO owns route planning; do not duplicate JANUS, NEXUS, SENTINEL, ATLAS, identity, audit, integration, or replication subsystems.
- All protected route endpoints fail closed when JANUS authorization is unavailable.
- Identical normalized request + engine version + profile/policy version + data snapshots must reproduce candidate ordering and score decomposition within defined floating-point tolerance.
- Hard constraints are pass/fail and cannot be outweighed by soft scoring.
- Missing required map/graph data must fail clearly; never fabricate a route.
- Missing optional layers may degrade confidence only when policy allows and must be identified in the response.
- Route records inherit/store tenant, organization, classification, compartments, explicit sharing policy, and principal ID.
- Offline/degraded planning must expose data freshness and preserve human decision history through later synchronization.
- The workspace stays behind `APOLLO_ROUTE_PLANNING_ENABLED`; default is disabled until migrations, permissions, and acceptance tests pass.

## Review Focus

- Invalid coordinates, non-finite numeric weights, zero/negative vehicle limits, or an inverted departure window must return validation errors instead of entering the engine.
- A principal whose tenant/classification/compartment context does not satisfy the session security label must be denied even when the principal has the route permission string.
- Required graph data missing must block generation; optional weather/terrain/comms data missing or stale must produce explicit degraded confidence and freshness metadata.
- Duplicate NEXUS snapshot/event message IDs must be idempotent and must not duplicate route datasets, audit events, candidates, or decisions.
- Offline decision synchronization must never replace a human decision with a later system recommendation or silently overwrite an earlier operator rationale.

---

## File Structure

- `route_models.py` — Pydantic request/response, security, graph, profile, candidate, window, replay models.
- `route_store.py` — route tables, persistence, security-filtered reads/writes, event/outbox storage.
- `route_engine.py` — deterministic graph candidate generation, hard constraints, scoring, tie-breaking, explanations.
- `route_windows.py` — departure-window evaluation using stored candidates/data snapshots.
- `route_replay.py` — replay bundle construction and reproducibility checks.
- `route_events.py` — NEXUS/SENTINEL event envelopes, durable outbox, retry-safe publishing.
- `route_api.py` — `/v1/routes/*` FastAPI router and permission enforcement.
- `route_ui.py` — static workspace route and feature-flag guard.
- `web/routes/index.html`, `web/routes/styles.css`, `web/routes/app.js` — Plan/Compare/Replay browser workspace.
- `tests/` — unit, API, integration-contract, replay, security, and UI smoke tests.
- `requirements-dev.txt` — pytest/httpx only; runtime requirements remain minimal.
- Modify `app.py`, `entrypoint.py`, `nexus_bridge.py`, `.github/workflows/ci.yml` only where integration requires it.

### Task 1: Test harness, route domain models, profiles, and feature flag

**Files:**
- Create: `requirements-dev.txt`
- Create: `route_models.py`
- Create: `tests/test_route_models.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `Coordinate`, `SecurityContext`, `VehicleProfile`, `PreferenceWeights`, `HardConstraints`, `PlanningWindow`, `GraphNode`, `GraphEdge`, `PlanningDataset`, `RouteSessionCreate`, `RouteCandidate`, `RouteWindowResult`, `RouteReplayBundle`.
- Produces: `ROUTE_ENGINE_VERSION = "1.0.0"`, `ROUTE_POLICY_VERSION = "1.0.0"`.

- [ ] **Step 1: Write failing model-validation tests**

Add tests named `test_coordinate_rejects_out_of_range`, `test_weights_reject_nan_and_negative`, `test_vehicle_profile_rejects_non_positive_limits`, `test_planning_window_rejects_inverted_range`, and `test_security_context_requires_tenant_and_principal`.

- [ ] **Step 2: Run the tests and verify failure**

Run: `python -m pytest tests/test_route_models.py -v`
Expected: FAIL because `route_models` does not exist.

- [ ] **Step 3: Implement the Pydantic models**

Use strict finite numeric validation. `VehicleProfile.mode` is `Literal["ground","air","surface","simulation"]`; route planning remains mode-agnostic and non-weapon. `PreferenceWeights.normalized()` returns a deterministic dictionary whose enabled weights sum to 1.0. `PlanningDataset` includes `dataset_id`, `version`, `source_system`, `observed_at`, `fetched_at`, `required_graph`, optional layer provenance, nodes, and edges.

- [ ] **Step 4: Add test dependencies and CI test execution**

`requirements-dev.txt` contains `-r requirements.txt`, `pytest==8.4.2`, and `httpx==0.28.1`. Update CI to install it, compile all route modules, and run `python -m pytest -q`.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_models.py -v`
Expected: PASS.

Commit: `test: establish APOLLO route planning domain models`

### Task 2: Route persistence and security-scoped storage

**Files:**
- Create: `route_store.py`
- Create: `tests/test_route_store.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: models from Task 1 and existing `app.conn`.
- Produces: `ensure_route_schema() -> None`, `create_session(...)`, `get_session(...)`, `save_candidates(...)`, `list_candidates(...)`, `save_windows(...)`, `record_decision(...)`, `append_route_event(...)`, `save_dataset_snapshot(...)`, `get_dataset_snapshot(...)`, `enqueue_outbox(...)`, `list_pending_outbox(...)`, `mark_outbox_delivered(...)`.

- [ ] **Step 1: Write failing schema/store tests**

Tests pin the five spec tables exactly: `apollo_route_sessions`, `apollo_route_candidates`, `apollo_route_windows`, `apollo_route_decisions`, `apollo_route_events`; add `apollo_route_datasets` for immutable normalized snapshot storage and `apollo_route_outbox` for durable NEXUS/SENTINEL delivery. Test indexes for `plan_id`, `session_id`, `created_at`, candidate lookup, dataset version, and unique outbox/event idempotency key.

- [ ] **Step 2: Add security mismatch tests**

`test_cross_tenant_session_read_denied`, `test_compartment_mismatch_denied`, and `test_classification_mismatch_denied` must prove store reads/writes require both route permission at API level and record-level security compatibility.

- [ ] **Step 3: Implement schema and store functions**

Persist `security_context JSONB` with sessions, datasets, decisions, and route events. Candidate/window access is authorized through the parent session security context. Do not mutate prior decisions; `record_decision` is append-only.

- [ ] **Step 4: Wire schema startup**

Call `ensure_route_schema()` from the existing FastAPI startup initialization only when `DATABASE_URL` is configured. Database errors must surface as degraded readiness/write failures, never fake success.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_store.py -v`
Expected: PASS.

Commit: `feat: add secure route planning persistence`

### Task 3: Deterministic graph engine, hard constraints, ranking, and explanations

**Files:**
- Create: `route_engine.py`
- Create: `tests/fixtures/route_graph.json`
- Create: `tests/test_route_engine.py`

**Interfaces:**
- Consumes: `RouteSessionCreate`, `PlanningDataset`, `VehicleProfile`, `PreferenceWeights`.
- Produces: `generate_candidates(request: RouteSessionCreate, dataset: PlanningDataset, profile: VehicleProfile, max_alternatives: int) -> list[RouteCandidate]`.
- Produces: `score_candidate(...) -> RouteCandidate`, `evaluate_hard_constraints(...) -> list[str]`, `explain_candidate(...) -> str`.

- [ ] **Step 1: Write failing deterministic-routing tests**

Fixture graph must contain at least three distinct origin-to-destination paths. Tests assert at least two alternatives, stable candidate IDs/order for identical inputs, deterministic tie-breaking by canonical node sequence, and repeatable score components to `1e-9` tolerance.

- [ ] **Step 2: Write hard-constraint tests**

Cover forbidden geofence edge, vehicle max range/endurance, route-mode mismatch, required-waypoint ordering, and blocked/unavailable edge. Assert an infeasible path cannot rank above a feasible path regardless of soft weights.

- [ ] **Step 3: Implement candidate generation**

Use a deterministic k-shortest-path approach over the normalized graph: shortest path for the first candidate and deterministic deviation paths for subsequent candidates. Sort adjacency by stable edge ID/node ID; never use set iteration as an ordering source.

- [ ] **Step 4: Implement scoring and explanation**

Expose distance, ETA, energy/fuel estimate, terrain cost, weather cost, communications quality/cost, uncertainty/freshness penalty, data-quality state, hard-constraint results, composite score, and an explanation that identifies strongest positive/negative factors and missing/stale layers. Required graph absent raises `RouteDataUnavailable` instead of synthesizing geometry.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_engine.py -v`
Expected: PASS.

Commit: `feat: add deterministic explainable route engine`

### Task 4: Route session API and JANUS authorization

**Files:**
- Create: `route_api.py`
- Create: `tests/test_route_api.py`
- Modify: `entrypoint.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: existing `app.auth`, Task 2 store, Task 3 engine.
- Produces endpoints:
  - `POST /v1/routes/sessions`
  - `GET /v1/routes/sessions/{id}`
  - `POST /v1/routes/sessions/{id}/generate`
  - `GET /v1/routes/sessions/{id}/candidates`
  - `GET /v1/routes/sessions/{id}/candidates/{candidate_id}`
  - `GET /v1/routes/profiles`
- Permissions: `apollo.routes.read`, `apollo.routes.write`, `apollo.routes.generate`.

- [ ] **Step 1: Write failing API permission/lifecycle tests**

Patch JANUS introspection in tests. Assert missing token → 401; missing permission → 403; JANUS unavailable → 503; valid token/session creation → 201; unknown plan/session/candidate → 404. Assert `ung.admin` retains the existing fallback behavior.

- [ ] **Step 2: Write generation contract tests**

Assert candidate payload contains geometry, metrics, score, score breakdown, feasibility, rejection reasons, explanation, data-quality/freshness metadata, engine/profile/policy versions, and provenance. Required dataset missing must return 409/422 with `required_route_dataset_unavailable` and no persisted candidates.

- [ ] **Step 3: Implement router and feature flag**

`router = APIRouter(prefix='/v1/routes', tags=['Route Planning'])`. Reject route writes/generation with 404 or 503-style feature-disabled response while `APOLLO_ROUTE_PLANNING_ENABLED` is not truthy; keep health/readiness available.

- [ ] **Step 4: Register router and system capability**

Include the route router in `entrypoint.py`; add `route-planning` to `/v1/system` capabilities only when the flag is enabled.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_api.py -v`
Expected: PASS.

Commit: `feat: expose secured APOLLO route planning API`

### Task 5: NEXUS snapshot ingestion, provenance, audit events, and durable outbox

**Files:**
- Create: `route_events.py`
- Create: `tests/test_route_events.py`
- Modify: `nexus_bridge.py`

**Interfaces:**
- Consumes: Task 2 dataset/outbox store and existing `NexusEnvelope`/NEXUS endpoint pattern.
- Produces: `ingest_route_snapshot(message_type, payload, message_id, security_context)`, `queue_route_event(session_id, event_type, payload, security_context, idempotency_key)`, `flush_route_outbox(authorization: str | None) -> dict`.

- [ ] **Step 1: Write failing inbound/idempotency tests**

Cover `APOLLO.ROUTE.WEATHER_SNAPSHOT`, `TERRAIN_SNAPSHOT`, `GEOFENCE_SNAPSHOT`, `COMMS_SNAPSHOT`, and `VEHICLE_PROFILE_UPDATED`. Every stored datum must retain source, observed/fetched time, version/hash when supplied, quality/confidence, and security context. Duplicate `message_id` must return duplicate=true without a second stored snapshot/event.

- [ ] **Step 2: Write outbox/degraded-connectivity tests**

When NEXUS is unavailable, `SESSION_CREATED`, `CANDIDATES_GENERATED`, `DECISION_RECORDED`, and `REPLAY_REQUESTED` remain queued. A later successful flush marks delivery without deleting local history. Test that authorization headers are never persisted or logged.

- [ ] **Step 3: Refactor NEXUS publish primitive**

Extract a reusable `publish_envelope(envelope: dict, authorization: str | None) -> dict` from the existing `/v1/nexus/publish` implementation; preserve current public behavior.

- [ ] **Step 4: Implement route snapshot dispatch and audit queueing**

Route-relevant inbound messages call `ingest_route_snapshot`. Security/operator events are persisted to `apollo_route_events` and queued toward the existing NEXUS integration path for SENTINEL consumption; distinguish `system_recommendation` from `human_decision` in event payloads.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_events.py -v`
Expected: PASS.

Commit: `feat: integrate route planning with NEXUS audit flow`

### Task 6: Departure windows, human decisions, and deterministic replay

**Files:**
- Create: `route_windows.py`
- Create: `route_replay.py`
- Create: `tests/test_route_windows.py`
- Create: `tests/test_route_replay.py`
- Modify: `route_api.py`

**Interfaces:**
- Produces: `evaluate_departure_windows(session, candidates, dataset, interval_minutes: int) -> list[RouteWindowResult]`.
- Produces: `build_replay_bundle(session_id: str, security_context: SecurityContext) -> RouteReplayBundle`.
- Adds endpoints:
  - `POST /v1/routes/sessions/{id}/windows`
  - `POST /v1/routes/sessions/{id}/decisions`
  - `GET /v1/routes/sessions/{id}/replay`
- Permissions: `apollo.routes.decide`, `apollo.routes.replay`.

- [ ] **Step 1: Write window-analysis tests**

Evaluate from earliest to latest departure at the requested interval. Assert each record stores departure time, data version/time, feasibility, candidate ID, score, ETA, energy estimate, weather margin, confidence, and freshness. Recommended window is the best feasible interval with deterministic tie-breaking.

- [ ] **Step 2: Write human-decision tests**

Allow decision types `select`, `reject`, and `note`; require nonblank rationale for `select`/`reject`. If the operator selects a non-top-ranked route, append a manual-override event and preserve the system top-ranked candidate unchanged.

- [ ] **Step 3: Write replay determinism tests**

Bundle includes normalized request, security label, engine/profile/policy versions, exact data snapshot identifiers/hashes, candidate geometries, score breakdowns, hard-constraint results, windows, events, and operator decisions. If a referenced immutable data snapshot is missing, set `replay_mode="approximate"` and list the missing snapshot IDs; never silently substitute current data.

- [ ] **Step 4: Implement windows/replay and API routes**

Replay reads stored outputs first and may optionally recompute for verification; it never alters the historical record.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_windows.py tests/test_route_replay.py -v`
Expected: PASS.

Commit: `feat: add route windows decisions and replay`

### Task 7: APOLLO Plan / Compare / Replay workspace

**Files:**
- Create: `route_ui.py`
- Create: `web/routes/index.html`
- Create: `web/routes/styles.css`
- Create: `web/routes/app.js`
- Create: `tests/test_route_ui.py`
- Modify: `entrypoint.py`

**Interfaces:**
- Produces: `GET /routes` operator workspace, served only when the feature flag is enabled.
- Browser consumes only `/v1/routes/*` JSON/GeoJSON-like structures; backend has no dependency on a particular map renderer.

- [ ] **Step 1: Write failing UI smoke tests**

Assert `/routes` is feature-flag guarded, returns the APOLLO route shell when enabled, and contains `Plan`, `Compare`, `Replay`, `WHY THIS ROUTE?`, layer controls, timeline, and a route comparison region.

- [ ] **Step 2: Implement dark responsive shell**

Desktop-first grid: top mission bar, dominant map canvas, right candidate panel, collapsible layer drawer, expandable bottom timeline, and explanation panel. Tablet fallback must not hide candidate explanations or decision controls.

- [ ] **Step 3: Implement map interaction without mandatory external renderer**

Use an SVG/Canvas coordinate surface for always-available local interaction and GeoJSON-like route rendering. If `APOLLO_BASEMAP_URL` is configured, the UI may use it as a background layer; absence of a basemap must not disable waypoint editing or route comparison. Support pointer/touch drag for origin/destination/intermediate waypoints and keyboard-focusable controls.

- [ ] **Step 4: Implement Plan / Compare / Replay API flows**

Plan creates session/generates candidates; Compare toggles two or more candidates and score components; Replay loads stored replay bundle; decision controls require rationale and display system recommendation separately from human choice. Stale/missing-layer banners must be prominent.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/test_route_ui.py -v`
Expected: PASS.

Commit: `feat: add APOLLO route planning workspace`

### Task 8: Observability, acceptance coverage, and release gate

**Files:**
- Create: `route_observability.py`
- Create: `tests/test_route_acceptance.py`
- Modify: `route_engine.py`
- Modify: `route_windows.py`
- Modify: `route_replay.py`
- Modify: `route_events.py`
- Modify: `route_api.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces structured counters/timers for generation latency, generated/rejected candidates, rejection reasons, layer freshness, scoring latency, window latency, outbox depth/failures, replay result, DB errors, and JANUS authorization failures.
- Does not log bearer tokens, secrets, or unauthorized route contents.

- [ ] **Step 1: Write failing acceptance/security tests**

One acceptance fixture must prove: session creation; origin/destination/waypoints; at least two alternatives when graph permits; full metrics/breakdown/explanation; hard-constraint rejection; operator selection/rejection rationale; non-top manual choice preserved; persisted windows; replay provenance; cross-tenant denial; duplicate NEXUS idempotency; stale optional layer degradation; required graph failure; offline queued events; no weapon/engagement request fields accepted.

- [ ] **Step 2: Add observability hooks**

Use monotonic timers around engine, scoring, window, replay, DB, and outbox operations. Emit structured logs with event names and numeric metadata only after authorization; redact/omit request route contents on auth failures.

- [ ] **Step 3: Update readiness/release gate**

When `APOLLO_ROUTE_PLANNING_ENABLED=true`, readiness reports route schema availability and dataset/outbox state. Keep the feature disabled by default. Document that enabling requires JANUS route permissions and successful CI acceptance tests.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`
Expected: all tests PASS.

Run: `python -m py_compile app.py entrypoint.py nexus_bridge.py planning_kpis.py route_models.py route_store.py route_engine.py route_windows.py route_replay.py route_events.py route_api.py route_ui.py route_observability.py`
Expected: exit 0.

Build: `docker build -t ung-apollo-route-v1 .`
Expected: exit 0.

- [ ] **Step 5: Commit**

Commit: `test: gate APOLLO route planning V1 release`

## Implementation Completion Gate

Before claiming V1 complete, verify every acceptance criterion from the design spec maps to a passing test. Do not enable the feature by default until: route schema initializes, JANUS permissions are configured, NEXUS idempotency tests pass, replay determinism passes, security-context tests pass, and the full acceptance test passes. Then use `superpowers:verification-before-completion` before any completion claim.

# APOLLO Route Planning V1 — Design Specification

Date: 2026-10-05
Status: Design approved; awaiting written-spec review
Repository: `UNG-APOLLO`

## 1. Purpose

Add a geospatial planning workspace to UNG-APOLLO for non-weapon mission planning such as logistics, transportation, emergency response, inspection, surveying, and simulated vehicle operations. The feature should provide an operator with multiple feasible route alternatives, explain why each route scored as it did, support time-window planning, and preserve a complete decision history.

This is an APOLLO capability, not a new standalone UNG product. Existing UNG services remain authoritative for their own domains:

- APOLLO: plan lifecycle, route generation, route scoring, comparison, replay, operator decisions.
- NEXUS: data integration and normalized event exchange.
- JANUS: identity, authentication, authorization, tenant/organization context, classification and compartment context.
- SENTINEL: security and audit events.
- ATLAS: orchestration and shared control-plane coordination where required.

## 2. Scope and safety boundary

V1 supports route and mission planning for benign/non-weapon use cases. It must not provide target engagement, weapon release, hostile-force optimization, strike routing, or autonomous attack behavior.

The engine may optimize common operational factors such as distance, ETA, energy/fuel use, terrain, weather, communications quality, geofences, and operator-defined benign constraints. Restricted zones are treated as avoidance/compliance constraints rather than hostile-force threat maps.

## 3. Existing APOLLO baseline

Current APOLLO already exposes plan, assessment, intelligence, scenario, recommendation, and decision-brief capabilities, backed by PostgreSQL and protected by JANUS bearer-token permissions. It also already has a NEXUS bridge with inbound/outbound event handling and integration-event persistence.

Route Planning V1 extends these existing patterns rather than replacing them.

## 4. User experience

### 4.1 Main workspace

The primary screen is a dark operations-style planning workspace with:

- Full-screen interactive map as the dominant canvas.
- Start, destination, and draggable intermediate waypoints.
- Candidate routes drawn simultaneously with clear visual differentiation.
- Layer controls for terrain, weather, geofences/restricted areas, communications quality, and optional user-provided overlays.
- Right-side route comparison panel.
- Expandable bottom timeline for departure windows and forecast variation.
- Persistent `WHY THIS ROUTE?` explanation panel.

### 4.2 Modes

The UI has three primary modes:

1. **Plan** — create/edit waypoints, constraints, departure window, vehicle profile, and generate route alternatives.
2. **Compare** — compare two or more candidates side by side, including score decomposition and constraint violations.
3. **Replay** — reconstruct a prior planning session from stored inputs, data snapshots, engine version, operator actions, and decisions.

### 4.3 Candidate route card

Each route card shows at minimum:

- Route label and stable candidate ID.
- Distance.
- ETA.
- Estimated energy or fuel consumption.
- Terrain/elevation cost.
- Weather impact.
- Communications-coverage estimate when available.
- Geofence/compliance status.
- Confidence / data-quality indicator.
- Composite score.
- Human-readable score explanation.

No route is hidden merely because it is not top-ranked. Infeasible routes may be retained for explanation when useful, but must be clearly marked as rejected and state which hard constraint failed.

## 5. Core planning model

### 5.1 Planning request

A planning request contains:

- `plan_id`
- vehicle/profile ID
- origin
- destination
- optional intermediate waypoints
- earliest departure
- latest departure / planning horizon
- hard constraints
- soft preferences
- enabled data layers
- maximum number of alternatives
- operator context from JANUS

### 5.2 Hard constraints

Hard constraints are pass/fail and cannot be outweighed by a good score. Examples include:

- forbidden geofence intersection
- vehicle range or endurance limit
- minimum/maximum elevation or depth limits where applicable
- route-type restrictions for the selected vehicle profile
- required waypoint ordering
- unavailable launch/arrival window

### 5.3 Soft factors

Soft factors contribute to ranking and remain visible in score decomposition:

- travel time
- path length
- energy/fuel estimate
- weather exposure
- terrain/elevation burden
- communications quality
- operator preference weights
- data freshness / uncertainty penalty

### 5.4 Candidate generation

The engine should separate candidate generation from candidate scoring.

Generation produces several geometrically and operationally distinct feasible alternatives. The initial implementation may use graph-based routing (A*/Dijkstra/k-shortest-path style) over a normalized cost surface or transport graph, with vehicle-specific edge filters.

The route engine must keep deterministic input normalization and deterministic tie-breaking for identical inputs and data versions so replay can reproduce the same result.

### 5.5 Scoring

Scoring uses a normalized weighted model with an explicit breakdown. Example conceptual form:

`score = w_time*T + w_energy*E + w_weather*W + w_terrain*R + w_comms*C + w_uncertainty*U`

Weights belong to a versioned vehicle/mission profile and are stored with the planning session. Scores are not presented without their components.

V1 does not use opaque ML ranking as the primary route selector. ML-derived estimates may be added later as independently visible inputs with provenance.

## 6. Launch/departure window analysis

For a requested planning horizon, APOLLO evaluates candidate departure times at a configurable interval. Each interval stores:

- forecast data version/time
- feasibility state
- candidate score
- ETA
- energy estimate
- weather margin
- confidence

The UI plots these as a timeline. A recommended window is simply the best feasible interval under the configured scoring model, with the explanation visible to the operator.

## 7. Data and persistence

Add route-specific persistence without overloading the existing `apollo_plans` table.

### 7.1 New tables

`apollo_route_sessions`
- id UUID PK
- plan_id UUID FK-ish reference to APOLLO plan
- vehicle_profile_id TEXT
- status TEXT
- request JSONB
- engine_version TEXT
- policy_version TEXT
- data_snapshot JSONB
- created_by TEXT
- created_at TIMESTAMPTZ
- updated_at TIMESTAMPTZ

`apollo_route_candidates`
- id UUID PK
- session_id UUID
- candidate_index INT
- geometry JSONB/GeoJSON
- metrics JSONB
- score DOUBLE PRECISION
- score_breakdown JSONB
- feasibility TEXT
- rejection_reasons JSONB
- explanation TEXT
- created_at TIMESTAMPTZ

`apollo_route_windows`
- id UUID PK
- session_id UUID
- departure_time TIMESTAMPTZ
- candidate_id UUID nullable
- feasibility TEXT
- metrics JSONB
- score DOUBLE PRECISION nullable
- created_at TIMESTAMPTZ

`apollo_route_decisions`
- id UUID PK
- session_id UUID
- candidate_id UUID nullable
- actor_id TEXT
- decision_type TEXT
- rationale TEXT
- metadata JSONB
- created_at TIMESTAMPTZ

`apollo_route_events`
- id UUID PK
- session_id UUID
- event_type TEXT
- payload JSONB
- source_system TEXT
- created_at TIMESTAMPTZ

Indexes should cover `plan_id`, `session_id`, `created_at`, and stable candidate lookup.

## 8. API design

All protected endpoints use JANUS bearer authorization and APOLLO-specific permissions.

Suggested V1 endpoints:

- `POST /v1/routes/sessions` — create planning session.
- `GET /v1/routes/sessions/{id}` — fetch session summary.
- `POST /v1/routes/sessions/{id}/generate` — generate/rank route candidates.
- `GET /v1/routes/sessions/{id}/candidates` — list candidates.
- `GET /v1/routes/sessions/{id}/candidates/{candidate_id}` — route details and score decomposition.
- `POST /v1/routes/sessions/{id}/windows` — calculate departure windows.
- `POST /v1/routes/sessions/{id}/decisions` — record operator selection/rejection/note.
- `GET /v1/routes/sessions/{id}/replay` — return deterministic replay bundle.
- `GET /v1/routes/profiles` — list available vehicle/mission profiles.

Suggested permissions:

- `apollo.routes.read`
- `apollo.routes.write`
- `apollo.routes.generate`
- `apollo.routes.decide`
- `apollo.routes.replay`

Admin remains governed by existing `ung.admin` fallback behavior unless JANUS policy is later tightened globally.

## 9. NEXUS integration

Use the existing APOLLO NEXUS bridge and envelope pattern.

Inbound examples:

- `APOLLO.ROUTE.WEATHER_SNAPSHOT`
- `APOLLO.ROUTE.TERRAIN_SNAPSHOT`
- `APOLLO.ROUTE.GEOFENCE_SNAPSHOT`
- `APOLLO.ROUTE.COMMS_SNAPSHOT`
- `APOLLO.ROUTE.VEHICLE_PROFILE_UPDATED`

Outbound examples:

- `APOLLO.ROUTE.SESSION_CREATED`
- `APOLLO.ROUTE.CANDIDATES_GENERATED`
- `APOLLO.ROUTE.DECISION_RECORDED`
- `APOLLO.ROUTE.REPLAY_REQUESTED`

Every external datum used in scoring should carry source, observed/fetched time, version/hash when available, and confidence/quality where available.

## 10. JANUS, classification, and multi-tenant enforcement

Route sessions, candidates, data snapshots, events, and decisions inherit or explicitly store the plan security context.

At minimum the authorization context must support:

- tenant/organization boundary
- classification level
- compartment membership
- explicit sharing policy
- principal ID

Checks occur at ingest, read, write, replay, and NEXUS synchronization boundaries. A route session must never become a bypass around the plan-level security label.

## 11. SENTINEL audit behavior

Security-relevant and operator-decision events should be emitted to SENTINEL or persisted for later delivery through NEXUS, including:

- session creation
- route generation
- rejected inputs
- permission denial
- external-data provenance changes
- candidate selected/rejected
- manual override of top-ranked candidate
- replay access
- configuration/profile version changes

Audit records must distinguish system recommendation from human decision.

## 12. Disconnected-edge behavior

V1 should be designed so the route engine can run with locally cached data when upstream links are unavailable.

Local edge state may include:

- map/graph tiles or region extracts
- terrain data
- vehicle profiles
- recent weather snapshots with clear staleness markers
- geofences
- scoring policies
- route sessions and decisions

Offline-generated sessions are marked with local provenance and data freshness. Synchronization later uses the existing UNG disconnected-edge replication model through NEXUS. Conflict handling must preserve decision history; later synchronization must not silently overwrite an operator decision made offline.

## 13. Replay and reproducibility

A replay bundle contains everything required to explain/recompute the original result as far as practical:

- normalized request
- engine version
- profile/policy version
- data-source versions/hashes
- candidate geometries
- score breakdowns
- hard-constraint results
- operator actions and timestamps

If an exact external-data snapshot is unavailable, replay must declare that it is approximate rather than silently using current data.

## 14. Explainability requirements

Every ranked route must answer:

- Why was this route generated?
- Which hard constraints did it satisfy?
- Which factors improved its score?
- Which factors reduced its score?
- What data was used and how old was it?
- What would need to change for another route to rank higher?

The UI must not reduce explanation to a single confidence percentage.

## 15. Failure behavior

- Missing required map/graph data: generation fails clearly; do not fabricate a route.
- Missing optional layer: generate only if policy allows, mark degraded confidence, and identify the missing layer.
- JANUS unavailable: fail closed for protected operations.
- NEXUS unavailable: local planning may continue if all required local data exists; outbound events queue for later delivery.
- Database unavailable: readiness is degraded and write operations fail rather than pretending persistence succeeded.
- Stale weather/environmental data: display age prominently and apply configured uncertainty penalty.

## 16. Observability

Expose metrics/logs/traces consistent with the shared UNG observability layer, including:

- route-generation latency
- candidates generated/rejected
- hard-constraint rejection counts by reason
- data-layer freshness
- scoring latency
- departure-window calculation latency
- NEXUS queue depth/failures
- replay success/failure
- database latency/errors
- JANUS authorization failures

Do not log secrets, bearer tokens, or unauthorized route contents.

## 17. UI implementation boundary

The backend API should not depend on one specific map renderer. The front end consumes GeoJSON-like route/layer structures and metric/explanation payloads.

V1 visual design goals:

- dark APOLLO operations theme
- map-first layout
- compact translucent telemetry cards
- readable route colors with selected/unselected states
- collapsible layer controls
- expandable timeline
- keyboard/mouse/touch-compatible waypoint editing where practical
- responsive desktop-first layout with usable tablet fallback

## 18. Testing strategy

### Unit tests

- constraint evaluation
- deterministic score normalization
- weighted ranking
- tie-breaking
- energy/time estimation adapters
- data freshness penalties
- explanation generation

### API tests

- JANUS permission enforcement
- session lifecycle
- candidate generation contract
- decision recording
- replay response
- invalid geometry/input handling
- degraded data-layer behavior

### Integration tests

- APOLLO ↔ NEXUS inbound/outbound envelopes
- APOLLO ↔ JANUS authorization
- PostgreSQL persistence/migrations
- SENTINEL audit-event delivery or durable queueing

### Determinism/replay tests

Given identical normalized inputs, profile version, engine version, and data snapshots, candidate ordering and score decomposition must reproduce exactly within defined floating-point tolerance.

### Security tests

- cross-tenant read/write denial
- classification/compartment enforcement
- replay access enforcement
- unauthorized NEXUS message rejection
- duplicate message/idempotency behavior

## 19. Rollout

V1 should be released incrementally:

1. Route-session schema and APIs.
2. Deterministic synthetic route generator and scoring fixtures.
3. Map UI with manual waypoints and candidate comparison.
4. Real map/terrain adapter.
5. Weather/geofence/comms adapters through NEXUS.
6. Departure-window analysis.
7. Replay and audit views.
8. Disconnected cache/synchronization hardening.

A feature flag should keep the new workspace disabled until its database migration, JANUS permissions, and route-generation acceptance tests pass.

## 20. Acceptance criteria

V1 is ready for implementation completion only when all of the following are true:

- Operator can create a route-planning session from an APOLLO plan.
- Operator can set origin, destination, intermediate waypoints, departure horizon, and vehicle profile.
- Engine returns at least two distinct feasible alternatives when the underlying graph permits them.
- Each candidate exposes distance, ETA, energy/fuel estimate, enabled environmental metrics, score breakdown, confidence/data-quality state, and explanation.
- Hard-constraint failures are explicit and cannot be outweighed by soft scoring.
- Operator can select or reject a candidate and provide a rationale.
- Manual choice different from the top-ranked route is preserved as a human decision, not rewritten as a system recommendation.
- Departure-window analysis is persisted and reproducible.
- Replay returns stored inputs, versions, decisions, and provenance.
- JANUS permissions protect all route endpoints.
- Tenant/classification/compartment policy is enforced on route records and synchronization.
- NEXUS integration uses existing envelope/idempotency patterns.
- SENTINEL receives or can later receive durable audit events.
- Offline/degraded operation never hides data staleness.
- No weapon-targeting or hostile-engagement optimization is included.

## 21. Non-goals for V1

- Autonomous weapon or engagement planning.
- Target selection or strike optimization.
- Direct autopilot command/control.
- Real-time vehicle piloting.
- Opaque AI-only ranking.
- Creating another identity, audit, integration, or replication subsystem inside APOLLO.
- Replacing JANUS, NEXUS, SENTINEL, ATLAS, or the shared edge-replication architecture.

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from route_engine import RouteDataUnavailable
from route_models import DataProvenance, GraphEdge, GraphNode, PlanningDataset, SecurityContext, VehicleProfile

DEFAULT_OPTIONAL_LAYERS = ['weather', 'terrain', 'geofence', 'comms']


def _dt(value: Any, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def _provenance(layer: str, snapshot: dict[str, Any], now: datetime, stale_after: float) -> DataProvenance:
    observed = _dt(snapshot.get('observed_at'))
    fetched = _dt(snapshot.get('fetched_at'), now)
    age = max(0.0, (now - observed).total_seconds()) if observed else None
    stale = bool(age is not None and age > stale_after)
    return DataProvenance(
        layer=layer,
        source_system=str(snapshot.get('source_system') or 'UNKNOWN'),
        version=str(snapshot.get('version')) if snapshot.get('version') is not None else None,
        content_hash=snapshot.get('content_hash') or snapshot.get('hash'),
        observed_at=observed,
        fetched_at=fetched,
        confidence=snapshot.get('confidence', snapshot.get('quality')),
        age_seconds=age,
        stale=stale,
    )


def _stable_version(graph_snapshot: dict[str, Any], optional_snapshots: dict[str, dict[str, Any]]) -> str:
    material = {
        'graph': graph_snapshot.get('version') or graph_snapshot.get('content_hash'),
        'layers': {k: (v.get('version') or v.get('content_hash')) for k, v in sorted(optional_snapshots.items())},
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]


def build_planning_dataset(
    graph_snapshot: dict[str, Any] | None,
    optional_snapshots: dict[str, dict[str, Any]] | None,
    profile: VehicleProfile,
) -> PlanningDataset:
    if not graph_snapshot or not graph_snapshot.get('nodes') or not graph_snapshot.get('edges'):
        raise RouteDataUnavailable('required_route_dataset_unavailable')
    optional_snapshots = optional_snapshots or {}
    now = datetime.now(timezone.utc)
    stale_after = float(profile.metadata.get('layer_stale_after_seconds', 21600))
    expected = list(profile.metadata.get('optional_layers', DEFAULT_OPTIONAL_LAYERS))
    graph_prov = _provenance('graph', graph_snapshot, now, stale_after)
    nodes = [GraphNode.model_validate(n) for n in graph_snapshot['nodes']]
    edges = [GraphEdge.model_validate(e).model_copy(deep=True) for e in graph_snapshot['edges']]
    by_id = {e.edge_id: e for e in edges}
    layer_prov: dict[str, DataProvenance] = {}
    penalties = 0.0
    confidences = [graph_prov.confidence if graph_prov.confidence is not None else 1.0]

    for layer, snapshot in sorted(optional_snapshots.items()):
        if layer not in DEFAULT_OPTIONAL_LAYERS:
            continue
        prov = _provenance(layer, snapshot, now, stale_after)
        layer_prov[layer] = prov
        if prov.confidence is not None:
            confidences.append(prov.confidence)
        if prov.stale:
            penalties += 0.15
        values = snapshot.get('edge_values') or {}
        for edge_id, value in values.items():
            edge = by_id.get(str(edge_id))
            if not edge:
                continue
            if layer == 'weather': edge.weather_cost = float(value)
            elif layer == 'terrain': edge.terrain_cost = float(value)
            elif layer == 'comms': edge.comms_quality = float(value)
            elif layer == 'geofence': edge.geofence_ids = sorted({str(v) for v in (value or [])})

    missing = sorted(set(expected) - set(layer_prov))
    penalties += 0.1 * len(missing)
    confidence = max(0.0, min(confidences) - 0.1 * len(missing) - 0.05 * sum(1 for p in layer_prov.values() if p.stale))
    from route_observability import metrics
    metrics.increment('route_layers_missing', len(missing))
    metrics.increment('route_layers_stale', sum(1 for p in layer_prov.values() if p.stale))
    security = SecurityContext.model_validate(graph_snapshot.get('security_context') or {
        'tenant_id': 'system', 'principal_id': 'UNG-NEXUS'
    })
    return PlanningDataset(
        dataset_id=str(graph_snapshot.get('dataset_id') or graph_snapshot.get('region_id') or 'route-dataset'),
        version=_stable_version(graph_snapshot, optional_snapshots),
        source_system=str(graph_snapshot.get('source_system') or 'UNKNOWN'),
        observed_at=_dt(graph_snapshot.get('observed_at')),
        fetched_at=_dt(graph_snapshot.get('fetched_at'), now) or now,
        graph_provenance=graph_prov,
        optional_layer_provenance=layer_prov,
        nodes=nodes,
        edges=edges,
        missing_layers=missing,
        confidence=round(confidence, 6),
        uncertainty_penalty=round(min(1.0, penalties), 6),
        security_context=security,
    )

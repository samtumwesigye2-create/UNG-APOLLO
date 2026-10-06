from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from route_models import PlanningDataset, RouteCandidate, RouteWindowResult


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime): return value
    return datetime.fromisoformat(str(value).replace('Z','+00:00'))


def evaluate_departure_windows(
    session: dict[str, Any], candidates: list[RouteCandidate], dataset: PlanningDataset, interval_minutes: int,
) -> list[RouteWindowResult]:
    if interval_minutes <= 0: raise ValueError('interval_minutes_must_be_positive')
    window = (session.get('request') or {}).get('planning_window') or {}
    start = _parse(window['earliest_departure']); end = _parse(window['latest_departure'])
    feasible = sorted([c for c in candidates if c.feasibility != 'rejected' and c.score is not None], key=lambda c: (c.score, c.candidate_id))
    rows: list[RouteWindowResult] = []; at = start
    while at <= end:
        if feasible:
            best = feasible[0]; weather_margin = max(0.0, 1.0 - float(best.metrics.get('weather_cost', 0.0)))
            rows.append(RouteWindowResult(
                departure_time=at, candidate_id=best.candidate_id,
                feasibility='feasible' if best.feasibility == 'feasible' else 'degraded', score=best.score,
                metrics={'eta_minutes':best.metrics.get('eta_minutes'),'energy_estimate':best.metrics.get('energy_estimate'),'weather_margin':weather_margin},
                data_version=dataset.version, confidence=dataset.confidence,
                freshness={k:p.model_dump(mode='json') for k,p in dataset.optional_layer_provenance.items()},
            ))
        else:
            rows.append(RouteWindowResult(departure_time=at,feasibility='unavailable',score=None,metrics={'weather_margin':None},data_version=dataset.version,confidence=dataset.confidence))
        at += timedelta(minutes=interval_minutes)
    ranked=[(r.score,r.departure_time,i) for i,r in enumerate(rows) if r.score is not None and r.feasibility!='unavailable']
    if ranked: rows[min(ranked)[2]].recommended=True
    return rows

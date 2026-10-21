from datetime import datetime, timedelta, timezone
import math
import pytest
from pydantic import ValidationError

from route_models import (
    Coordinate,
    SecurityContext,
    VehicleProfile,
    PreferenceWeights,
    PlanningWindow,
)


def test_coordinate_rejects_out_of_range():
    with pytest.raises(ValidationError):
        Coordinate(lat=91, lon=0)
    with pytest.raises(ValidationError):
        Coordinate(lat=0, lon=-181)


def test_weights_reject_nan_and_negative():
    with pytest.raises(ValidationError):
        PreferenceWeights(time=float('nan'))
    with pytest.raises(ValidationError):
        PreferenceWeights(energy=-0.1)


def test_vehicle_profile_rejects_non_positive_limits():
    with pytest.raises(ValidationError):
        VehicleProfile(profile_id='x', version='1', name='x', mode='ground', max_range_km=0, cruise_speed_kph=10, energy_per_km=1)
    with pytest.raises(ValidationError):
        VehicleProfile(profile_id='x', version='1', name='x', mode='ground', max_range_km=10, cruise_speed_kph=-1, energy_per_km=1)


def test_planning_window_rejects_inverted_range():
    start = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        PlanningWindow(earliest_departure=start, latest_departure=start - timedelta(minutes=1))


def test_security_context_requires_tenant_and_principal():
    with pytest.raises(ValidationError):
        SecurityContext(tenant_id='', principal_id='p')
    with pytest.raises(ValidationError):
        SecurityContext(tenant_id='t', principal_id='')


def test_weights_normalized_sum_to_one():
    w = PreferenceWeights(time=2, energy=1, weather=1, terrain=0, comms=0, uncertainty=0)
    n = w.normalized()
    assert list(n) == ['time', 'energy', 'weather', 'terrain', 'comms', 'uncertainty']
    assert math.isclose(sum(n.values()), 1.0)
    assert n['time'] == 0.5

"""Physics heating model: fits a simulated house, and keeps its safety properties."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.intelligent_hvac import physics
from custom_components.intelligent_hvac.learning import learn_thermal_model, predict_time_to_target_heat
from custom_components.intelligent_hvac.models import ThermalSegment

K, G, P = 0.03, 0.1, 8.0
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _evolve(ti: float, to: float, hours: float, heating: bool) -> float:
    """Exact solution of dTi/dt = P*u - k*(Ti - To) + g."""
    teq = to + ((P if heating else 0.0) + G) / K
    return teq + (ti - teq) * math.exp(-K * hours)


def _seg(kind: str, start: datetime, minutes: float, ti0: float, to: float, noise: float = 0.0) -> ThermalSegment:
    ti1 = _evolve(ti0, to, minutes / 60, kind == "Active_Heating") + noise
    change = ti1 - ti0
    if kind != "Active_Heating":
        kind = "Passive_Decay" if change < 0 else "Passive_Gain"
    return ThermalSegment(type=kind, start_time=start, end_time=start + timedelta(minutes=minutes),
                          duration_minutes=minutes, start_indoor_temp=ti0, end_indoor_temp=ti1,
                          avg_outdoor_temp=to, total_temp_change=change, rate=change / minutes * 60, is_valid=True)


def simulated_nights(n: int = 20, noise: float = 0.0, seed: int = 1) -> list[ThermalSegment]:
    """Each night: idle from 70 overnight, then a morning recovery to 70."""
    rnd = random.Random(seed)
    segs, t = [], T0
    for i in range(n):
        to = rnd.uniform(5, 50)
        idle = _seg("idle", t, rnd.uniform(240, 480), 70.0, to, rnd.gauss(0, noise))
        segs.append(idle)
        t = idle.end_time
        # Heat from wherever the night left us up to 70.
        mins = physics.recovery_minutes(idle.end_indoor_temp, 70.0, to, K, G, P)
        heat = _seg("Active_Heating", t, mins, idle.end_indoor_temp, to, rnd.gauss(0, noise))
        segs.append(heat)
        t = heat.end_time + timedelta(hours=10)
    return segs


def test_fit_recovers_known_house():
    m = physics.fit_heating(simulated_nights())
    assert m is not None and m.p_source == "long_runs"
    assert m.k == pytest.approx(K, rel=0.05)
    assert m.P == pytest.approx(P, rel=0.05)
    assert m.backtest_mae_min is not None and m.backtest_mae_min < 2


def test_fit_is_robust_to_sensor_noise():
    m = physics.fit_heating(simulated_nights(n=30, noise=0.3))
    assert m is not None
    assert m.k == pytest.approx(K, rel=0.3)
    assert m.P == pytest.approx(P, rel=0.15)


def test_colder_is_never_faster():
    m = physics.fit_heating(simulated_nights())
    times = [m.recovery_minutes(65, 70, out) for out in (50, 30, 10, -10)]
    assert all(a < b for a, b in zip(times, times[1:]))


def test_unreachable_target_returns_none():
    # Furnace too small for the cold: equilibrium below the target.
    assert physics.recovery_minutes(60, 75, outdoor=-30, k=0.2, g=0.0, P=15.0) is None


def test_refuses_without_enough_idle_history():
    segs = [s for s in simulated_nights() if s.type == "Active_Heating"]
    assert physics.fit_heating(segs) is None


def test_refuses_non_physical_loss():
    # Idle periods where the house *warms* more the colder it is outside -> k < 0.
    segs = []
    for i in range(12):
        to = 10 + 3 * i
        rate = -0.05 * (70 - to) * -1  # wrong sign on purpose
        segs.append(ThermalSegment(type="Passive_Gain", start_time=T0 + timedelta(hours=8 * i),
                                   end_time=T0 + timedelta(hours=8 * i + 2), duration_minutes=120,
                                   start_indoor_temp=70, end_indoor_temp=70 + rate * 2, avg_outdoor_temp=to,
                                   total_temp_change=rate * 2, rate=rate, is_valid=True))
    assert physics.fit_loss(segs) is None


def test_confidence_reflects_error():
    good = physics.fit_heating(simulated_nights())
    noisy = physics.fit_heating(simulated_nights(n=30, noise=0.8, seed=7))
    assert physics.confidence(good) > physics.confidence(noisy)
    assert physics.confidence(None) == 0.0


def test_learned_model_predicts_through_public_api():
    model = learn_thermal_model(simulated_nights(), training_days=30)
    assert model.time_to_target_functions["heating"]["function_type"] == "physics_rc"
    minutes = predict_time_to_target_heat(model, 5.0, outdoor_temp=30.0, indoor_temp=65.0)
    assert minutes == pytest.approx(physics.recovery_minutes(65, 70, 30, K, G, P), rel=0.05)
    assert model.confidence_score > 0.7


def test_no_heating_prediction_without_physics():
    model = learn_thermal_model([s for s in simulated_nights() if s.type == "Active_Heating"], training_days=30)
    assert "heating" not in model.time_to_target_functions
    assert model.confidence_score <= 0.3

"""Resampling + segmentation regressions found in the 2026-11 audit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.intelligent_hvac.resampling import anchor_time, resample_to_uniform_timesteps
from custom_components.intelligent_hvac.segmentation import identify_segments

T0 = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
CLIMATE, INDOOR, OUTDOOR = "climate.thermostat", "sensor.bedroom", "sensor.outdoor"


def _climate_history(actions: list[tuple[int, str]]) -> list[dict]:
    """hvac_action changes at minute offsets. The state (mode) stays 'heat', so
    last_changed never moves: exactly how HA reports attribute-only changes."""
    return [
        {"state": "heat", "last_changed": T0 - timedelta(days=3),
         "last_updated": T0 + timedelta(minutes=m), "attributes": {"hvac_action": a}}
        for m, a in actions
    ]


def _sensor_history(points: list[tuple[int, float]], entity_unit="°F") -> list[dict]:
    return [
        {"state": str(v), "last_changed": T0 + timedelta(minutes=m),
         "last_updated": T0 + timedelta(minutes=m), "attributes": {"unit_of_measurement": entity_unit}}
        for m, v in points
    ]


# A night: idle (cooling down), a 60-min heat-up 64->70, idle, heat, idle.
ACTIONS = [(0, "idle"), (120, "heating"), (180, "idle"), (300, "heating"), (330, "idle")]
INDOOR_PTS = [(0, 66.0), (120, 64.0), (180, 70.0), (300, 68.0), (330, 70.0), (480, 68.5)]
OUTDOOR_PTS = [(0, 30.0), (480, 30.0)]


def _segments(start_min: int, end_min: int, interval: int = 5):
    start, end = T0 + timedelta(minutes=start_min), T0 + timedelta(minutes=end_min)
    hist = {CLIMATE: _climate_history(ACTIONS), INDOOR: _sensor_history(INDOOR_PTS),
            OUTDOOR: _sensor_history(OUTDOOR_PTS)}
    resampled = {e: resample_to_uniform_timesteps(r, start, end, interval, entity_id=e) for e, r in hist.items()}
    return identify_segments(resampled, CLIMATE, [INDOOR], OUTDOOR, interval)


def test_attribute_only_changes_are_not_collapsed():
    hist = _climate_history(ACTIONS)
    out = resample_to_uniform_timesteps(hist, T0, T0 + timedelta(minutes=400), 5, entity_id=CLIMATE)
    actions = [r["attributes"]["hvac_action"] for r in out]
    # Keyed by last_changed this was a single record -> 'idle' everywhere.
    assert actions.count("heating") == (60 + 30) // 5


def test_grid_is_clock_anchored():
    odd_start = T0 + timedelta(minutes=2, seconds=17)
    out = resample_to_uniform_timesteps(_sensor_history(INDOOR_PTS), odd_start, T0 + timedelta(minutes=30), 5)
    assert all(r["timestamp"].minute % 5 == 0 and r["timestamp"].second == 0 for r in out)
    assert anchor_time(odd_start, 5) == T0


def test_heating_run_is_complete_and_not_merged_with_idle():
    segs = _segments(-30, 480)
    heat = [s for s in segs if s.type == "Active_Heating"]
    assert len(heat) == 2
    first = heat[0]
    assert first.start_time == T0 + timedelta(minutes=120)
    assert first.duration_minutes == 60               # heating only, no coasting appended
    assert round(first.start_indoor_temp, 1) == 64.0
    assert round(first.end_indoor_temp, 1) == 70.0
    assert round(first.rate, 1) == 6.0                # °F/h


def test_open_run_and_truncated_first_run_are_not_stored():
    segs = _segments(60, 200)   # starts mid-idle, ends mid-idle
    kinds = [(s.type, s.start_time) for s in segs]
    # The idle run that began before the window and the open idle run at the end are dropped.
    assert kinds == [("Active_Heating", T0 + timedelta(minutes=120))]


def test_overlapping_windows_yield_identical_segments():
    """15-min updates re-read the last 24 h; the same runs must get the same keys."""
    a = {(s.start_time, s.type, s.duration_minutes) for s in _segments(-30, 480)}
    b = {(s.start_time, s.type, s.duration_minutes) for s in _segments(-17, 493)}
    common = {k for k in a if k[0] >= T0 + timedelta(minutes=100)}
    assert common and common <= b


def test_short_heating_cycle_with_no_visible_rise_is_kept():
    """1°F thermostat resolution: a 10-min cycle can show +0°F but is real heating time."""
    actions = [(0, "idle"), (60, "heating"), (70, "idle"), (200, "heating")]
    pts = [(0, 69.0), (200, 69.0)]
    hist = {CLIMATE: _climate_history(actions), INDOOR: _sensor_history(pts), OUTDOOR: _sensor_history(OUTDOOR_PTS)}
    end = T0 + timedelta(minutes=300)
    resampled = {e: resample_to_uniform_timesteps(r, T0 - timedelta(minutes=30), end, 5, entity_id=e)
                 for e, r in hist.items()}
    segs = identify_segments(resampled, CLIMATE, [INDOOR], OUTDOOR, 5)
    assert [(s.type, s.duration_minutes) for s in segs if s.type == "Active_Heating"] == [("Active_Heating", 10)]


def test_indoor_sensor_listed_twice_is_not_double_counted():
    hist = {CLIMATE: _climate_history(ACTIONS), INDOOR: _sensor_history(INDOOR_PTS), OUTDOOR: _sensor_history(OUTDOOR_PTS)}
    start, end = T0 - timedelta(minutes=30), T0 + timedelta(minutes=480)
    resampled = {e: resample_to_uniform_timesteps(r, start, end, 5, entity_id=e) for e, r in hist.items()}
    once = identify_segments(resampled, CLIMATE, [INDOOR], OUTDOOR, 5)
    twice = identify_segments(resampled, CLIMATE, [INDOOR, INDOOR], OUTDOOR, 5)
    assert [s.start_indoor_temp for s in once] == [s.start_indoor_temp for s in twice]


def test_short_furnace_cycle_kept_short_idle_dropped():
    actions = [(0, "idle"), (60, "heating"), (65, "idle"), (70, "heating"), (75, "idle"), (200, "heating")]
    pts = [(0, 69.0), (200, 69.0)]
    hist = {CLIMATE: _climate_history(actions), INDOOR: _sensor_history(pts), OUTDOOR: _sensor_history(OUTDOOR_PTS)}
    resampled = {e: resample_to_uniform_timesteps(r, T0 - timedelta(minutes=30), T0 + timedelta(minutes=300), 5, entity_id=e)
                 for e, r in hist.items()}
    segs = identify_segments(resampled, CLIMATE, [INDOOR], OUTDOOR, 5)
    assert [s.duration_minutes for s in segs if s.type == "Active_Heating"] == [5, 5]

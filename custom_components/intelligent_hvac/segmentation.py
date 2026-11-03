"""Thermal segmentation: cut the resampled timeline into runs of one hvac_action.

Rules (tested in tests/test_segmentation.py):

* A segment is a maximal run of one ``hvac_action`` (heating / idle / cooling...).
* The run touching the start of the fetch window is dropped (it began before the
  window, so its start is fake), and so is the run still open at the end (it
  hasn't finished). Only complete runs are stored, so the same physical run gets
  the same key on every overlapping fetch.
* Heating/cooling runs are typed by the action, not by the measured rate: a
  10-minute furnace cycle that the thermostat's 1°F resolution shows as +0°F is
  still a heating run, and its duration matters (duty cycle).
* Active (heating/cooling) runs are kept down to one sample; idle runs need
  MIN_SEGMENT_DURATION_MINUTES.
* No merging of adjacent runs. (The previous merge glued each heating run to the
  idle period after it while keeping the "heating" label and rate, so heating
  durations included coasting time.)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .const import (
    MIN_SEGMENT_DURATION_MINUTES,
    SEGMENT_TYPE_ACTIVE_COOLING,
    SEGMENT_TYPE_ACTIVE_HEATING,
    SEGMENT_TYPE_PASSIVE_DECAY,
    SEGMENT_TYPE_PASSIVE_GAIN,
    SEGMENT_TYPE_STABLE,
)
from .models import ThermalSegment

_LOGGER = logging.getLogger(__name__)

_INVALID = ("unknown", "unavailable", "none", "", "n/a", "na")


def _parse_temperature(state: Any, attributes: dict[str, Any]) -> float | None:
    """Temperature from a state, or from climate/weather attributes."""
    if isinstance(state, (int, float)):
        return float(state)
    if state is not None and str(state).strip().lower() not in _INVALID:
        try:
            return float(state)
        except (TypeError, ValueError):
            pass
    for key in ("current_temperature", "temperature"):
        val = attributes.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _temps_by_time(records: list[dict[str, Any]], weather: bool) -> dict[datetime, float]:
    out: dict[datetime, float] = {}
    for rec in records:
        ts = rec.get("timestamp")
        if ts is None:
            continue
        attrs = rec.get("attributes") or {}
        temp = (_parse_temperature(attrs.get("temperature"), {}) if weather
                else _parse_temperature(rec.get("state"), attrs))
        if temp is not None:
            out[ts] = temp
    return out


def _segment_type(action: str, rate: float) -> str:
    action = str(action).lower()
    if action == "heating":
        return SEGMENT_TYPE_ACTIVE_HEATING
    if action == "cooling":
        return SEGMENT_TYPE_ACTIVE_COOLING
    if action in ("idle", "off"):
        if rate < -0.01:
            return SEGMENT_TYPE_PASSIVE_DECAY
        if rate > 0.01:
            return SEGMENT_TYPE_PASSIVE_GAIN
    return SEGMENT_TYPE_STABLE


def identify_segments(
    resampled_data: dict[str, list[dict[str, Any]]],
    climate_entity: str,
    indoor_sensors: list[str],
    outdoor_sensor: str,
    sampling_interval_minutes: float,
) -> list[ThermalSegment]:
    """Return complete, valid segments from resampled data.

    ``indoor_sensors`` is the full list of entities averaged into the indoor
    temperature (the coordinator passes the thermostat plus configured sensors).
    """
    climate_data = (resampled_data or {}).get(climate_entity, [])
    if not climate_data:
        _LOGGER.warning("No climate entity data available for segmentation")
        return []

    per_sensor = [
        _temps_by_time(resampled_data.get(eid, []), weather=False)
        for eid in dict.fromkeys(indoor_sensors)  # de-duplicated, order kept
    ]
    outdoor = _temps_by_time(
        resampled_data.get(outdoor_sensor, []),
        weather=bool(outdoor_sensor) and outdoor_sensor.startswith("weather."),
    )

    # Build the timeline: (timestamp, action, indoor, outdoor)
    timeline: list[tuple[datetime, str, float, float | None]] = []
    for rec in climate_data:
        ts = rec.get("timestamp")
        if ts is None:
            continue
        vals = [s[ts] for s in per_sensor if ts in s]
        if not vals:
            continue
        action = (rec.get("attributes") or {}).get("hvac_action") or "unknown"
        timeline.append((ts, str(action), sum(vals) / len(vals), outdoor.get(ts)))

    if len(timeline) < 2:
        return []

    # Runs of one action: [start_idx, end_idx] where end_idx is the first sample of the next run.
    runs: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(timeline)):
        if timeline[i][1] != timeline[start][1]:
            runs.append((start, i))
            start = i
    # The final run (start..end of data) is still open: never stored.
    # The first run began before the window: its start is fake, drop it.
    runs = runs[1:]

    segments: list[ThermalSegment] = []
    for a, b in runs:
        t0, action, temp0, _ = timeline[a]
        t1, _, temp1, _ = timeline[b]
        minutes = (t1 - t0).total_seconds() / 60.0
        active = action.lower() in ("heating", "cooling")
        # Furnace cycles are often < 10 min; every minute of run time counts for
        # duty cycle, so active runs are kept down to one sample.
        if minutes < (sampling_interval_minutes if active else MIN_SEGMENT_DURATION_MINUTES):
            continue
        outs = [o for _, _, _, o in timeline[a:b + 1] if o is not None]
        avg_out = sum(outs) / len(outs) if outs else None
        change = temp1 - temp0
        rate = change / minutes * 60.0
        seg_type = _segment_type(action, rate)
        if seg_type == SEGMENT_TYPE_STABLE or avg_out is None:
            continue
        segments.append(ThermalSegment(
            type=seg_type,
            start_time=t0,
            end_time=t1,
            duration_minutes=minutes,
            start_indoor_temp=temp0,
            end_indoor_temp=temp1,
            avg_outdoor_temp=avg_out,
            total_temp_change=change,
            rate=rate,
            is_valid=True,
        ))

    _LOGGER.info("Identified %d complete segments from %d timesteps", len(segments), len(timeline))
    return segments

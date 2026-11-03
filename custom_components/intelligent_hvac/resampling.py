"""Uniform time series resampling.

Two properties matter for learning and are tested:

* Records are ordered by ``last_updated``, not ``last_changed``. A climate
  entity's ``hvac_action`` (heating/idle) is an *attribute*; when it flips the
  state (the HVAC mode) is unchanged, so every record in a heating season shares
  one ``last_changed``. Keying by it collapsed thousands of records into one per
  mode change, so the furnace cycling was invisible.
* The grid is anchored to absolute clock times (multiples of the interval since
  the epoch). Overlapping fetch windows then produce identical timestamps, so
  the same physical segment is recognised as the same segment on every update.
"""
from __future__ import annotations

import bisect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_INVALID = ("unknown", "unavailable", "none", "", "n/a", "na")


def anchor_time(t: datetime, interval_minutes: float, *, ceil: bool = False) -> datetime:
    """Snap a time to the interval grid (floor by default)."""
    step = interval_minutes * 60
    secs = (dt_util.as_utc(t) - _EPOCH).total_seconds()
    n = int(secs // step) + (1 if ceil and secs % step else 0)
    return _EPOCH + timedelta(seconds=n * step)


def _record_time(record: dict[str, Any]) -> datetime:
    return dt_util.as_utc(record.get("last_updated") or record["last_changed"])


def _parse_state(state: Any) -> float | None:
    if isinstance(state, (int, float)):
        return float(state)
    if state is None or str(state).strip().lower() in _INVALID:
        return None
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def resample_to_uniform_timesteps(
    history_records: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    interval_minutes: float,
    entity_id: str | None = None,
) -> list[dict[str, Any]]:
    """Resample irregular history onto a uniform, clock-anchored grid.

    Climate and weather entities (string states, data in attributes) are
    forward-filled: each timestep gets the record in effect at that moment,
    never a future one. Numeric sensors are linearly interpolated between the
    surrounding valid readings; timesteps outside the readings get the nearest
    reading only within one interval, otherwise they are skipped (gaps stay
    gaps).
    """
    if not history_records:
        return []

    records = sorted(history_records, key=_record_time)
    times = [_record_time(r) for r in records]
    step = timedelta(minutes=interval_minutes)

    t = anchor_time(start_time, interval_minutes, ceil=True)
    end = dt_util.as_utc(end_time)
    out: list[dict[str, Any]] = []

    special = bool(entity_id) and entity_id.split(".", 1)[0] in ("climate", "weather")

    if special:
        while t <= end:
            i = bisect.bisect_right(times, t) - 1
            if i >= 0:
                rec = records[i]
                out.append({"timestamp": t, "state": rec.get("state", "unknown"),
                            "attributes": rec.get("attributes") or {}})
            t += step
        return out

    valid = [(tm, v, r) for tm, r in zip(times, records) if (v := _parse_state(r.get("state"))) is not None]
    if not valid:
        return []
    vtimes = [x[0] for x in valid]
    while t <= end:
        j = bisect.bisect_right(vtimes, t)
        value: float | None = None
        attrs: dict[str, Any] = {}
        if 0 < j < len(valid):
            (t0, v0, r0), (t1, v1, _) = valid[j - 1], valid[j]
            span = (t1 - t0).total_seconds()
            frac = (t - t0).total_seconds() / span if span > 0 else 0.0
            value, attrs = v0 + (v1 - v0) * frac, r0.get("attributes") or {}
        elif j == len(valid):  # after the last reading: hold it (sensors report on change)
            value, attrs = valid[-1][1], valid[-1][2].get("attributes") or {}
        elif j == 0 and (vtimes[0] - t) <= step:
            value, attrs = valid[0][1], valid[0][2].get("attributes") or {}
        if value is not None:
            out.append({"timestamp": t, "state": value, "attributes": attrs})
        t += step
    return out

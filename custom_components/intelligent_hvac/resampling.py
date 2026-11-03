"""Uniform time series resampling."""
import logging
from datetime import datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)


def resample_to_uniform_timesteps(
    history_records: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    interval_minutes: int,
    entity_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Resample irregular history data to uniform timesteps using linear interpolation.
    
    Args:
        history_records: List of history records with 'state', 'last_changed', 'attributes'
        start_time: Start of time range
        end_time: End of time range
        interval_minutes: Target interval in minutes (e.g., 5 for 5-minute intervals)
    
    Returns:
        List of resampled records with uniform timesteps
    """
    if not history_records:
        _LOGGER.warning("No history records to resample")
        return []
    
    # Convert to UTC
    start_utc = dt_util.as_utc(start_time)
    end_utc = dt_util.as_utc(end_time)
    
    # Generate uniform timesteps
    interval = timedelta(minutes=interval_minutes)
    timesteps = []
    current = start_utc
    while current <= end_utc:
        timesteps.append(current)
        current += interval
    
    if not timesteps:
        return []
    
    # Build lookup for history records by timestamp
    history_by_time = {}
    for record in history_records:
        # Use last_changed as the timestamp
        ts = dt_util.as_utc(record["last_changed"])
        history_by_time[ts] = record
    
    # Get sorted timestamps
    sorted_times = sorted(history_by_time.keys())
    
    if not sorted_times:
        _LOGGER.warning("No valid timestamps in history records")
        return []
    
    resampled = []
    
    # Check if this is a climate or weather entity (for special handling)
    # These entities have string states and important data in attributes
    is_special_entity = entity_id and (
        entity_id.startswith("climate.") or entity_id.startswith("weather.")
    )
    
    for timestep in timesteps:
        if is_special_entity:
            # For climate entities, preserve most recent state and attributes
            # Don't try to interpolate - just use the nearest record
            record = _get_nearest_record(timestep, sorted_times, history_by_time)
            if record:
                resampled.append({
                    "timestamp": timestep,
                    "state": record.get("state", "unknown"),
                    "attributes": record.get("attributes", {}),
                })
        else:
            # For sensors, interpolate numeric values
            value = _interpolate_value(
                timestep,
                sorted_times,
                history_by_time,
            )
            
            # Only add if we have a valid value (don't add None/unknown)
            if value is not None:
                resampled.append({
                    "timestamp": timestep,
                    "state": value,
                    "attributes": _get_attributes_at_time(
                        timestep,
                        sorted_times,
                        history_by_time,
                    ),
                })
        # Note: We skip timesteps with no valid data (gaps are OK)
    
    _LOGGER.debug(
        "Resampled %d records to %d uniform timesteps (%d minute intervals)",
        len(history_records),
        len(resampled),
        interval_minutes,
    )
    
    return resampled


def _interpolate_value(
    target_time: datetime,
    sorted_times: list[datetime],
    history_by_time: dict[datetime, dict[str, Any]],
) -> float | None:
    """Interpolate state value at target_time using linear interpolation."""
    target_utc = dt_util.as_utc(target_time)
    
    # Find the two points to interpolate between
    before = None
    after = None
    
    for i, ts in enumerate(sorted_times):
        if ts <= target_utc:
            before = ts
            if i + 1 < len(sorted_times):
                after = sorted_times[i + 1]
        else:
            if before is None:
                # Target is before first record - use first record
                after = ts
            break
    
    # Get state values
    try:
        if before is not None and after is not None:
            # Interpolate between two points
            before_record = history_by_time[before]
            after_record = history_by_time[after]
            
            before_state = _parse_state(before_record["state"])
            after_state = _parse_state(after_record["state"])
            
            # If either is None, try to find nearest valid value
            if before_state is None and after_state is None:
                # Look for nearest valid value in either direction
                return _find_nearest_valid_value(
                    target_utc,
                    sorted_times,
                    history_by_time,
                )
            
            # If one is None, use the other
            if before_state is None:
                return after_state
            if after_state is None:
                return before_state
            
            # Both valid - linear interpolation
            before_ts = dt_util.as_timestamp(before)
            after_ts = dt_util.as_timestamp(after)
            target_ts = dt_util.as_timestamp(target_utc)
            
            if after_ts == before_ts:
                return before_state
            
            ratio = (target_ts - before_ts) / (after_ts - before_ts)
            interpolated = before_state + (after_state - before_state) * ratio
            
            return interpolated
            
        elif before is not None:
            # Use last known value (if valid)
            state = _parse_state(history_by_time[before]["state"])
            if state is not None:
                return state
            # If invalid, look for nearest valid
            return _find_nearest_valid_value(
                target_utc,
                sorted_times,
                history_by_time,
            )
            
        elif after is not None:
            # Use first known value (if valid)
            state = _parse_state(history_by_time[after]["state"])
            if state is not None:
                return state
            # If invalid, look for nearest valid
            return _find_nearest_valid_value(
                target_utc,
                sorted_times,
                history_by_time,
            )
            
    except (ValueError, TypeError, KeyError) as err:
        _LOGGER.debug("Error interpolating value at %s: %s", target_time, err)
        return None
    
    return None


def _find_nearest_valid_value(
    target_time: datetime,
    sorted_times: list[datetime],
    history_by_time: dict[datetime, dict[str, Any]],
    max_gap_minutes: int = 30,
) -> float | None:
    """Find nearest valid value within max_gap_minutes."""
    target_utc = dt_util.as_utc(target_time)
    target_ts = dt_util.as_timestamp(target_utc)
    max_gap_seconds = max_gap_minutes * 60
    
    nearest = None
    min_diff = None
    
    for ts in sorted_times:
        state = _parse_state(history_by_time[ts]["state"])
        if state is None:
            continue
        
        diff = abs(dt_util.as_timestamp(ts) - target_ts)
        if diff <= max_gap_seconds:
            if min_diff is None or diff < min_diff:
                min_diff = diff
                nearest = state
    
    return nearest


def _parse_state(state: str | float | int | None) -> float | None:
    """Parse state to float, handling 'unknown', 'unavailable', etc."""
    if state is None:
        return None
    
    # If already a number, return it
    if isinstance(state, (int, float)):
        return float(state)
    
    # Convert to string and check for invalid states
    state_str = str(state).strip().lower()
    if state_str in ("unknown", "unavailable", "none", "", "n/a", "na"):
        return None
    
    try:
        return float(state)
    except (ValueError, TypeError):
        return None


def _get_nearest_record(
    target_time: datetime,
    sorted_times: list[datetime],
    history_by_time: dict[datetime, dict[str, Any]],
) -> dict[str, Any] | None:
    """Get the nearest history record to target_time (for climate entities)."""
    target_utc = dt_util.as_utc(target_time)
    
    # Find the record with timestamp <= target_time (most recent before or at target)
    nearest = None
    nearest_time = None
    
    for ts in sorted_times:
        if ts <= target_utc:
            nearest_time = ts
            nearest = history_by_time[ts]
        else:
            break
    
    # If no record before target, use first record after
    if nearest is None and sorted_times:
        nearest_time = sorted_times[0]
        nearest = history_by_time[nearest_time]
    
    return nearest


def _get_attributes_at_time(
    target_time: datetime,
    sorted_times: list[datetime],
    history_by_time: dict[datetime, dict[str, Any]],
) -> dict[str, Any]:
    """Get attributes at target_time (use nearest record)."""
    target_utc = dt_util.as_utc(target_time)
    
    # Find nearest record
    nearest = None
    min_diff = None
    
    for ts in sorted_times:
        diff = abs((dt_util.as_utc(ts) - target_utc).total_seconds())
        if min_diff is None or diff < min_diff:
            min_diff = diff
            nearest = ts
    
    if nearest is not None:
        return history_by_time[nearest].get("attributes", {})
    
    return {}


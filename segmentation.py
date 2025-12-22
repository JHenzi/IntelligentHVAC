"""Thermal segmentation engine."""
import logging
from datetime import datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util

from .const import (
    MIN_SEGMENT_DURATION_MINUTES,
    TREND_DEADBAND_F,
    MIN_TEMP_CHANGE_F,
    HVAC_CYCLE_MERGE_MINUTES,
    SEGMENT_TYPE_PASSIVE_DECAY,
    SEGMENT_TYPE_PASSIVE_GAIN,
    SEGMENT_TYPE_ACTIVE_HEATING,
    SEGMENT_TYPE_ACTIVE_COOLING,
    SEGMENT_TYPE_STABLE,
)
from .models import ThermalSegment

_LOGGER = logging.getLogger(__name__)


def identify_segments(
    resampled_data: dict[str, list[dict[str, Any]]],
    climate_entity: str,
    indoor_sensors: list[str],
    outdoor_sensor: str,
    sampling_interval_minutes: int,
) -> list[ThermalSegment]:
    """
    Identify thermal segments from resampled data.
    
    Args:
        resampled_data: Resampled time series data keyed by entity_id
        climate_entity: Climate entity ID (for hvac_action)
        indoor_sensors: List of indoor sensor entity IDs
        outdoor_sensor: Outdoor sensor entity ID
        sampling_interval_minutes: Sampling interval in minutes
    
    Returns:
        List of identified thermal segments
    """
    segments = []
    
    if not resampled_data:
        _LOGGER.warning("No resampled data available for segmentation")
        return segments
    
    # Get climate entity data for hvac_action
    climate_data = resampled_data.get(climate_entity, [])
    if not climate_data:
        _LOGGER.warning("No climate entity data available for segmentation")
        return segments
    
    # Calculate normalized indoor temperature for each timestep
    indoor_temps = _calculate_indoor_temps_over_time(
        resampled_data,
        indoor_sensors,
        climate_entity,
    )
    
    # Get outdoor temperatures
    outdoor_data = resampled_data.get(outdoor_sensor, [])
    outdoor_temps = {}
    for record in outdoor_data:
        timestamp = record.get("timestamp")
        if timestamp:
            # For weather entities, temperature is in attributes, not state
            attributes = record.get("attributes", {})
            if outdoor_sensor and outdoor_sensor.startswith("weather."):
                # Weather entities have 'temperature' attribute
                temp = _parse_temperature(attributes.get("temperature"), attributes)
            else:
                # For sensor entities, temperature is in state
                temp = _parse_temperature(record.get("state"), attributes)
            if temp is not None:
                outdoor_temps[timestamp] = temp
    
    # Identify segments by analyzing hvac_action changes
    current_segment_start = None
    current_hvac_action = None
    current_start_temp = None
    
    for i, record in enumerate(climate_data):
        timestamp = record.get("timestamp")
        if not timestamp:
            continue
        
        # Get hvac_action from attributes
        attributes = record.get("attributes", {})
        hvac_action = attributes.get("hvac_action", "unknown")
        
        # Get current indoor temp
        indoor_temp = indoor_temps.get(timestamp)
        if indoor_temp is None:
            continue
        
        # Check if hvac_action changed (segment boundary)
        if hvac_action != current_hvac_action:
            # End previous segment if exists
            if current_segment_start is not None and current_hvac_action is not None:
                segment = _create_segment(
                    current_segment_start,
                    timestamp,
                    current_hvac_action,
                    current_start_temp,
                    indoor_temp,
                    outdoor_temps,
                    sampling_interval_minutes,
                )
                if segment:
                    segments.append(segment)
            
            # Start new segment
            current_segment_start = timestamp
            current_hvac_action = hvac_action
            current_start_temp = indoor_temp
    
    # Handle final segment
    if current_segment_start is not None and current_hvac_action is not None:
        if climate_data:
            final_timestamp = climate_data[-1].get("timestamp")
            final_temp = indoor_temps.get(final_timestamp) if final_timestamp else None
            if final_temp is not None:
                segment = _create_segment(
                    current_segment_start,
                    final_timestamp,
                    current_hvac_action,
                    current_start_temp,
                    final_temp,
                    outdoor_temps,
                    sampling_interval_minutes,
                )
                if segment:
                    segments.append(segment)
    
    # Merge short HVAC cycles
    segments = _merge_short_cycles(segments, HVAC_CYCLE_MERGE_MINUTES)
    
    # Filter out invalid segments
    valid_segments = [seg for seg in segments if seg.is_valid]
    
    _LOGGER.info(
        "Identified %d segments (%d valid) from %d timesteps",
        len(segments),
        len(valid_segments),
        len(climate_data),
    )
    
    return valid_segments


def _calculate_indoor_temps_over_time(
    resampled_data: dict[str, list[dict[str, Any]]],
    indoor_sensors: list[str],
    climate_entity: str,
) -> dict[datetime, float]:
    """Calculate normalized indoor temperature for each timestep."""
    temps = {}
    
    # Collect all timestamps from climate entity
    climate_data = resampled_data.get(climate_entity, [])
    timestamps = [r.get("timestamp") for r in climate_data if r.get("timestamp")]
    
    for timestamp in timestamps:
        if timestamp is None:
            continue
        
        # Collect temperatures from all indoor sensors at this timestamp
        sensor_temps = []
        for sensor_id in indoor_sensors + [climate_entity]:
            sensor_data = resampled_data.get(sensor_id, [])
            for record in sensor_data:
                if record.get("timestamp") == timestamp:
                    temp = _parse_temperature(record.get("state"), record.get("attributes", {}))
                    if temp is not None:
                        sensor_temps.append(temp)
                        break
        
        # Average the temperatures
        if sensor_temps:
            temps[timestamp] = sum(sensor_temps) / len(sensor_temps)
    
    return temps


def _parse_temperature(
    state: str | float | int | None,
    attributes: dict[str, Any],
) -> float | None:
    """Parse temperature from state or attributes."""
    # Try state first
    if state is not None:
        if isinstance(state, (int, float)):
            return float(state)
        
        state_str = str(state).strip().lower()
        if state_str not in ("unknown", "unavailable", "none", "", "n/a", "na"):
            try:
                return float(state)
            except (ValueError, TypeError):
                pass
    
    # Try attributes (for climate and weather entities)
    # Climate entities use 'current_temperature'
    if "current_temperature" in attributes:
        temp = attributes["current_temperature"]
        if isinstance(temp, (int, float)):
            return float(temp)
    
    # Weather entities use 'temperature'
    if "temperature" in attributes:
        temp = attributes["temperature"]
        if isinstance(temp, (int, float)):
            return float(temp)
    
    return None


def _create_segment(
    start_time: datetime,
    end_time: datetime,
    hvac_action: str,
    start_temp: float | None,
    end_temp: float | None,
    outdoor_temps: dict[datetime, float],
    sampling_interval_minutes: int,
) -> ThermalSegment | None:
    """Create a thermal segment from boundary data."""
    if start_temp is None or end_temp is None:
        return None
    
    duration_minutes = (end_time - start_time).total_seconds() / 60.0
    
    # Check minimum duration
    if duration_minutes < MIN_SEGMENT_DURATION_MINUTES:
        return None
    
    total_temp_change = end_temp - start_temp
    
    # Check minimum temperature change
    if abs(total_temp_change) < MIN_TEMP_CHANGE_F:
        # Mark as stable (exclude from learning)
        return ThermalSegment(
            type=SEGMENT_TYPE_STABLE,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            start_indoor_temp=start_temp,
            end_indoor_temp=end_temp,
            avg_outdoor_temp=None,
            total_temp_change=total_temp_change,
            rate=0.0,
            is_valid=False,
        )
    
    # Check trend deadband
    if abs(total_temp_change) < TREND_DEADBAND_F:
        return ThermalSegment(
            type=SEGMENT_TYPE_STABLE,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            start_indoor_temp=start_temp,
            end_indoor_temp=end_temp,
            avg_outdoor_temp=None,
            total_temp_change=total_temp_change,
            rate=0.0,
            is_valid=False,
        )
    
    # Calculate rate (°F per hour)
    rate = (total_temp_change / duration_minutes) * 60.0 if duration_minutes > 0 else 0.0
    
    # Determine segment type based on hvac_action and rate
    segment_type = _determine_segment_type(hvac_action, rate)
    
    # Calculate average outdoor temperature during segment
    avg_outdoor_temp = _calculate_avg_outdoor_temp(start_time, end_time, outdoor_temps)
    
    # Segment is valid if it has outdoor temperature
    is_valid = avg_outdoor_temp is not None
    
    return ThermalSegment(
        type=segment_type,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration_minutes,
        start_indoor_temp=start_temp,
        end_indoor_temp=end_temp,
        avg_outdoor_temp=avg_outdoor_temp,
        total_temp_change=total_temp_change,
        rate=rate,
        is_valid=is_valid,
    )


def _determine_segment_type(hvac_action: str, rate: float) -> str:
    """Determine segment type based on hvac_action and temperature rate."""
    hvac_action_lower = str(hvac_action).lower()
    
    if hvac_action_lower == "idle":
        if rate < -0.01:  # Negative rate (cooling down)
            return SEGMENT_TYPE_PASSIVE_DECAY
        elif rate > 0.01:  # Positive rate (heating up)
            return SEGMENT_TYPE_PASSIVE_GAIN
        else:
            return SEGMENT_TYPE_STABLE
    elif hvac_action_lower == "heating":
        if rate > 0:
            return SEGMENT_TYPE_ACTIVE_HEATING
        else:
            return SEGMENT_TYPE_STABLE
    elif hvac_action_lower == "cooling":
        if rate < 0:
            return SEGMENT_TYPE_ACTIVE_COOLING
        else:
            return SEGMENT_TYPE_STABLE
    else:
        return SEGMENT_TYPE_STABLE


def _calculate_avg_outdoor_temp(
    start_time: datetime,
    end_time: datetime,
    outdoor_temps: dict[datetime, float],
) -> float | None:
    """Calculate average outdoor temperature during segment."""
    temps_in_range = []
    
    for timestamp, temp in outdoor_temps.items():
        if start_time <= timestamp <= end_time:
            temps_in_range.append(temp)
    
    if not temps_in_range:
        return None
    
    return sum(temps_in_range) / len(temps_in_range)


def _merge_short_cycles(
    segments: list[ThermalSegment],
    merge_threshold_minutes: float,
) -> list[ThermalSegment]:
    """Merge short HVAC cycles to prevent micro-segments."""
    if not segments:
        return segments
    
    merged = []
    i = 0
    
    while i < len(segments):
        current = segments[i]
        
        # Look ahead for short cycles to merge
        if i + 1 < len(segments):
            next_seg = segments[i + 1]
            gap = (next_seg.start_time - current.end_time).total_seconds() / 60.0
            
            # If gap is small and segments are complementary (on/off), merge them
            if (
                gap < merge_threshold_minutes
                and current.type in (SEGMENT_TYPE_ACTIVE_HEATING, SEGMENT_TYPE_ACTIVE_COOLING)
                and next_seg.type in (SEGMENT_TYPE_PASSIVE_DECAY, SEGMENT_TYPE_PASSIVE_GAIN, SEGMENT_TYPE_STABLE)
            ):
                # Merge: extend current segment to include next
                merged_seg = ThermalSegment(
                    type=current.type,
                    start_time=current.start_time,
                    end_time=next_seg.end_time,
                    duration_minutes=(next_seg.end_time - current.start_time).total_seconds() / 60.0,
                    start_indoor_temp=current.start_indoor_temp,
                    end_indoor_temp=next_seg.end_indoor_temp,
                    avg_outdoor_temp=(
                        (current.avg_outdoor_temp or 0) + (next_seg.avg_outdoor_temp or 0)
                    ) / 2 if (current.avg_outdoor_temp and next_seg.avg_outdoor_temp) else None,
                    total_temp_change=(next_seg.end_indoor_temp or 0) - (current.start_indoor_temp or 0),
                    rate=current.rate,  # Keep original rate
                    is_valid=current.is_valid and next_seg.is_valid,
                )
                merged.append(merged_seg)
                i += 2  # Skip both segments
                continue
        
        merged.append(current)
        i += 1
    
    return merged


"""Smart cooling control logic for Intelligent HVAC."""
from __future__ import annotations

from datetime import time
from typing import Any

import homeassistant.util.dt as dt_util

from .const import (
    CONF_COMFORT_COOL_TEMP,
    CONF_MAX_PRECOOL_OFFSET,
    CONF_NIGHT_END,
    CONF_NIGHT_SETBACK_TEMP,
    CONF_NIGHT_START,
    CONF_PRECOOL_LEAD_MINUTES,
    DEFAULT_COMFORT_COOL_TEMP,
    DEFAULT_MAX_PRECOOL_OFFSET,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_SETBACK_TEMP,
    DEFAULT_NIGHT_START,
    DEFAULT_PRECOOL_LEAD_MINUTES,
)
from .guidance import determine_cooling_strategy
from .learning import predict_cooling_holding_margin, predict_passive_decay_rate


def _parse_time(value: str, fallback: str) -> time:
    """Parse a HH:MM string into a time object."""
    candidate = value or fallback
    try:
        hour_str, minute_str = candidate.split(":", 1)
        return time(hour=int(hour_str), minute=int(minute_str))
    except (ValueError, TypeError):
        hour_str, minute_str = fallback.split(":", 1)
        return time(hour=int(hour_str), minute=int(minute_str))


def _is_in_window(now_time: time, start: time, end: time) -> bool:
    """Return True when now is inside the configured night window."""
    if start <= end:
        return start <= now_time < end
    return now_time >= start or now_time < end


def _get_current_outdoor_temp(coordinator) -> float | None:
    """Get current outdoor temperature from resampled data or state."""
    outdoor_sensor = coordinator.outdoor_sensor
    if not outdoor_sensor:
        return None

    helper_sensor = "sensor.outdoor_temperature_most_recent"
    if outdoor_sensor.startswith("weather."):
        helper_state = coordinator.hass.states.get(helper_sensor)
        if helper_state and helper_state.state not in ("unknown", "unavailable"):
            try:
                return float(helper_state.state)
            except (TypeError, ValueError):
                pass

    # Prefer the live entity state for "current" temperature. Recorder history can be
    # stale for weather entities because attribute-only updates are not significant.
    state = coordinator.hass.states.get(outdoor_sensor)
    if state:
        try:
            return float(state.state)
        except (TypeError, ValueError):
            pass
        attrs = state.attributes
        temp = attrs.get("temperature") or attrs.get("current_temperature")
        if isinstance(temp, (int, float)):
            return float(temp)

    resampled_data = coordinator.resampled_data
    if resampled_data:
        outdoor_data = resampled_data.get(outdoor_sensor, [])
        if outdoor_data:
            latest = outdoor_data[-1]
            attributes = latest.get("attributes", {})
            if outdoor_sensor.startswith("weather."):
                temp = attributes.get("temperature")
                if isinstance(temp, (int, float)):
                    return float(temp)
            else:
                state = latest.get("state")
                if isinstance(state, (int, float)):
                    return float(state)
                temp = attributes.get("temperature") or attributes.get("current_temperature")
                if isinstance(temp, (int, float)):
                    return float(temp)

    return None


def _get_cooling_target_temp(coordinator) -> float | None:
    """Get cooling target temperature from the climate entity."""
    climate_entity = coordinator.climate_entity
    if not climate_entity:
        return None

    state = coordinator.hass.states.get(climate_entity)
    if not state:
        return None

    for attr_name in ("target_temp_high", "temperature", "target_temp_low"):
        value = state.attributes.get(attr_name)
        if isinstance(value, (int, float)):
            return float(value)

    return None


def _get_forecast_high_temp(coordinator) -> float | None:
    """Get today's forecast high if the coordinator has cached it."""
    data = coordinator.data or {}
    forecast_high = data.get("forecast_high_temp")
    if isinstance(forecast_high, (int, float)):
        return float(forecast_high)

    weather_state = coordinator.hass.states.get("weather.home")
    if weather_state:
        forecast = weather_state.attributes.get("forecast")
        if isinstance(forecast, list) and forecast:
            temp = forecast[0].get("temperature")
            if isinstance(temp, (int, float)):
                return float(temp)

    return None


def _extract_temperature_from_record(entity_id: str, record: dict[str, Any]) -> float | None:
    """Extract a temperature value from a resampled record."""
    attributes = record.get("attributes", {})
    if entity_id.startswith("climate."):
        value = attributes.get("current_temperature")
    elif entity_id.startswith("weather."):
        value = attributes.get("temperature")
    else:
        value = record.get("state")

    if isinstance(value, (int, float)):
        return float(value)
    return None


def _calculate_entity_trend_rate(
    records: list[dict[str, Any]],
    entity_id: str,
    *,
    window_minutes: int = 30,
) -> float | None:
    """Calculate a simple temperature trend in °F/hour over a recent window."""
    if not records:
        return None

    latest = records[-1]
    latest_time = latest.get("timestamp")
    latest_temp = _extract_temperature_from_record(entity_id, latest)
    if latest_time is None or latest_temp is None:
        return None

    earliest_candidate = None
    for record in reversed(records):
        timestamp = record.get("timestamp")
        if timestamp is None:
            continue
        elapsed_minutes = (latest_time - timestamp).total_seconds() / 60.0
        if elapsed_minutes >= window_minutes:
            earliest_candidate = record
            break
        earliest_candidate = record

    if earliest_candidate is None:
        return None

    earliest_time = earliest_candidate.get("timestamp")
    earliest_temp = _extract_temperature_from_record(entity_id, earliest_candidate)
    if earliest_time is None or earliest_temp is None:
        return None

    elapsed_hours = (latest_time - earliest_time).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return None

    return (latest_temp - earliest_temp) / elapsed_hours


def _calculate_average_trend_rate(
    coordinator,
    entity_ids: list[str],
    *,
    window_minutes: int = 30,
) -> float | None:
    """Average recent trend rates across the provided entities."""
    rates = []
    for entity_id in entity_ids:
        records = coordinator.resampled_data.get(entity_id, [])
        rate = _calculate_entity_trend_rate(
            records,
            entity_id,
            window_minutes=window_minutes,
        )
        if rate is not None:
            rates.append(rate)

    if not rates:
        return None

    return sum(rates) / len(rates)


def calculate_smart_cooling_setpoint(coordinator) -> dict[str, Any] | None:
    """Calculate a recommended cooling setpoint and strategy."""
    data = coordinator.data
    if not data:
        return None

    model = data.get("model")
    indoor_temp = data.get("normalized_indoor_temp")
    if not model or indoor_temp is None:
        return None

    outdoor_temp = _get_current_outdoor_temp(coordinator)
    if outdoor_temp is None:
        return None

    comfort_temp = float(
        coordinator.options.get(
            CONF_COMFORT_COOL_TEMP,
            coordinator.config.get(CONF_COMFORT_COOL_TEMP, DEFAULT_COMFORT_COOL_TEMP),
        )
    )
    night_setback_temp = float(
        coordinator.options.get(
            CONF_NIGHT_SETBACK_TEMP,
            coordinator.config.get(CONF_NIGHT_SETBACK_TEMP, DEFAULT_NIGHT_SETBACK_TEMP),
        )
    )
    max_precool_offset = float(
        coordinator.options.get(
            CONF_MAX_PRECOOL_OFFSET,
            coordinator.config.get(CONF_MAX_PRECOOL_OFFSET, DEFAULT_MAX_PRECOOL_OFFSET),
        )
    )
    precool_lead_minutes = float(
        coordinator.options.get(
            CONF_PRECOOL_LEAD_MINUTES,
            coordinator.config.get(CONF_PRECOOL_LEAD_MINUTES, DEFAULT_PRECOOL_LEAD_MINUTES),
        )
    )
    night_start = _parse_time(
        coordinator.options.get(
            CONF_NIGHT_START,
            coordinator.config.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
        ),
        DEFAULT_NIGHT_START,
    )
    night_end = _parse_time(
        coordinator.options.get(
            CONF_NIGHT_END,
            coordinator.config.get(CONF_NIGHT_END, DEFAULT_NIGHT_END),
        ),
        DEFAULT_NIGHT_END,
    )

    now_local = dt_util.as_local(dt_util.utcnow())
    in_night_window = _is_in_window(now_local.time(), night_start, night_end)

    current_target = _get_cooling_target_temp(coordinator)
    holding_margin = predict_cooling_holding_margin(model, indoor_temp, outdoor_temp)
    passive_decay_rate = predict_passive_decay_rate(model, indoor_temp, outdoor_temp)
    forecast_high_temp = _get_forecast_high_temp(coordinator)
    indoor_trend_rate = _calculate_average_trend_rate(
        coordinator,
        [coordinator.climate_entity, *coordinator.indoor_sensors] if coordinator.climate_entity else coordinator.indoor_sensors,
    )
    if coordinator.outdoor_sensor and coordinator.outdoor_sensor.startswith("weather."):
        outdoor_trend_entities = ["sensor.outdoor_temperature_most_recent"]
    else:
        outdoor_trend_entities = [coordinator.outdoor_sensor] if coordinator.outdoor_sensor else []

    outdoor_trend_rate = _calculate_average_trend_rate(
        coordinator,
        outdoor_trend_entities,
    )

    strategy_data = determine_cooling_strategy(
        indoor_temp=indoor_temp,
        outdoor_temp=outdoor_temp,
        comfort_temp=comfort_temp,
        night_setback_temp=night_setback_temp,
        max_precool_offset=max_precool_offset,
        precool_lead_minutes=precool_lead_minutes,
        in_night_window=in_night_window,
        current_target=current_target,
        holding_margin=holding_margin,
        passive_decay_rate=passive_decay_rate,
        forecast_high_temp=forecast_high_temp,
        indoor_trend_rate=indoor_trend_rate,
        outdoor_trend_rate=outdoor_trend_rate,
    )

    return {
        "recommended_temp": strategy_data["recommended_temp"],
        "strategy": strategy_data["strategy"],
        "guidance_message": strategy_data["guidance_message"],
        "comfort_temp": comfort_temp,
        "night_setback_temp": night_setback_temp,
        "max_precool_offset": max_precool_offset,
        "precool_lead_minutes": precool_lead_minutes,
        "in_night_window": in_night_window,
        "holding_margin": round(holding_margin, 3) if holding_margin is not None else None,
        "current_target": current_target,
        "indoor_temp": indoor_temp,
        "outdoor_temp": outdoor_temp,
        "forecast_high_temp": forecast_high_temp,
        "passive_decay_rate": strategy_data["passive_decay_rate"],
        "passive_cooling_rate": strategy_data["passive_cooling_rate"],
        "passive_gain_rate": strategy_data["passive_gain_rate"],
        "forecast_heat_excess": strategy_data["forecast_heat_excess"],
        "forecast_risk_score": strategy_data["forecast_risk_score"],
        "natural_cooling_favorable": strategy_data["natural_cooling_favorable"],
        "window_favorable": strategy_data["window_favorable"],
        "anticipate_warming_active": strategy_data["anticipate_warming_active"],
        "indoor_trend_rate": strategy_data["indoor_trend_rate"],
        "outdoor_trend_rate": strategy_data["outdoor_trend_rate"],
        "indoor_temp_falling": strategy_data["indoor_temp_falling"],
        "indoor_temp_rising": strategy_data["indoor_temp_rising"],
        "indoor_temp_stable": strategy_data["indoor_temp_stable"],
        "house_is_materially_hot": strategy_data["house_is_materially_hot"],
        "house_is_super_hot": strategy_data["house_is_super_hot"],
        "window_guidance_advisory_only": strategy_data["window_guidance_advisory_only"],
    }

"""Temperature normalization and aggregation."""
import logging
from typing import Any

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def convert_temperature(
    temperature: float,
    from_unit: str,
    to_unit: str,
) -> float:
    """
    Convert temperature between units.
    
    Args:
        temperature: Temperature value
        from_unit: Source unit (can be UnitOfTemperature constant or string like '°C', '°F')
        to_unit: Target unit
    
    Returns:
        Converted temperature
    """
    if from_unit == to_unit:
        return temperature
    
    # Normalize unit strings (handle both constants and string values)
    def _is_fahrenheit(unit: str) -> bool:
        """Check if unit is Fahrenheit."""
        return unit in (
            UnitOfTemperature.FAHRENHEIT,
            "°F",
            "fahrenheit",
            "F",
        )
    
    def _is_celsius(unit: str) -> bool:
        """Check if unit is Celsius."""
        return unit in (
            UnitOfTemperature.CELSIUS,
            "°C",
            "celsius",
            "C",
        )
    
    # Convert to Celsius first if needed
    if _is_fahrenheit(from_unit):
        celsius = (temperature - 32) * 5 / 9
    elif _is_celsius(from_unit):
        celsius = temperature
    else:
        # Unknown unit, assume it's already in target unit
        _LOGGER.warning("Unknown temperature unit: %s, assuming no conversion needed", from_unit)
        return temperature
    
    # Convert from Celsius to target unit
    if _is_fahrenheit(to_unit):
        return (celsius * 9 / 5) + 32
    elif _is_celsius(to_unit):
        return celsius
    else:
        # Unknown target unit, return Celsius
        _LOGGER.warning("Unknown target temperature unit: %s, returning Celsius", to_unit)
        return celsius


def normalize_indoor_temperature(
    hass: HomeAssistant,
    sensor_ids: list[str],
    weights: dict[str, float] | None = None,
    resampled_data: dict[str, list[dict[str, Any]]] | None = None,
) -> float | None:
    """
    Calculate normalized indoor temperature from multiple sensors.
    
    Args:
        hass: Home Assistant instance
        sensor_ids: List of sensor entity IDs
        weights: Optional dictionary mapping sensor_id to weight (default: equal weights)
        resampled_data: Optional pre-resampled data (if None, uses current state)
    
    Returns:
        Normalized temperature in HA's configured unit, or None if no valid data
    """
    if not sensor_ids:
        return None
    
    # Get HA's configured temperature unit
    ha_unit = hass.config.units.temperature_unit
    
    values = []
    total_weight = 0.0
    
    for sensor_id in sensor_ids:
        try:
            temp_value = None
            sensor_unit = ha_unit
            
            # Try resampled data first if available
            if resampled_data and sensor_id in resampled_data:
                sensor_data = resampled_data[sensor_id]
                if sensor_data:
                    latest = sensor_data[-1]
                    attributes = latest.get("attributes", {})
                    
                    # For climate entities, temperature is in current_temperature attribute
                    if sensor_id.startswith("climate."):
                        temp_value = _parse_state(attributes.get("current_temperature"))
                        if temp_value is not None:
                            sensor_unit = attributes.get("temperature_unit", ha_unit)
                    else:
                        # For sensors, temperature is in state
                        temp_value = _parse_state(latest.get("state"))
                        if temp_value is not None:
                            sensor_unit = attributes.get("unit_of_measurement", ha_unit)
            
            # Fall back to current state if resampled data didn't work
            if temp_value is None:
                state = hass.states.get(sensor_id)
                if state is None:
                    _LOGGER.debug("Sensor %s not found", sensor_id)
                    continue
                
                # Try to get temperature from entity (handles sensor, climate, weather)
                temp_value, sensor_unit = get_temperature_from_entity(
                    hass,
                    sensor_id,
                )
            
            if temp_value is not None:
                # Convert to HA unit if needed
                if sensor_unit != ha_unit:
                    temp_value = convert_temperature(
                        temp_value,
                        sensor_unit,
                        ha_unit,
                    )
                
                weight = 1.0
                if weights and sensor_id in weights:
                    weight = weights[sensor_id]
                
                values.append((temp_value, weight))
                total_weight += weight
                
        except Exception as err:
            _LOGGER.warning(
                "Error getting temperature from %s: %s",
                sensor_id,
                err,
            )
            continue
    
    if not values:
        _LOGGER.warning(
            "No valid temperature values from sensors: %s. "
            "Check if sensors exist, are online, and have numeric temperature values.",
            sensor_ids,
        )
        return None
    
    # Calculate weighted average
    if total_weight > 0:
        weighted_sum = sum(temp * weight for temp, weight in values)
        return weighted_sum / total_weight
    else:
        # Fallback to simple average
        return sum(temp for temp, _ in values) / len(values)


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


def get_temperature_from_entity(
    hass: HomeAssistant,
    entity_id: str,
    attribute: str | None = None,
) -> tuple[float | None, str]:
    """
    Get temperature value from an entity (sensor, climate, or weather).
    
    Args:
        hass: Home Assistant instance
        entity_id: Entity ID
        attribute: Optional attribute name (e.g., 'temperature' for weather)
    
    Returns:
        Tuple of (temperature_value, unit) or (None, unit) if unavailable
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None, ""
    
    ha_unit = hass.config.units.temperature_unit
    
    # Handle different entity types
    if entity_id.startswith("sensor."):
        temp_value = _parse_state(state.state)
        unit = state.attributes.get("unit_of_measurement", ha_unit)
        
    elif entity_id.startswith("climate."):
        # Climate entities have current_temperature attribute
        temp_value = state.attributes.get("current_temperature")
        if temp_value is not None:
            temp_value = _parse_state(str(temp_value))
        unit = state.attributes.get("temperature_unit", ha_unit)
        
    elif entity_id.startswith("weather."):
        # Weather entities have temperature attribute
        attr_name = attribute or "temperature"
        temp_value = state.attributes.get(attr_name)
        if temp_value is not None:
            temp_value = _parse_state(str(temp_value))
        unit = state.attributes.get("temperature_unit", ha_unit)
        
    else:
        # Try to parse as generic sensor
        temp_value = _parse_state(state.state)
        unit = state.attributes.get("unit_of_measurement", ha_unit)
    
    if temp_value is not None and unit != ha_unit:
        try:
            temp_value = convert_temperature(temp_value, unit, ha_unit)
            unit = ha_unit
        except Exception as err:
            _LOGGER.warning(
                "Error converting temperature from %s to %s: %s",
                unit,
                ha_unit,
                err,
            )
    
    return temp_value, unit


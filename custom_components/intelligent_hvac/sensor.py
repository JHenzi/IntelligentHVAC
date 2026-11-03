"""Sensor platform for Intelligent HVAC."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .const import (
    CONF_COMFORT_COOL_TEMP,
    CONF_PRECOOL_LEAD_MINUTES,
    DEFAULT_COMFORT_COOL_TEMP,
    DEFAULT_PRECOOL_LEAD_MINUTES,
)
from .coordinator import IntelligentHVACCoordinator
from .learning import (
    predict_active_cooling_rate,
    predict_cooling_holding_margin,
    predict_passive_decay_rate,
    predict_time_to_target_heat,
    predict_time_to_target_cool,
)
from .smart_control import calculate_smart_cooling_setpoint

_LOGGER = logging.getLogger(__name__)


def _get_current_outdoor_temp(
    hass: HomeAssistant,
    outdoor_sensor: str | None,
    resampled_data: dict[str, list[dict[str, Any]]] | None = None,
) -> float | None:
    """
    Get current outdoor temperature from sensor or resampled data.
    
    Returns:
        Outdoor temperature in °F, or None if unavailable
    """
    if not outdoor_sensor:
        return None

    helper_sensor = "sensor.outdoor_temperature_most_recent"
    if outdoor_sensor.startswith("weather."):
        helper_state = hass.states.get(helper_sensor)
        if helper_state and helper_state.state not in ("unknown", "unavailable"):
            try:
                return float(helper_state.state)
            except (ValueError, TypeError):
                pass

    # Prefer the live entity state for "current" temperature. Recorder history can be
    # stale for weather entities because attribute-only updates are not significant.
    state = hass.states.get(outdoor_sensor)
    if state:
        try:
            temp = float(state.state)
            return temp
        except (ValueError, TypeError):
            pass

        attrs = state.attributes
        if outdoor_sensor.startswith("weather."):
            temp = attrs.get("temperature")
        else:
            temp = attrs.get("temperature") or attrs.get("current_temperature")

        if isinstance(temp, (int, float)):
            return float(temp)

    # Fall back to recorder-backed resampled data when live state is unavailable.
    if resampled_data:
        outdoor_data = resampled_data.get(outdoor_sensor, [])
        if outdoor_data:
            # Get most recent record
            latest = outdoor_data[-1]
            attributes = latest.get("attributes", {})
            
            # For weather entities, temperature is in attributes
            if outdoor_sensor.startswith("weather."):
                temp = attributes.get("temperature")
                if isinstance(temp, (int, float)):
                    return float(temp)
            else:
                # For sensor entities, try state first
                state = latest.get("state")
                if isinstance(state, (int, float)):
                    return float(state)
                # Fallback to attributes
                temp = attributes.get("temperature") or attributes.get("current_temperature")
                if isinstance(temp, (int, float)):
                    return float(temp)
    
    return None


def _get_cooling_target_temp(
    hass: HomeAssistant,
    climate_entity: str | None,
) -> float | None:
    """Get the active cooling target temperature from the climate entity."""
    if not climate_entity:
        return None

    state = hass.states.get(climate_entity)
    if not state:
        return None

    attrs = state.attributes

    for attr_name in ("target_temp_low", "temperature"):
        value = attrs.get(attr_name)
        if isinstance(value, (int, float)):
            return float(value)

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Intelligent HVAC sensors from a config entry."""
    _LOGGER.info("Setting up Intelligent HVAC sensors for entry: %s", entry.title)
    
    # Get coordinator from hass.data
    coordinator: IntelligentHVACCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
    # Create all sensors
    sensors = [
        StatusSensor(coordinator, entry),
        LearnedIndoorTempSensor(coordinator, entry),
        ThermalModelConfidenceSensor(coordinator, entry),
        # Heating sensors (winter)
        HeatingRate32FSensor(coordinator, entry),
        TimeToTarget5FHeatSensor(coordinator, entry),
        # Cooling sensors (summer)
        CoolingRate75FSensor(coordinator, entry),
        TimeToTarget5FCoolSensor(coordinator, entry),
        CoolingHoldingMarginSensor(coordinator, entry),
        PreCoolLeadTimeSensor(coordinator, entry),
        RecommendedCoolingSetpointSensor(coordinator, entry),
        CoolingStrategySensor(coordinator, entry),
        # Passive sensors (year-round)
        PassiveDecayRate32FSensor(coordinator, entry),
        PassiveGainRate75FSensor(coordinator, entry),
        TimeToDrop5FPassiveSensor(coordinator, entry),
    ]
    
    async_add_entities(sensors)
    _LOGGER.info("Intelligent HVAC sensor platform setup complete: %d sensors", len(sensors))


class IntelligentHVACSensor(CoordinatorEntity, SensorEntity):
    """Base class for Intelligent HVAC sensors."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
        name: str,
        unique_id: str,
        icon: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        # Entity IDs become sensor.intelligent_hvac_<name> on a fresh install,
        # which is what the comfort package and dashboard reference.
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_id}"
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Intelligent HVAC",
            "manufacturer": "Intelligent HVAC",
            "model": entry.title,
            "entry_type": DeviceEntryType.SERVICE,
        }


class StatusSensor(IntelligentHVACSensor):
    """Sensor showing the current status of the thermal model."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the status sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Thermal Model Status",
            unique_id="status",
            icon="mdi:information",
        )
    
    @property
    def native_value(self) -> str:
        """Return the current status."""
        if not self.coordinator.last_update_success:
            return "Error"
        
        if not self.coordinator.data:
            return "Initializing"
        
        # Check if we have normalized temp (basic data collection working)
        normalized_temp = self.coordinator.data.get("normalized_indoor_temp")
        if normalized_temp is None:
            # Check why - do we have resampled data?
            if not self.coordinator.resampled_data:
                return "No History Data"
            else:
                return "Collecting Data"
        
        # Check if we have segments and model (learning status)
        segments = self.coordinator.data.get("segments", [])
        model = self.coordinator.data.get("model")
        
        if model and model.confidence_score >= 0.5:
            return "Trained"
        elif segments:
            return "Learning"
        else:
            return "Collecting Data"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "last_update_success": self.coordinator.last_update_success,
            "last_update": self.coordinator.data.get("last_update") if self.coordinator.data else None,
        }
        
        if self.coordinator.data:
            attrs["normalized_temp_available"] = (
                self.coordinator.data.get("normalized_indoor_temp") is not None
            )
            attrs["entities_tracked"] = len(self.coordinator.resampled_data)
            attrs["climate_entity"] = self.coordinator.climate_entity
            attrs["indoor_sensors"] = self.coordinator.indoor_sensors
            attrs["outdoor_sensor"] = self.coordinator.outdoor_sensor
            attrs["training_days"] = self.coordinator.training_days
            attrs["sampling_interval_minutes"] = self.coordinator.sampling_interval
            
            # Show which entities have resampled data
            if self.coordinator.resampled_data:
                attrs["entities_with_data"] = list(self.coordinator.resampled_data.keys())
                attrs["entities_without_data"] = [
                    eid for eid in (
                        [self.coordinator.climate_entity] +
                        self.coordinator.indoor_sensors +
                        [self.coordinator.outdoor_sensor]
                    )
                    if eid and eid not in self.coordinator.resampled_data
                ]
                if attrs["entities_without_data"]:
                    attrs["diagnostic"] = (
                        f"{len(attrs['entities_without_data'])} entity(ies) have no history data. "
                        "This is normal for new entities or if history retention is short."
                    )
            
            # Diagnostic: check if entities are currently available (for normalization fallback)
            # Only warn if entity is missing AND we don't have historical data for it
            missing_entities = []
            entities_with_data_set = set(self.coordinator.resampled_data.keys()) if self.coordinator.resampled_data else set()
            
            for entity_id in (
                [self.coordinator.climate_entity] +
                self.coordinator.indoor_sensors +
                [self.coordinator.outdoor_sensor]
            ):
                if entity_id:
                    # Only warn if missing AND we don't have historical data
                    if not self.hass.states.get(entity_id) and entity_id not in entities_with_data_set:
                        missing_entities.append(entity_id)
            
            if missing_entities:
                attrs["missing_entities"] = missing_entities
                attrs["diagnostic"] = (
                    f"Warning: {len(missing_entities)} entity(ies) not found and have no history data. "
                    "Learning may be limited until entities are available."
                )
            
            # Add normalization diagnostic
            normalized_temp = self.coordinator.data.get("normalized_indoor_temp") if self.coordinator.data else None
            if normalized_temp is None:
                # Check current sensor states
                sensor_states = {}
                for sensor_id in self.coordinator.indoor_sensors:
                    state = self.hass.states.get(sensor_id)
                    if state:
                        sensor_states[sensor_id] = {
                            "state": state.state,
                            "unit": state.attributes.get("unit_of_measurement", "unknown"),
                        }
                    else:
                        sensor_states[sensor_id] = {"error": "Entity not found"}
                attrs["sensor_states"] = sensor_states
                attrs["normalization_issue"] = (
                    "Cannot calculate normalized temp. "
                    "Check sensor_states attribute for details."
                )
            
            # Add learning progress diagnostics
            segments = self.coordinator.data.get("segments", []) if self.coordinator.data else []
            model = self.coordinator.data.get("model") if self.coordinator.data else None
            
            if segments:
                # Count segments by type
                from .const import (
                    SEGMENT_TYPE_PASSIVE_DECAY,
                    SEGMENT_TYPE_PASSIVE_GAIN,
                    SEGMENT_TYPE_ACTIVE_HEATING,
                    SEGMENT_TYPE_ACTIVE_COOLING,
                )
                attrs["total_segments"] = len(segments)
                attrs["passive_decay_segments"] = len([s for s in segments if s.type == SEGMENT_TYPE_PASSIVE_DECAY])
                attrs["active_heating_segments"] = len([s for s in segments if s.type == SEGMENT_TYPE_ACTIVE_HEATING])
                attrs["active_cooling_segments"] = len([s for s in segments if s.type == SEGMENT_TYPE_ACTIVE_COOLING])
                attrs["valid_segments"] = len([s for s in segments if s.is_valid])
            else:
                attrs["total_segments"] = 0
                attrs["learning_status"] = "No segments identified yet. Waiting for HVAC activity."
            
            if model:
                attrs["model_confidence"] = round(model.confidence_score * 100, 1)
                attrs["model_functions"] = list(model.time_to_target_functions.keys())
            else:
                attrs["model_confidence"] = None
                attrs["model_status"] = "No model created yet. Need more segments or data."
        
        return attrs


class LearnedIndoorTempSensor(IntelligentHVACSensor):
    """Sensor for normalized indoor temperature."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the learned indoor temperature sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Learned Indoor Temperature",
            unique_id="learned_indoor_temp",
            icon="mdi:thermometer",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the normalized indoor temperature."""
        if not self.coordinator.data:
            return None
        
        # Try to get from coordinator data
        temp = self.coordinator.data.get("normalized_indoor_temp")
        if temp is not None:
            return round(temp, 1)
        
        # Fallback: calculate from current state
        from .normalization import normalize_indoor_temperature
        temp = normalize_indoor_temperature(
            self.hass,
            self.coordinator.indoor_sensors,
            weights=None,
            resampled_data=None,  # Use current state
        )
        return round(temp, 1) if temp is not None else None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return self.hass.config.units.temperature_unit
    
    @property
    def available(self) -> bool:
        """Return True if sensor is available."""
        return self.native_value is not None
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}
        if self.coordinator.data:
            attrs["last_update"] = self.coordinator.data.get("last_update")
        attrs["sensors_used"] = self.coordinator.indoor_sensors
        return attrs


class ThermalModelConfidenceSensor(IntelligentHVACSensor):
    """Sensor for thermal model confidence score."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the confidence sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Thermal Model Confidence",
            unique_id="thermal_model_confidence",
            icon="mdi:chart-line",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the confidence score (0-100%)."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Return confidence as percentage (0-100)
        confidence_pct = model.confidence_score * 100.0
        
        # Return None if confidence is too low (< 10%) to indicate insufficient data
        if confidence_pct < 10.0:
            return None
        
        return round(confidence_pct, 1)
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "%"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        
        attrs = {
            "model_id": model.model_id if model else "unknown",
            "training_days": self.coordinator.training_days,
        }
        
        if model:
            attrs.update({
                "confidence_raw": round(model.confidence_score, 3),
                "last_updated": model.last_updated.isoformat() if model.last_updated else None,
                "functions_available": list(model.time_to_target_functions.keys()),
            })
        
        return attrs


class HeatingRate32FSensor(IntelligentHVACSensor):
    """Sensor for heating rate at 32°F (winter reference temperature)."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the heating rate sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Heating Rate at 32°F",
            unique_id="heating_rate_at_32f",
            icon="mdi:thermometer-arrow-up",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the heating rate in °F/hr."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if heating function is available
        if "heating" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 32°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 32.0
        
        # Calculate heating rate from active heating model
        # Rate = temp_change / time, so we predict time for 1°F change
        time_minutes = predict_time_to_target_heat(model, 1.0, outdoor_temp, normalized_temp)
        if time_minutes is not None and time_minutes > 0:
            # Convert to rate: 1°F / (time_minutes / 60 hours) = 60 / time_minutes °F/hr
            rate = 60.0 / time_minutes
            return round(rate, 2)
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "°F/hr"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "How fast the home heats when HVAC is active",
            "reference_temp_f": outdoor_temp if outdoor_temp else 32,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
            attrs["functions_available"] = list(model.time_to_target_functions.keys())
            attrs["heating_function_exists"] = "heating" in model.time_to_target_functions
            # Debug: show what prediction returns
            if "heating" in model.time_to_target_functions:
                normalized_temp = data.get("normalized_indoor_temp")
                if normalized_temp is not None:
                    from .learning import predict_time_to_target_heat
                    time_minutes = predict_time_to_target_heat(model, 1.0, outdoor_temp or 32.0, normalized_temp)
                    attrs["debug_time_for_1f"] = time_minutes
                    if time_minutes is not None and time_minutes > 0:
                        attrs["debug_calculated_rate"] = round(60.0 / time_minutes, 2)
        return attrs


class PassiveDecayRate32FSensor(IntelligentHVACSensor):
    """Sensor for passive decay rate at 32°F (winter - home loses heat)."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the passive decay rate sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Passive Decay Rate at 32°F",
            unique_id="passive_decay_rate_at_32f",
            icon="mdi:thermometer-arrow-down",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the passive decay rate in °F/hr."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if passive decay function is available
        if "passive_decay" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 32°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 32.0
        
        # Predict passive decay rate at current outdoor temp
        rate = predict_passive_decay_rate(model, normalized_temp, outdoor_temp)
        if rate is not None:
            return round(rate, 2)
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "°F/hr"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "How fast the home loses heat when HVAC is off (passive decay)",
            "reference_temp_f": outdoor_temp if outdoor_temp else 32,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        return attrs


class TimeToTarget5FHeatSensor(IntelligentHVACSensor):
    """Sensor for time to raise temperature by 5°F."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the time-to-target sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Time to Target 5°F Heat",
            unique_id="time_to_target_5f_heat",
            icon="mdi:timer",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the time in minutes."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if heating function is available
        if "heating" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 32°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 32.0
        
        # Predict time to raise temp by 5°F
        time_minutes = predict_time_to_target_heat(model, 5.0, outdoor_temp, normalized_temp)
        if time_minutes is not None:
            return round(time_minutes, 1)
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "min"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "Time to raise temperature by 5°F given current outdoor temperature",
            "outdoor_temp_f": outdoor_temp,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        return attrs


class CoolingRate75FSensor(IntelligentHVACSensor):
    """Sensor for cooling rate at 75°F (summer reference)."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the cooling rate sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Cooling Rate at 75°F",
            unique_id="cooling_rate_at_75f",
            icon="mdi:thermometer-arrow-down",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the cooling rate in °F/hr."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if cooling function is available
        if "cooling" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 75°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 75.0
        
        # Calculate cooling rate from active cooling model
        # Rate = temp_change / time, so we predict time for 1°F change
        time_minutes = predict_time_to_target_cool(model, 1.0, outdoor_temp, normalized_temp)
        if time_minutes is not None and time_minutes > 0:
            # Convert to rate: -1°F / (time_minutes / 60 hours) = -60 / time_minutes °F/hr
            # (negative because cooling reduces temperature)
            rate = -60.0 / time_minutes
            return round(rate, 2)
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "°F/hr"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "How fast the home cools when HVAC is active",
            "reference_temp_f": outdoor_temp if outdoor_temp else 75,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        return attrs


class TimeToTarget5FCoolSensor(IntelligentHVACSensor):
    """Sensor for time to lower temperature by 5°F."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the time-to-target cooling sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Time to Target 5°F Cool",
            unique_id="time_to_target_5f_cool",
            icon="mdi:timer-outline",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the time in minutes."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if cooling function is available
        if "cooling" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 75°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 75.0
        
        # Predict time to lower temp by 5°F
        time_minutes = predict_time_to_target_cool(model, 5.0, outdoor_temp, normalized_temp)
        if time_minutes is not None:
            return round(time_minutes, 1)
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "min"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "Time to lower temperature by 5°F given current outdoor temperature",
            "outdoor_temp_f": outdoor_temp,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        return attrs


class CoolingHoldingMarginSensor(IntelligentHVACSensor):
    """Sensor for net cooling headroom under current conditions."""

    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the cooling holding margin sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Cooling Holding Margin",
            unique_id="cooling_holding_margin",
            icon="mdi:gauge",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the net cooling headroom in °F/hr."""
        data = self.coordinator.data
        if not data:
            return None

        model = data.get("model")
        normalized_temp = data.get("normalized_indoor_temp")
        if not model or normalized_temp is None:
            return None

        if "cooling" not in model.time_to_target_functions:
            return None
        if "passive_decay" not in model.time_to_target_functions:
            return None

        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        if outdoor_temp is None:
            return None

        margin = predict_cooling_holding_margin(model, normalized_temp, outdoor_temp)
        return round(margin, 2) if margin is not None else None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "°F/hr"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        normalized_temp = data.get("normalized_indoor_temp") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )

        attrs = {
            "description": "Net cooling capacity after subtracting passive heat gain",
            "indoor_temp_f": normalized_temp,
            "outdoor_temp_f": outdoor_temp,
        }

        if model and normalized_temp is not None and outdoor_temp is not None:
            active_rate = predict_active_cooling_rate(model, normalized_temp, outdoor_temp)
            passive_rate = predict_passive_decay_rate(model, normalized_temp, outdoor_temp)
            attrs["active_cooling_rate_f_per_hour"] = (
                round(active_rate, 3) if active_rate is not None else None
            )
            attrs["passive_gain_rate_f_per_hour"] = (
                round(max(0.0, passive_rate), 3) if passive_rate is not None else None
            )
            attrs["cooling_can_keep_up"] = (
                self.native_value is not None and self.native_value > 0
            )

        return attrs


class PreCoolLeadTimeSensor(IntelligentHVACSensor):
    """Sensor for how long current pre-cool buffer will last."""

    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the pre-cool lead time sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Pre-cool Lead Time",
            unique_id="precool_lead_time",
            icon="mdi:snowflake-alert",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return minutes of lead time needed to get back to comfort."""
        data = self.coordinator.data
        if not data:
            return None

        model = data.get("model")
        normalized_temp = data.get("normalized_indoor_temp")
        if not model or normalized_temp is None:
            return None

        comfort_temp = float(
            self.coordinator.options.get(
                CONF_COMFORT_COOL_TEMP,
                self.coordinator.config.get(
                    CONF_COMFORT_COOL_TEMP,
                    DEFAULT_COMFORT_COOL_TEMP,
                ),
            )
        )

        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        if outdoor_temp is None:
            return None

        current_target = _get_cooling_target_temp(self.hass, self.coordinator.climate_entity)
        if current_target is None:
            current_target = comfort_temp

        # Use the warmer of indoor temp or current target as the recovery starting point.
        recovery_start_temp = max(normalized_temp, current_target)
        temp_delta = max(0.0, recovery_start_temp - comfort_temp)

        if temp_delta <= 0:
            return 0.0

        lead_minutes = predict_time_to_target_cool(
            model,
            temp_delta,
            outdoor_temp,
            recovery_start_temp,
        )
        if lead_minutes is None:
            return None

        return round(lead_minutes, 1)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "min"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        normalized_temp = data.get("normalized_indoor_temp") if data else None
        comfort_temp = float(
            self.coordinator.options.get(
                CONF_COMFORT_COOL_TEMP,
                self.coordinator.config.get(
                    CONF_COMFORT_COOL_TEMP,
                    DEFAULT_COMFORT_COOL_TEMP,
                ),
            )
        )
        configured_lead = float(
            self.coordinator.options.get(
                CONF_PRECOOL_LEAD_MINUTES,
                self.coordinator.config.get(
                    CONF_PRECOOL_LEAD_MINUTES,
                    DEFAULT_PRECOOL_LEAD_MINUTES,
                ),
            )
        )
        target_temp = _get_cooling_target_temp(self.hass, self.coordinator.climate_entity)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        margin = (
            predict_cooling_holding_margin(model, normalized_temp, outdoor_temp)
            if model and normalized_temp is not None and outdoor_temp is not None
            else None
        )
        recovery_start_temp = (
            max(normalized_temp, target_temp)
            if normalized_temp is not None and target_temp is not None
            else None
        )

        return {
            "description": "Minutes of lead time needed to cool from the current setback/indoor temperature back to comfort",
            "indoor_temp_f": normalized_temp,
            "cooling_target_f": target_temp,
            "comfort_temp_f": comfort_temp,
            "outdoor_temp_f": outdoor_temp,
            "cooling_holding_margin_f_per_hour": round(margin, 3) if margin is not None else None,
            "recovery_start_temp_f": recovery_start_temp,
            "configured_precool_lead_minutes": configured_lead,
            "precool_recommended_now": self.native_value is not None and self.native_value >= configured_lead,
            "cooling_can_keep_up": margin > 0 if margin is not None else None,
        }


class RecommendedCoolingSetpointSensor(IntelligentHVACSensor):
    """Sensor for the recommended cooling setpoint right now."""

    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the recommended cooling setpoint sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Recommended Cooling Setpoint",
            unique_id="recommended_cooling_setpoint",
            icon="mdi:thermostat-auto",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the recommended cooling setpoint."""
        recommendation = calculate_smart_cooling_setpoint(self.coordinator)
        if not recommendation:
            return None
        return recommendation["recommended_temp"]

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return self.hass.config.units.temperature_unit

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        recommendation = calculate_smart_cooling_setpoint(self.coordinator)
        if not recommendation:
            return {}
        return {
            "strategy": recommendation["strategy"],
            "guidance_message": recommendation["guidance_message"],
            "comfort_temp_f": recommendation["comfort_temp"],
            "night_setback_temp_f": recommendation["night_setback_temp"],
            "precool_lead_minutes": recommendation["precool_lead_minutes"],
            "max_precool_offset_f": recommendation["max_precool_offset"],
            "in_night_window": recommendation["in_night_window"],
            "holding_margin_f_per_hour": recommendation["holding_margin"],
            "current_target_f": recommendation["current_target"],
            "indoor_temp_f": recommendation["indoor_temp"],
            "outdoor_temp_f": recommendation["outdoor_temp"],
            "forecast_high_temp_f": recommendation["forecast_high_temp"],
            "passive_decay_rate_f_per_hour": recommendation["passive_decay_rate"],
            "passive_cooling_rate_f_per_hour": recommendation["passive_cooling_rate"],
            "passive_gain_rate_f_per_hour": recommendation["passive_gain_rate"],
            "forecast_heat_excess_f": recommendation["forecast_heat_excess"],
            "forecast_risk_score": recommendation["forecast_risk_score"],
            "natural_cooling_favorable": recommendation["natural_cooling_favorable"],
            "window_favorable": recommendation["window_favorable"],
            "anticipate_warming_active": recommendation["anticipate_warming_active"],
            "indoor_trend_rate_f_per_hour": recommendation["indoor_trend_rate"],
            "outdoor_trend_rate_f_per_hour": recommendation["outdoor_trend_rate"],
            "indoor_temp_falling": recommendation["indoor_temp_falling"],
            "indoor_temp_rising": recommendation["indoor_temp_rising"],
            "indoor_temp_stable": recommendation["indoor_temp_stable"],
            "house_is_materially_hot": recommendation["house_is_materially_hot"],
            "window_guidance_advisory_only": recommendation["window_guidance_advisory_only"],
        }


class CoolingStrategySensor(IntelligentHVACSensor):
    """Sensor describing the current cooling strategy."""

    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the cooling strategy sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Cooling Strategy",
            unique_id="cooling_strategy",
            icon="mdi:hvac",
        )

    @property
    def native_value(self) -> str | None:
        """Return the current strategy name."""
        recommendation = calculate_smart_cooling_setpoint(self.coordinator)
        if not recommendation:
            return None
        return recommendation["strategy"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return strategy details."""
        recommendation = calculate_smart_cooling_setpoint(self.coordinator)
        if not recommendation:
            return {}
        return {
            "recommended_temp_f": recommendation["recommended_temp"],
            "guidance_message": recommendation["guidance_message"],
            "comfort_temp_f": recommendation["comfort_temp"],
            "night_setback_temp_f": recommendation["night_setback_temp"],
            "in_night_window": recommendation["in_night_window"],
            "holding_margin_f_per_hour": recommendation["holding_margin"],
            "current_target_f": recommendation["current_target"],
            "indoor_temp_f": recommendation["indoor_temp"],
            "outdoor_temp_f": recommendation["outdoor_temp"],
            "forecast_high_temp_f": recommendation["forecast_high_temp"],
            "natural_cooling_favorable": recommendation["natural_cooling_favorable"],
            "window_favorable": recommendation["window_favorable"],
            "anticipate_warming_active": recommendation["anticipate_warming_active"],
            "forecast_risk_score": recommendation["forecast_risk_score"],
            "indoor_trend_rate_f_per_hour": recommendation["indoor_trend_rate"],
            "outdoor_trend_rate_f_per_hour": recommendation["outdoor_trend_rate"],
            "indoor_temp_falling": recommendation["indoor_temp_falling"],
            "indoor_temp_rising": recommendation["indoor_temp_rising"],
            "indoor_temp_stable": recommendation["indoor_temp_stable"],
            "window_guidance_advisory_only": recommendation["window_guidance_advisory_only"],
        }


class PassiveGainRate75FSensor(IntelligentHVACSensor):
    """Sensor for passive gain rate at 75°F (summer - home gains heat)."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the passive gain rate sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Passive Gain Rate at 75°F",
            unique_id="passive_gain_rate_at_75f",
            icon="mdi:thermometer-plus",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the passive gain rate in °F/hr."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if passive decay function is available
        if "passive_decay" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 75°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 75.0
        
        # Predict passive decay rate at current outdoor temp
        # For passive gain, we invert the decay rate (positive when outdoor > indoor)
        rate = predict_passive_decay_rate(model, normalized_temp, outdoor_temp)
        if rate is not None:
            # Invert for gain (positive rate when outdoor is warmer)
            return -rate if rate < 0 else rate
        
        return None
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "°F/hr"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        attrs = {
            "description": "How fast the home gains heat when HVAC is off (passive gain)",
            "reference_temp_f": outdoor_temp if outdoor_temp else 75,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
        }
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        return attrs


class TimeToDrop5FPassiveSensor(IntelligentHVACSensor):
    """Sensor for time to drop temperature by 5°F via passive decay (HVAC off)."""
    
    def __init__(
        self,
        coordinator: IntelligentHVACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the time-to-drop sensor."""
        super().__init__(
            coordinator,
            entry,
            name="Time to Drop 5°F Passive",
            unique_id="time_to_drop_5f_passive",
            icon="mdi:timer-sand",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        """Return the time in minutes to drop 5°F via passive decay."""
        data = self.coordinator.data
        if not data:
            return None
        
        model = data.get("model")
        if not model:
            return None
        
        # Check if passive decay function is available
        if "passive_decay" not in model.time_to_target_functions:
            return None
        
        # Get current indoor temp
        normalized_temp = data.get("normalized_indoor_temp")
        if normalized_temp is None:
            return None
        
        # Get outdoor temp (use 32°F as reference if not available)
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        ) or 32.0
        
        # Predict passive decay rate at current temps
        rate = predict_passive_decay_rate(model, normalized_temp, outdoor_temp)
        if rate is None:
            return None
        
        # Rate is negative (losing heat), so we need abs() for calculation
        # Time (hours) = temperature_drop / abs(rate)
        # Time (minutes) = (temperature_drop / abs(rate)) * 60
        temp_drop = 5.0  # Drop by 5°F
        abs_rate = abs(rate)
        
        if abs_rate < 0.01:  # Avoid division by zero
            return None
        
        time_hours = temp_drop / abs_rate
        time_minutes = time_hours * 60.0
        
        return round(time_minutes, 1)
    
    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "min"
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        model = data.get("model") if data else None
        normalized_temp = data.get("normalized_indoor_temp") if data else None
        outdoor_temp = _get_current_outdoor_temp(
            self.hass,
            self.coordinator.outdoor_sensor,
            self.coordinator.resampled_data,
        )
        
        attrs = {
            "description": "Time to drop temperature by 5°F via passive decay (HVAC off) given current temperatures",
            "indoor_temp_f": normalized_temp,
            "outdoor_temp_f": outdoor_temp,
            "uses_actual_outdoor_temp": outdoor_temp is not None,
            "temperature_drop_f": 5.0,
        }
        
        # Calculate and include the decay rate for reference
        if model and normalized_temp is not None and "passive_decay" in model.time_to_target_functions:
            rate = predict_passive_decay_rate(model, normalized_temp, outdoor_temp or 32.0)
            if rate is not None:
                attrs["passive_decay_rate_f_per_hour"] = round(rate, 3)
        
        if model:
            attrs["model_confidence"] = round(model.confidence_score * 100, 1)
            attrs["low_confidence_warning"] = model.confidence_score < 0.5
        
        return attrs

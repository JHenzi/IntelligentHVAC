"""Sensor platform for Intelligent HVAC."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntelligentHVACCoordinator
from .learning import (
    predict_passive_decay_rate,
    predict_time_to_target_heat,
    predict_time_to_target_cool,
)

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
    
    # Try to get from resampled data first (most recent value)
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
    
    # Fallback to current state
    state = hass.states.get(outdoor_sensor)
    if state:
        # Try state value
        try:
            temp = float(state.state)
            return temp
        except (ValueError, TypeError):
            pass
        
        # Try attributes
        attrs = state.attributes
        if outdoor_sensor.startswith("weather."):
            temp = attrs.get("temperature")
        else:
            temp = attrs.get("temperature") or attrs.get("current_temperature")
        
        if isinstance(temp, (int, float)):
            return float(temp)
    
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
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_id}"
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Intelligent HVAC",
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
        time_minutes = predict_time_to_target_heat(model, 1.0, outdoor_temp)
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
                    time_minutes = predict_time_to_target_heat(model, 1.0, outdoor_temp or 32.0)
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
        time_minutes = predict_time_to_target_heat(model, 5.0, outdoor_temp)
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
        time_minutes = predict_time_to_target_cool(model, 1.0, outdoor_temp)
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
        time_minutes = predict_time_to_target_cool(model, 5.0, outdoor_temp)
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

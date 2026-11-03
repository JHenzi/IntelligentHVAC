"""Config flow for Intelligent HVAC integration."""
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    DOMAIN,
    CONF_CLIMATE_ENTITY,
    CONF_INDOOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_INDOOR_SENSOR_WEIGHTS,
    CONF_SAMPLING_INTERVAL_MINUTES,
    CONF_COMFORT_COOL_TEMP,
    CONF_NIGHT_SETBACK_TEMP,
    CONF_MAX_PRECOOL_OFFSET,
    CONF_PRECOOL_LEAD_MINUTES,
    CONF_NIGHT_START,
    CONF_NIGHT_END,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_SAMPLING_INTERVAL_MINUTES,
    DEFAULT_COMFORT_COOL_TEMP,
    DEFAULT_NIGHT_SETBACK_TEMP,
    DEFAULT_MAX_PRECOOL_OFFSET,
    DEFAULT_PRECOOL_LEAD_MINUTES,
    DEFAULT_NIGHT_START,
    DEFAULT_NIGHT_END,
)

_LOGGER = logging.getLogger(__name__)


class IntelligentHVACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intelligent HVAC."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._errors: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        self._errors = {}

        if user_input is not None:
            # Validate user input
            self._errors = await self._validate_input(self.hass, user_input)
            
            if not self._errors:
                # Create entry
                return self.async_create_entry(
                    title=f"Intelligent HVAC - {user_input[CONF_CLIMATE_ENTITY]}",
                    data=user_input,
                )

        # Build form schema with searchable entity selectors
        # EntitySelector allows typing/searching - we validate domains in _validate_input
        # Indoor sensors: allow sensor and climate domains (climate devices have current_temperature)
        # Outdoor sensor: allow sensor, climate, and weather domains
        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_INDOOR_SENSORS): EntitySelector(
                    EntitySelectorConfig(
                        multiple=True,
                        # Note: domain list not supported, validate in _validate_input
                    )
                ),
                vol.Required(CONF_OUTDOOR_SENSOR): EntitySelector(
                    EntitySelectorConfig(
                        # Note: domain list not supported, validate in _validate_input
                    )
                ),
                vol.Optional(
                    CONF_TRAINING_DAYS, default=DEFAULT_TRAINING_DAYS
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=365,
                        mode="box",
                        step=1,
                    )
                ),
                vol.Optional(
                    CONF_SAMPLING_INTERVAL_MINUTES,
                    default=DEFAULT_SAMPLING_INTERVAL_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=60,
                        mode="box",
                        step=1,
                    )
                ),
                vol.Optional(
                    CONF_COMFORT_COOL_TEMP,
                    default=DEFAULT_COMFORT_COOL_TEMP,
                ): NumberSelector(
                    NumberSelectorConfig(min=60, max=85, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_NIGHT_SETBACK_TEMP,
                    default=DEFAULT_NIGHT_SETBACK_TEMP,
                ): NumberSelector(
                    NumberSelectorConfig(min=60, max=85, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_MAX_PRECOOL_OFFSET,
                    default=DEFAULT_MAX_PRECOOL_OFFSET,
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=6, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_PRECOOL_LEAD_MINUTES,
                    default=DEFAULT_PRECOOL_LEAD_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=360, mode="box", step=5)
                ),
                vol.Optional(
                    CONF_NIGHT_START,
                    default=DEFAULT_NIGHT_START,
                ): TextSelector(
                    TextSelectorConfig(type="text")
                ),
                vol.Optional(
                    CONF_NIGHT_END,
                    default=DEFAULT_NIGHT_END,
                ): TextSelector(
                    TextSelectorConfig(type="text")
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=self._errors,
        )

    async def _validate_input(
        self, hass: HomeAssistant, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Validate user input."""
        errors: dict[str, str] = {}

        # Validate climate entity exists
        climate_entity = user_input.get(CONF_CLIMATE_ENTITY)
        if climate_entity:
            state = hass.states.get(climate_entity)
            if state is None:
                errors[CONF_CLIMATE_ENTITY] = "entity_not_found"
            elif not climate_entity.startswith("climate."):
                errors[CONF_CLIMATE_ENTITY] = "must_be_climate_entity"

        # Validate indoor sensors
        indoor_sensors = user_input.get(CONF_INDOOR_SENSORS, [])
        if not indoor_sensors:
            # Handle case where it might be a string instead of list
            if isinstance(indoor_sensors, str):
                indoor_sensors = [indoor_sensors]
            else:
                indoor_sensors = []
        
        if len(indoor_sensors) == 0:
            errors[CONF_INDOOR_SENSORS] = "at_least_one_required"
        else:
            # Ensure it's a list
            if not isinstance(indoor_sensors, list):
                indoor_sensors = [indoor_sensors]
            
            for sensor in indoor_sensors:
                state = hass.states.get(sensor)
                if state is None:
                    errors[CONF_INDOOR_SENSORS] = "entity_not_found"
                    break
                # Validate domain (sensor or climate)
                if not (sensor.startswith("sensor.") or sensor.startswith("climate.")):
                    errors[CONF_INDOOR_SENSORS] = "must_be_sensor_or_climate"
                    break

        # Validate outdoor sensor
        outdoor_sensor = user_input.get(CONF_OUTDOOR_SENSOR)
        if outdoor_sensor:
            state = hass.states.get(outdoor_sensor)
            if state is None:
                errors[CONF_OUTDOOR_SENSOR] = "entity_not_found"
            # Validate domain (sensor, climate, or weather)
            elif not (
                outdoor_sensor.startswith("sensor.")
                or outdoor_sensor.startswith("climate.")
                or outdoor_sensor.startswith("weather.")
            ):
                errors[CONF_OUTDOOR_SENSOR] = "must_be_sensor_climate_or_weather"

        return errors

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        # In newer HA versions, config_entry is handled by the base class
        return IntelligentHVACOptionsFlowHandler()


class IntelligentHVACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Intelligent HVAC."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current values
        # config_entry is a property from the base class
        options = self.config_entry.options or {}
        data = self.config_entry.data

        # Get available entities for selection
        climate_entities = sorted(self.hass.states.async_entity_ids("climate"))
        sensor_entities = sorted(self.hass.states.async_entity_ids("sensor"))
        weather_entities = sorted(self.hass.states.async_entity_ids("weather"))
        
        # Combine for outdoor sensor (sensor, climate, weather)
        outdoor_options = sorted(sensor_entities + climate_entities + weather_entities)
        
        # Current values
        current_climate = data.get(CONF_CLIMATE_ENTITY, "")
        current_indoor = data.get(CONF_INDOOR_SENSORS, [])
        current_outdoor = data.get(CONF_OUTDOOR_SENSOR, "")

        # Build form schema with current values
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CLIMATE_ENTITY,
                    default=current_climate,
                ): EntitySelector(
                    EntitySelectorConfig(domain="climate")
                ),
                vol.Optional(
                    CONF_INDOOR_SENSORS,
                    default=current_indoor,
                ): EntitySelector(
                    EntitySelectorConfig(
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_OUTDOOR_SENSOR,
                    default=current_outdoor,
                ): EntitySelector(
                    EntitySelectorConfig(
                        # Allow all entity types for outdoor
                    )
                ),
                vol.Optional(
                    CONF_TRAINING_DAYS,
                    default=options.get(CONF_TRAINING_DAYS, data.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS)),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=365,
                        mode="box",
                        step=1,
                    )
                ),
                vol.Optional(
                    CONF_SAMPLING_INTERVAL_MINUTES,
                    default=options.get(
                        CONF_SAMPLING_INTERVAL_MINUTES,
                        data.get(CONF_SAMPLING_INTERVAL_MINUTES, DEFAULT_SAMPLING_INTERVAL_MINUTES),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=60,
                        mode="box",
                        step=1,
                    )
                ),
                vol.Optional(
                    CONF_COMFORT_COOL_TEMP,
                    default=options.get(
                        CONF_COMFORT_COOL_TEMP,
                        data.get(CONF_COMFORT_COOL_TEMP, DEFAULT_COMFORT_COOL_TEMP),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=60, max=85, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_NIGHT_SETBACK_TEMP,
                    default=options.get(
                        CONF_NIGHT_SETBACK_TEMP,
                        data.get(CONF_NIGHT_SETBACK_TEMP, DEFAULT_NIGHT_SETBACK_TEMP),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=60, max=85, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_MAX_PRECOOL_OFFSET,
                    default=options.get(
                        CONF_MAX_PRECOOL_OFFSET,
                        data.get(CONF_MAX_PRECOOL_OFFSET, DEFAULT_MAX_PRECOOL_OFFSET),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=6, mode="box", step=0.5)
                ),
                vol.Optional(
                    CONF_PRECOOL_LEAD_MINUTES,
                    default=options.get(
                        CONF_PRECOOL_LEAD_MINUTES,
                        data.get(CONF_PRECOOL_LEAD_MINUTES, DEFAULT_PRECOOL_LEAD_MINUTES),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=360, mode="box", step=5)
                ),
                vol.Optional(
                    CONF_NIGHT_START,
                    default=options.get(
                        CONF_NIGHT_START,
                        data.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                    ),
                ): TextSelector(
                    TextSelectorConfig(type="text")
                ),
                vol.Optional(
                    CONF_NIGHT_END,
                    default=options.get(
                        CONF_NIGHT_END,
                        data.get(CONF_NIGHT_END, DEFAULT_NIGHT_END),
                    ),
                ): TextSelector(
                    TextSelectorConfig(type="text")
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)

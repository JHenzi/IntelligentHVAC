"""Config flow tests (run against a real, in-memory Home Assistant)."""
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.intelligent_hvac.const import DOMAIN

VALID = {
    "climate_entity": "climate.thermostat",
    "indoor_sensors": ["sensor.living_room"],
    "outdoor_sensor": "weather.home",
}


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.thermostat", "heat", {"current_temperature": 68})
    hass.states.async_set("sensor.living_room", "68.5", {"unit_of_measurement": "°F"})
    hass.states.async_set("weather.home", "sunny", {"temperature": 40})
    hass.states.async_set("light.kitchen", "on")


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_form_creates_entry(hass: HomeAssistant) -> None:
    _seed_states(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.intelligent_hvac.async_setup_entry", return_value=True
    ) as setup_entry:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["climate_entity"] == "climate.thermostat"
    assert len(setup_entry.mock_calls) == 1


async def test_unknown_climate_entity(hass: HomeAssistant) -> None:
    _seed_states(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**VALID, "climate_entity": "climate.nope"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"climate_entity": "entity_not_found"}


async def test_indoor_sensor_wrong_domain(hass: HomeAssistant) -> None:
    _seed_states(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**VALID, "indoor_sensors": ["light.kitchen"]}
    )
    assert result["errors"] == {"indoor_sensors": "must_be_sensor_or_climate"}


async def test_outdoor_sensor_wrong_domain(hass: HomeAssistant) -> None:
    _seed_states(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**VALID, "outdoor_sensor": "light.kitchen"}
    )
    assert result["errors"] == {"outdoor_sensor": "must_be_sensor_climate_or_weather"}

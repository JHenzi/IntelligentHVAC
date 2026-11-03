"""Service and entry-lifecycle tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.intelligent_hvac.const import DOMAIN

PKG = "custom_components.intelligent_hvac"


def _fake_coordinator(climate="climate.thermostat"):
    coord = MagicMock()
    coord.climate_entity = climate
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
async def services(hass: HomeAssistant):
    """Integration services registered, with two fake entries."""
    assert await async_setup_component(hass, DOMAIN, {})
    a, b = _fake_coordinator("climate.up"), _fake_coordinator("climate.down")
    hass.data[DOMAIN] = {"A": {"coordinator": a}, "B": {"coordinator": b}}
    return a, b


async def test_force_update_without_entry_id_updates_all(hass, services):
    a, b = services
    await hass.services.async_call(DOMAIN, "force_update", {}, blocking=True)
    a.async_request_refresh.assert_awaited_once()
    b.async_request_refresh.assert_awaited_once()


async def test_force_update_single_entry(hass, services):
    a, b = services
    await hass.services.async_call(DOMAIN, "force_update", {"entry_id": "B"}, blocking=True)
    a.async_request_refresh.assert_not_awaited()
    b.async_request_refresh.assert_awaited_once()


async def _apply(hass, state, attrs, recommended):
    hass.states.async_set("climate.up", state, attrs)
    calls = async_mock_service(hass, "climate", "set_temperature")
    with patch(f"{PKG}.calculate_smart_cooling_setpoint",
               return_value={"recommended_temp": recommended, "strategy": "comfort_hold"}):
        await hass.services.async_call(
            DOMAIN, "apply_smart_cooling_setpoint", {"climate_entity": "climate.up"}, blocking=True
        )
    return calls


async def test_apply_setpoint_sets_new_temperature(hass, services):
    calls = await _apply(hass, "cool", {"temperature": 76}, 73.0)
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": "climate.up", "temperature": 73.0}


async def test_apply_setpoint_skips_when_unchanged(hass, services):
    calls = await _apply(hass, "cool", {"temperature": 73}, 73.0)
    assert calls == []


async def test_apply_setpoint_heat_cool_sets_high_keeps_low(hass, services):
    calls = await _apply(hass, "heat_cool", {"target_temp_low": 58, "target_temp_high": 78}, 74.0)
    assert calls[0].data == {"entity_id": "climate.up", "target_temp_low": 58.0, "target_temp_high": 74.0}


async def test_apply_setpoint_no_recommendation_does_nothing(hass, services):
    hass.states.async_set("climate.up", "cool", {"temperature": 76})
    calls = async_mock_service(hass, "climate", "set_temperature")
    with patch(f"{PKG}.calculate_smart_cooling_setpoint", return_value=None):
        await hass.services.async_call(
            DOMAIN, "apply_smart_cooling_setpoint", {"climate_entity": "climate.up"}, blocking=True
        )
    assert calls == []


async def test_apply_setpoint_rejects_non_climate_entity(hass, services):
    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN, "apply_smart_cooling_setpoint", {"climate_entity": "light.kitchen"}, blocking=True
        )


async def test_options_change_reloads_once(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Intelligent HVAC - climate.thermostat",
        data={"climate_entity": "climate.thermostat", "indoor_sensors": ["sensor.a"],
              "outdoor_sensor": "sensor.b"},
    )
    entry.add_to_hass(hass)
    fake = MagicMock()
    fake.return_value.async_config_entry_first_refresh = AsyncMock()
    with patch(f"{PKG}.coordinator.IntelligentHVACCoordinator", fake), \
         patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert fake.call_count == 1

        hass.config_entries.async_update_entry(entry, options={"training_days": 14})
        await hass.async_block_till_done()

    assert fake.call_count == 2  # initial setup + exactly one reload
    assert entry.options == {"training_days": 14}

"""Intelligent HVAC integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .smart_control import calculate_smart_cooling_setpoint

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_FORCE_UPDATE = "force_update"
SERVICE_APPLY_SMART_COOLING_SETPOINT = "apply_smart_cooling_setpoint"

ATTR_ENTRY_ID = "entry_id"
ATTR_CLIMATE_ENTITY = "climate_entity"

FORCE_UPDATE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})
APPLY_SETPOINT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CLIMATE_ENTITY): cv.entity_domain("climate"),
    }
)

# Don't re-send a setpoint the thermostat already has (cloud thermostats
# rate-limit, and every call shows up in the thermostat's history).
SETPOINT_TOLERANCE = 0.25


def _coordinators(hass: HomeAssistant) -> dict[str, Any]:
    """Return {entry_id: coordinator} for all loaded entries."""
    return {
        entry_id: data["coordinator"]
        for entry_id, data in hass.data.get(DOMAIN, {}).items()
        if data.get("coordinator") is not None
    }


def _same(current: Any, wanted: float) -> bool:
    return isinstance(current, (int, float)) and abs(float(current) - wanted) < SETPOINT_TOLERANCE


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Intelligent HVAC component (services)."""

    async def handle_force_update(call: ServiceCall) -> None:
        """Relearn now: one entry if entry_id is given, otherwise all."""
        coordinators = _coordinators(hass)
        entry_id = call.data.get(ATTR_ENTRY_ID)
        if entry_id:
            if entry_id not in coordinators:
                _LOGGER.error("Intelligent HVAC entry %s not found", entry_id)
                return
            coordinators = {entry_id: coordinators[entry_id]}
        for eid, coordinator in coordinators.items():
            _LOGGER.info("Manually triggering update for entry %s", eid)
            await coordinator.async_request_refresh()

    async def handle_apply_smart_cooling_setpoint(call: ServiceCall) -> None:
        """Apply the recommended smart cooling setpoint to the climate entity."""
        coordinators = _coordinators(hass)
        entry_id = call.data.get(ATTR_ENTRY_ID)
        climate_entity = call.data.get(ATTR_CLIMATE_ENTITY)
        coordinator = None

        if entry_id:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                _LOGGER.error("Intelligent HVAC entry %s not found", entry_id)
                return
        elif climate_entity:
            for eid, candidate in coordinators.items():
                if candidate.climate_entity == climate_entity:
                    entry_id, coordinator = eid, candidate
                    break
            if coordinator is None:
                _LOGGER.error("No Intelligent HVAC entry found for climate entity %s", climate_entity)
                return
        elif len(coordinators) == 1:
            entry_id, coordinator = next(iter(coordinators.items()))
        else:
            _LOGGER.error(
                "climate_entity or entry_id required for "
                "intelligent_hvac.apply_smart_cooling_setpoint when %d entries exist",
                len(coordinators),
            )
            return

        if not coordinator.climate_entity:
            _LOGGER.error("Entry %s has no climate entity configured", entry_id)
            return

        recommendation = calculate_smart_cooling_setpoint(coordinator)
        if not recommendation:
            _LOGGER.debug("No smart cooling recommendation yet for entry %s (model still learning)", entry_id)
            return

        wanted = float(recommendation["recommended_temp"])
        climate_state = hass.states.get(coordinator.climate_entity)
        service_data: dict[str, Any] = {"entity_id": coordinator.climate_entity}
        attrs = climate_state.attributes if climate_state else {}

        if climate_state and climate_state.state == "heat_cool":
            if _same(attrs.get("target_temp_high"), wanted):
                _LOGGER.debug("Cooling setpoint already %.1f°F; nothing to do", wanted)
                return
            current_low = attrs.get("target_temp_low")
            if not isinstance(current_low, (int, float)):
                current_low = 60.0
            service_data["target_temp_low"] = min(float(current_low), 60.0)
            service_data["target_temp_high"] = wanted
        else:
            if _same(attrs.get("temperature"), wanted):
                _LOGGER.debug("Cooling setpoint already %.1f°F; nothing to do", wanted)
                return
            service_data["temperature"] = wanted

        await hass.services.async_call("climate", "set_temperature", service_data, blocking=True)
        _LOGGER.info(
            "Applied smart cooling setpoint %.1f°F to %s using strategy %s",
            wanted,
            coordinator.climate_entity,
            recommendation["strategy"],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_UPDATE, handle_force_update, schema=FORCE_UPDATE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_SMART_COOLING_SETPOINT,
        handle_apply_smart_cooling_setpoint,
        schema=APPLY_SETPOINT_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Intelligent HVAC from a config entry."""
    _LOGGER.info("Setting up Intelligent HVAC config entry: %s", entry.title)

    try:
        hass.data.setdefault(DOMAIN, {})

        from .coordinator import IntelligentHVACCoordinator

        coordinator = IntelligentHVACCoordinator(hass, entry)
        hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(async_update_options))

        _LOGGER.info("Intelligent HVAC setup complete for entry: %s", entry.title)
        return True

    except ConfigEntryNotReady:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise
    except Exception as err:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _LOGGER.error("Failed to set up Intelligent HVAC: %s", err, exc_info=True)
        raise ConfigEntryNotReady(f"Failed to set up integration: {err}") from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Intelligent HVAC config entry: %s", entry.title)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        if not domain_data:
            hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed: reload once. The coordinator reads options over data."""
    _LOGGER.info("Updating options for Intelligent HVAC entry: %s", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)

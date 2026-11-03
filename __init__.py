"""Intelligent HVAC integration."""
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.reload import async_setup_reload_service

from .const import DOMAIN, PLATFORMS
from .smart_control import calculate_smart_cooling_setpoint

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Intelligent HVAC component."""
    _LOGGER.info("Setting up Intelligent HVAC integration")
    
    # Set up reload service for hot reload during development
    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)
    
    # Register service to manually trigger update
    async def handle_force_update(call):
        """Handle force update service call."""
        entry_id = call.data.get("entry_id")
        if not entry_id:
            _LOGGER.error("entry_id required for intelligent_hvac.force_update service")
            return
        
        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            _LOGGER.error("Entry %s not found", entry_id)
            return
        
        coordinator = hass.data[DOMAIN][entry_id].get("coordinator")
        if coordinator:
            _LOGGER.info("Manually triggering update for entry %s", entry_id)
            await coordinator.async_request_refresh()
        else:
            _LOGGER.error("Coordinator not found for entry %s", entry_id)
    
    hass.services.async_register(
        DOMAIN,
        "force_update",
        handle_force_update,
    )

    async def handle_apply_smart_cooling_setpoint(call):
        """Apply the recommended smart cooling setpoint to the climate entity."""
        entries = hass.data.get(DOMAIN, {})
        entry_id = call.data.get("entry_id")
        climate_entity = call.data.get("climate_entity")
        resolved_entry_id = entry_id
        coordinator = None

        if entry_id:
            if entry_id not in entries:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            coordinator = entries[entry_id].get("coordinator")
        elif climate_entity:
            for candidate_entry_id, entry_data in entries.items():
                candidate = entry_data.get("coordinator")
                if candidate and candidate.climate_entity == climate_entity:
                    resolved_entry_id = candidate_entry_id
                    coordinator = candidate
                    break
            if coordinator is None:
                _LOGGER.error(
                    "No Intelligent HVAC entry found for climate entity %s",
                    climate_entity,
                )
                return
        elif len(entries) == 1:
            resolved_entry_id, entry_data = next(iter(entries.items()))
            coordinator = entry_data.get("coordinator")
        else:
            _LOGGER.error(
                "climate_entity or entry_id required for intelligent_hvac.apply_smart_cooling_setpoint service"
            )
            return

        if not coordinator or not coordinator.climate_entity:
            _LOGGER.error("Coordinator or climate entity not found for entry %s", resolved_entry_id)
            return

        recommendation = calculate_smart_cooling_setpoint(coordinator)
        if not recommendation:
            _LOGGER.error("No smart cooling recommendation available for entry %s", resolved_entry_id)
            return

        climate_state = hass.states.get(coordinator.climate_entity)
        service_data = {"entity_id": coordinator.climate_entity}

        if climate_state and climate_state.state == "heat_cool":
            current_low = climate_state.attributes.get("target_temp_low")
            if not isinstance(current_low, (int, float)):
                current_low = 60.0
            service_data["target_temp_low"] = min(float(current_low), 60.0)
            service_data["target_temp_high"] = recommendation["recommended_temp"]
        else:
            service_data["temperature"] = recommendation["recommended_temp"]

        await hass.services.async_call(
            "climate",
            "set_temperature",
            service_data,
            blocking=True,
        )
        _LOGGER.info(
            "Applied smart cooling setpoint %.1f°F to %s using strategy %s",
            recommendation["recommended_temp"],
            coordinator.climate_entity,
            recommendation["strategy"],
        )

    hass.services.async_register(
        DOMAIN,
        "apply_smart_cooling_setpoint",
        handle_apply_smart_cooling_setpoint,
    )
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Intelligent HVAC from a config entry."""
    _LOGGER.info("Setting up Intelligent HVAC config entry: %s", entry.title)
    
    try:
        # Initialize domain data structure
        hass.data.setdefault(DOMAIN, {})
        
        # Create coordinator
        from .coordinator import IntelligentHVACCoordinator
        coordinator = IntelligentHVACCoordinator(hass, entry)
        
        # Store coordinator
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "config": entry.data,
            "options": entry.options,
        }
        
        # Start initial data fetch (don't wait for it)
        await coordinator.async_config_entry_first_refresh()
        
        # Forward setup to sensor platform
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Set up update listener for config changes
        entry.async_on_unload(entry.add_update_listener(async_update_options))
        
        _LOGGER.info("Intelligent HVAC setup complete for entry: %s", entry.title)
        return True
        
    except Exception as err:
        _LOGGER.error("Failed to set up Intelligent HVAC: %s", err, exc_info=True)
        raise ConfigEntryNotReady(f"Failed to set up integration: {err}") from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Intelligent HVAC config entry: %s", entry.title)
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Clean up domain data
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            del hass.data[DOMAIN][entry.entry_id]
            # If no more entries, clean up domain
            if not hass.data[DOMAIN]:
                del hass.data[DOMAIN]
    
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info("Updating options for Intelligent HVAC entry: %s", entry.title)
    
    # Merge options into data (so entity changes are persisted)
    new_data = {**entry.data, **entry.options}
    hass.config_entries.async_update_entry(entry, data=new_data)
    
    # Reload the entry to apply new options
    await hass.config_entries.async_reload(entry.entry_id)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.info("Reloading Intelligent HVAC entry: %s", entry.title)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)

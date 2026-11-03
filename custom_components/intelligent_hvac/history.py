"""History API wrapper for retrieving historical data."""
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)


async def async_get_history(
    hass: HomeAssistant,
    entity_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """
    Retrieve historical state data for an entity.
    
    Args:
        hass: Home Assistant instance
        entity_id: Entity ID to retrieve history for
        start_time: Start of time range (UTC)
        end_time: End of time range (UTC)
    
    Returns:
        List of state change records with keys: state, last_changed, attributes
    """
    _LOGGER.debug(
        "Fetching history for %s from %s to %s",
        entity_id,
        start_time,
        end_time,
    )
    
    try:
        # Convert to UTC if needed
        start_utc = dt_util.as_utc(start_time)
        end_utc = dt_util.as_utc(end_time)
        
        # Use recorder's async_get_significant_states (modern API)
        recorder = get_instance(hass)
        
        # Get significant states (state changes) for the entity
        states_dict = await recorder.async_add_executor_job(
            get_significant_states,
            hass,
            start_utc,
            end_utc,
            [entity_id],
            None,  # filters
            True,  # include_start_time_state
        )
        
        if not states_dict or entity_id not in states_dict:
            _LOGGER.debug("No history found for entity %s", entity_id)
            return []
        
        # Convert to our format
        records = []
        for state in states_dict[entity_id]:
            records.append({
                "state": state.state,
                "last_changed": state.last_changed,
                "last_updated": state.last_updated,
                "attributes": dict(state.attributes) if state.attributes else {},
            })
        
        _LOGGER.debug(
            "Retrieved %d history records for %s",
            len(records),
            entity_id,
        )
        
        return records
        
    except Exception as err:
        _LOGGER.error(
            "Error fetching history for %s: %s",
            entity_id,
            err,
            exc_info=True,
        )
        return []


async def async_get_history_for_entities(
    hass: HomeAssistant,
    entity_ids: list[str],
    start_time: datetime,
    end_time: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """
    Retrieve historical data for multiple entities.
    
    Returns:
        Dictionary mapping entity_id to list of state records
    """
    results = {}
    
    for entity_id in entity_ids:
        results[entity_id] = await async_get_history(
            hass,
            entity_id,
            start_time,
            end_time,
        )
    
    return results


"""Old segment stores are archived (renamed), never deleted, and never learned from."""
from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intelligent_hvac.const import DOMAIN, STORE_SCHEMA_VERSION
from custom_components.intelligent_hvac.coordinator import IntelligentHVACCoordinator

DATA = {"climate_entity": "climate.thermostat", "indoor_sensors": ["sensor.bedroom"],
        "outdoor_sensor": "weather.home", "training_days": 30, "sampling_interval_minutes": 5}


def _write_store(hass: HomeAssistant, entry_id: str, checkpoint: dict) -> Path:
    d = Path(hass.config.config_dir) / "intelligent_hvac"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"checkpoint_{entry_id}.json").write_text(json.dumps(checkpoint))
    (d / f"segments_{entry_id}.json").write_text(json.dumps({"segments": []}))
    return d


async def test_legacy_store_is_archived(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, entry_id="E1")
    entry.add_to_hass(hass)
    # A v1 store, as on every install before this fix: no store_schema key.
    d = await hass.async_add_executor_job(_write_store, hass, "E1", {
        "entry_id": "E1", "version": "1.1.0", "last_processed_timestamp": "2026-11-03T15:00:00+00:00"})

    coord = IntelligentHVACCoordinator(hass, entry)
    await coord._async_load_checkpoint_state()

    files = sorted(p.name for p in d.iterdir() if "_E1" in p.name)
    assert files == ["checkpoint_E1.legacy-v1.json", "segments_E1.legacy-v1.json"]
    assert coord.existing_segments == [] and coord.last_processed_timestamp is None


async def test_current_store_is_kept(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, entry_id="E2")
    entry.add_to_hass(hass)
    coord = IntelligentHVACCoordinator(hass, entry)
    d = await hass.async_add_executor_job(_write_store, hass, "E2", {
        "entry_id": "E2", "store_schema": STORE_SCHEMA_VERSION, "config_hash": coord._calculate_config_hash(),
        "last_processed_timestamp": "2026-11-03T15:00:00+00:00"})

    await coord._async_load_checkpoint_state()

    assert sorted(p.name for p in d.iterdir() if "_E2" in p.name) == ["checkpoint_E2.json", "segments_E2.json"]
    assert coord.last_processed_timestamp is not None

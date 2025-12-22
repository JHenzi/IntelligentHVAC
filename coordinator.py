"""Data coordinator for Intelligent HVAC."""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_INDOOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_SAMPLING_INTERVAL_MINUTES,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_SAMPLING_INTERVAL_MINUTES,
    MIN_INCREMENTAL_FETCH_HOURS,
    VERSION,
)
from .history import async_get_history_for_entities
from .resampling import resample_to_uniform_timesteps
from .normalization import normalize_indoor_temperature
from .segmentation import identify_segments
from .learning import learn_thermal_model
from .storage import (
    async_save_raw_data,
    async_load_raw_data,
    async_save_segments,
    async_load_segments,
    async_save_model,
    async_load_model,
    async_save_checkpoint,
    async_load_checkpoint,
    async_delete_checkpoint,
)
from .models import ThermalSegment, ThermalModel

_LOGGER = logging.getLogger(__name__)

# Update interval - how often to refresh data
UPDATE_INTERVAL = timedelta(minutes=15)


class IntelligentHVACCoordinator(DataUpdateCoordinator):
    """Coordinator for Intelligent HVAC data."""
    
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Intelligent HVAC",
            update_interval=UPDATE_INTERVAL,
        )
        
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self.options = entry.options or {}
        
        # Get configuration
        self.climate_entity = self.config.get(CONF_CLIMATE_ENTITY)
        self.indoor_sensors = self.config.get(CONF_INDOOR_SENSORS, [])
        self.outdoor_sensor = self.config.get(CONF_OUTDOOR_SENSOR)
        self.training_days = self.options.get(
            CONF_TRAINING_DAYS,
            self.config.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS),
        )
        self.sampling_interval = self.options.get(
            CONF_SAMPLING_INTERVAL_MINUTES,
            self.config.get(
                CONF_SAMPLING_INTERVAL_MINUTES,
                DEFAULT_SAMPLING_INTERVAL_MINUTES,
            ),
        )
        
        # Data storage
        self.resampled_data: dict[str, list[dict[str, Any]]] = {}
        self.raw_data_loaded = False
        
        # Checkpoint state
        self.last_processed_timestamp: datetime | None = None
        self.existing_segments: list[ThermalSegment] = []
        self.existing_model: ThermalModel | None = None
        self._checkpoint_loaded = False  # Track if we've attempted to load checkpoint
        self.first_data_timestamp: datetime | None = None  # Track when data collection started
        
        _LOGGER.info(
            "Initialized coordinator: climate=%s, indoor=%s, outdoor=%s, days=%d, interval=%d min",
            self.climate_entity,
            self.indoor_sensors,
            self.outdoor_sensor,
            self.training_days,
            self.sampling_interval,
        )
    
    def _calculate_config_hash(self) -> str:
        """Calculate hash of current configuration to detect changes."""
        config_data = {
            "climate_entity": self.climate_entity,
            "indoor_sensors": sorted(self.indoor_sensors) if self.indoor_sensors else [],
            "outdoor_sensor": self.outdoor_sensor,
            "training_days": self.training_days,
            "sampling_interval": self.sampling_interval,
        }
        config_json = json.dumps(config_data, sort_keys=True)
        return hashlib.md5(config_json.encode()).hexdigest()
    
    async def _async_load_checkpoint_state(self) -> None:
        """Load checkpoint, segments, and model on startup."""
        try:
            checkpoint = await async_load_checkpoint(self.hass, self.entry.entry_id)
            
            if checkpoint:
                # Check if code version has changed (invalidate old data)
                checkpoint_version = checkpoint.get("version", "0.0.0")
                if checkpoint_version != VERSION:
                    _LOGGER.info(
                        "Code version changed (%s -> %s), invalidating checkpoint and starting fresh",
                        checkpoint_version,
                        VERSION,
                    )
                    await async_delete_checkpoint(self.hass, self.entry.entry_id)
                    # Also delete old segments and model to force fresh start
                    await self._async_delete_old_data()
                    self.existing_segments = []
                    self.existing_model = None
                    self.last_processed_timestamp = None
                    return
                
                # Check if config has changed
                current_config_hash = self._calculate_config_hash()
                checkpoint_config_hash = checkpoint.get("config_hash", "")
                
                if checkpoint_config_hash != current_config_hash:
                    _LOGGER.info(
                        "Config changed (hash mismatch), invalidating checkpoint and starting fresh"
                    )
                    await async_delete_checkpoint(self.hass, self.entry.entry_id)
                    # Also delete old segments and model to force fresh start
                    await self._async_delete_old_data()
                    self.existing_segments = []
                    self.existing_model = None
                    self.last_processed_timestamp = None
                    return
                
                # Load timestamp
                if "last_processed_timestamp" in checkpoint:
                    timestamp = checkpoint["last_processed_timestamp"]
                    if isinstance(timestamp, str):
                        timestamp = datetime.fromisoformat(timestamp)
                    self.last_processed_timestamp = timestamp
                    _LOGGER.info(
                        "Loaded checkpoint: last processed at %s",
                        self.last_processed_timestamp,
                    )
                
                # Validate timestamp is reasonable (not in future, not too old)
                now = dt_util.utcnow()
                if self.last_processed_timestamp:
                    if self.last_processed_timestamp > now:
                        _LOGGER.warning(
                            "Checkpoint timestamp is in the future, invalidating"
                        )
                        await async_delete_checkpoint(self.hass, self.entry.entry_id)
                        self.last_processed_timestamp = None
                    elif self.last_processed_timestamp < now - timedelta(days=365):
                        _LOGGER.warning(
                            "Checkpoint timestamp is too old (>1 year), invalidating"
                        )
                        await async_delete_checkpoint(self.hass, self.entry.entry_id)
                        self.last_processed_timestamp = None
            
            # Load existing segments and model
            self.existing_segments = await async_load_segments(self.hass, self.entry.entry_id)
            self.existing_model = await async_load_model(self.hass, self.entry.entry_id)
            
            if self.existing_segments:
                _LOGGER.info("Loaded %d existing segments", len(self.existing_segments))
            if self.existing_model:
                _LOGGER.info("Loaded existing model: %s", self.existing_model.model_id)
                
        except Exception as err:
            _LOGGER.warning(
                "Error loading checkpoint state (will start fresh): %s",
                err,
                exc_info=True,
            )
            self.last_processed_timestamp = None
            self.existing_segments = []
            self.existing_model = None
    
    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data."""
        _LOGGER.info("Starting data update cycle")
        
        # Load checkpoint state if not already loaded (lazy loading)
        if not self._checkpoint_loaded:
            await self._async_load_checkpoint_state()
            self._checkpoint_loaded = True
        
        try:
            # Determine fetch window based on checkpoint
            end_time = dt_util.utcnow()
            
            if self.last_processed_timestamp:
                # Incremental fetch: from last processed timestamp to now
                # But ensure we fetch at least MIN_INCREMENTAL_FETCH_HOURS to catch missed updates
                min_start_time = end_time - timedelta(hours=MIN_INCREMENTAL_FETCH_HOURS)
                start_time = min(self.last_processed_timestamp, min_start_time)
                _LOGGER.info(
                    "Incremental fetch: from %s to %s (checkpoint: %s)",
                    start_time,
                    end_time,
                    self.last_processed_timestamp,
                )
            else:
                # First run: fetch full training window
                start_time = end_time - timedelta(days=self.training_days)
                _LOGGER.info(
                    "First run: fetching full history from %s to %s (%d days)",
                    start_time,
                    end_time,
                    self.training_days,
                )
            
            # Collect all entity IDs we need
            all_entities = []
            if self.climate_entity:
                all_entities.append(self.climate_entity)
            if self.indoor_sensors:
                all_entities.extend(self.indoor_sensors)
            if self.outdoor_sensor:
                all_entities.append(self.outdoor_sensor)
            
            # Fetch history for all entities
            _LOGGER.info("Fetching history for %d entities", len(all_entities))
            history_data = await async_get_history_for_entities(
                self.hass,
                all_entities,
                start_time,
                end_time,
            )
            
            # Log what we got
            total_records = 0
            for entity_id, records in history_data.items():
                record_count = len(records) if records else 0
                total_records += record_count
                _LOGGER.info(
                    "Entity %s: %d history records",
                    entity_id,
                    record_count,
                )
            
            if total_records == 0:
                _LOGGER.warning(
                    "No history records found for any entity. "
                    "This might mean: 1) History retention is too short, "
                    "2) Entities are new, 3) Recorder is not enabled"
                )
            
            # Resample to uniform timesteps
            self.resampled_data = {}
            for entity_id, records in history_data.items():
                if records:
                    resampled = resample_to_uniform_timesteps(
                        records,
                        start_time,
                        end_time,
                        self.sampling_interval,
                        entity_id=entity_id,  # Pass entity_id for climate entity detection
                    )
                    if resampled:
                        self.resampled_data[entity_id] = resampled
                        _LOGGER.info(
                            "Resampled %s: %d records -> %d uniform timesteps",
                            entity_id,
                            len(records),
                            len(resampled),
                        )
                    else:
                        _LOGGER.warning(
                            "Resampling %s produced no results (all values may be 'unknown')",
                            entity_id,
                        )
                else:
                    _LOGGER.warning(
                        "No history records for %s - check if entity exists and has history",
                        entity_id,
                    )
            
            # Calculate normalized indoor temperature
            # Include both climate_entity (if it has temperature) and indoor_sensors
            all_indoor_entities = []
            if self.climate_entity:
                all_indoor_entities.append(self.climate_entity)
            if self.indoor_sensors:
                all_indoor_entities.extend(self.indoor_sensors)
            
            _LOGGER.info(
                "Calculating normalized indoor temperature from %d entities: %s",
                len(all_indoor_entities),
                all_indoor_entities,
            )
            
            # Try with resampled data first, fall back to current state
            normalized_temp = normalize_indoor_temperature(
                self.hass,
                all_indoor_entities,
                weights=None,  # TODO: Support weights from config
                resampled_data=self.resampled_data if self.resampled_data else None,
            )
            
            # If still None, try with current state only
            if normalized_temp is None:
                _LOGGER.warning("Normalized temp from resampled data is None, trying current state")
                normalized_temp = normalize_indoor_temperature(
                    self.hass,
                    all_indoor_entities,
                    weights=None,
                    resampled_data=None,  # Force current state
                )
            
            if normalized_temp is None:
                _LOGGER.error(
                    "Failed to calculate normalized temperature from entities: %s. "
                    "Check if entities exist, are online, and have numeric temperature values.",
                    all_indoor_entities,
                )
            else:
                _LOGGER.info("Normalized indoor temperature: %.1f", normalized_temp)
            
            # Save raw data for next time
            if self.resampled_data:
                await async_save_raw_data(self.hass, self.entry.entry_id, self.resampled_data)
            self.raw_data_loaded = True
            
            # Track first data timestamp
            if self.first_data_timestamp is None and self.resampled_data:
                # Find earliest timestamp in resampled data
                earliest = None
                for entity_data in self.resampled_data.values():
                    if entity_data:
                        first_record = entity_data[0]
                        record_time = first_record.get("timestamp")
                        if record_time and (earliest is None or record_time < earliest):
                            earliest = record_time
                if earliest:
                    self.first_data_timestamp = earliest
            
            # Step 2: Identify segments from resampled data
            new_segments = []
            if self.resampled_data and self.climate_entity:
                try:
                    all_indoor_entities = []
                    if self.climate_entity:
                        all_indoor_entities.append(self.climate_entity)
                    if self.indoor_sensors:
                        all_indoor_entities.extend(self.indoor_sensors)
                    
                    new_segments = identify_segments(
                        self.resampled_data,
                        self.climate_entity,
                        all_indoor_entities,
                        self.outdoor_sensor,
                        self.sampling_interval,
                    )
                    _LOGGER.info("Identified %d new segments", len(new_segments))
                except Exception as err:
                    _LOGGER.error("Error identifying segments: %s", err, exc_info=True)
            
            # Merge new segments with existing segments (remove duplicates)
            all_segments = self._merge_segments(self.existing_segments, new_segments)
            _LOGGER.info(
                "Total segments: %d (existing: %d, new: %d)",
                len(all_segments),
                len(self.existing_segments),
                len(new_segments),
            )
            
            # Step 3: Learn thermal model from all segments
            if all_segments:
                try:
                    self.existing_model = learn_thermal_model(
                        all_segments,
                        self.training_days,
                        self.first_data_timestamp,
                    )
                    _LOGGER.info(
                        "Learned model: confidence=%.1f%%, model_id=%s",
                        self.existing_model.confidence_score * 100,
                        self.existing_model.model_id,
                    )
                except Exception as err:
                    _LOGGER.error("Error learning model: %s", err, exc_info=True)
            
            # Save segments and model
            if all_segments:
                await async_save_segments(self.hass, self.entry.entry_id, all_segments)
                self.existing_segments = all_segments
            
            if self.existing_model:
                await async_save_model(self.hass, self.entry.entry_id, self.existing_model)
            
            # Save checkpoint after successful update
            now = dt_util.utcnow()
            checkpoint_metadata = {
                "segments_count": len(self.existing_segments),
                "model_id": self.existing_model.model_id if self.existing_model else "",
                "config_hash": self._calculate_config_hash(),
                "created_at": self.last_processed_timestamp.isoformat() if self.last_processed_timestamp else now.isoformat(),
            }
            await async_save_checkpoint(
                self.hass,
                self.entry.entry_id,
                now,
                checkpoint_metadata,
            )
            self.last_processed_timestamp = now
            
            _LOGGER.info(
                "Data update complete: %d entities, normalized temp: %s, checkpoint saved",
                len(self.resampled_data),
                normalized_temp,
            )
            
            return {
                "resampled_data": self.resampled_data,
                "normalized_indoor_temp": normalized_temp,
                "last_update": now.isoformat(),
                "last_processed_timestamp": now.isoformat(),
                "segments": self.existing_segments,
                "model": self.existing_model,
            }
            
        except Exception as err:
            _LOGGER.error("Error updating data: %s", err, exc_info=True)
            raise UpdateFailed(f"Error updating data: {err}") from err
    
    def _merge_segments(
        self,
        existing: list[ThermalSegment],
        new: list[ThermalSegment],
    ) -> list[ThermalSegment]:
        """Merge new segments with existing, removing duplicates."""
        # Create a set of existing segment keys (start_time, end_time, type)
        existing_keys = {
            (seg.start_time, seg.end_time, seg.type) for seg in existing
        }
        
        # Add new segments that don't already exist
        merged = list(existing)
        for seg in new:
            key = (seg.start_time, seg.end_time, seg.type)
            if key not in existing_keys:
                merged.append(seg)
                existing_keys.add(key)
        
        # Sort by start_time
        merged.sort(key=lambda s: s.start_time)
        
        return merged
    
    async def _async_delete_old_data(self) -> None:
        """Delete old segments and model files when version/config changes."""
        # Note: We don't need to explicitly delete files - they'll be overwritten
        # on the next update. The important thing is we've cleared the in-memory
        # state (existing_segments, existing_model) so we start fresh.
        _LOGGER.info("Old data will be overwritten on next update")


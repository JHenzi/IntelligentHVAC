"""Storage for raw data, segments, and models."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    STORAGE_DIR,
    RAW_DATA_FILE,
    SEGMENTS_FILE,
    MODEL_FILE,
    CHECKPOINT_FILE_PREFIX,
    VERSION,
)
from .models import ThermalModel, ThermalSegment

_LOGGER = logging.getLogger(__name__)


def get_storage_path(hass: HomeAssistant) -> Path:
    """Get the storage directory path."""
    config_dir = Path(hass.config.config_dir)
    storage_dir = config_dir / STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _get_entry_file_path(hass: HomeAssistant, entry_id: str, filename: str) -> Path:
    """Get entry-specific file path."""
    storage_dir = get_storage_path(hass)
    return storage_dir / filename


async def async_save_raw_data(
    hass: HomeAssistant,
    entry_id: str,
    data: dict[str, Any],
) -> None:
    """Save resampled raw data to JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{RAW_DATA_FILE.replace('.json', '')}_{entry_id}.json")
        
        # Convert datetime objects to ISO strings
        serializable_data = _make_serializable(data)
        
        def _write_file():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(serializable_data, f, indent=2, default=str)
        
        await hass.async_add_executor_job(_write_file)
        _LOGGER.debug("Saved raw data to %s", file_path)
        
    except Exception as err:
        _LOGGER.error("Error saving raw data: %s", err, exc_info=True)


async def async_load_raw_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """Load resampled raw data from JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{RAW_DATA_FILE.replace('.json', '')}_{entry_id}.json")
        
        def _file_exists():
            return file_path.exists()
        
        if not await hass.async_add_executor_job(_file_exists):
            return None
        
        def _read_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        data = await hass.async_add_executor_job(_read_file)
        _LOGGER.debug("Loaded raw data from %s", file_path)
        return data
        
    except Exception as err:
        _LOGGER.error("Error loading raw data: %s", err, exc_info=True)
        return None


async def async_save_segments(
    hass: HomeAssistant,
    entry_id: str,
    segments: list[ThermalSegment],
) -> None:
    """Save thermal segments to JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{SEGMENTS_FILE.replace('.json', '')}_{entry_id}.json")
        
        segments_data = [seg.to_dict() for seg in segments]
        
        def _write_file():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(segments_data, f, indent=2, default=str)
        
        await hass.async_add_executor_job(_write_file)
        _LOGGER.debug("Saved %d segments to %s", len(segments), file_path)
        
    except Exception as err:
        _LOGGER.error("Error saving segments: %s", err, exc_info=True)


async def async_load_segments(hass: HomeAssistant, entry_id: str) -> list[ThermalSegment]:
    """Load thermal segments from JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{SEGMENTS_FILE.replace('.json', '')}_{entry_id}.json")
        
        def _file_exists():
            return file_path.exists()
        
        if not await hass.async_add_executor_job(_file_exists):
            return []
        
        def _read_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        segments_data = await hass.async_add_executor_job(_read_file)
        segments = [
            ThermalSegment.from_dict(seg_data)
            for seg_data in segments_data
        ]
        
        _LOGGER.debug("Loaded %d segments from %s", len(segments), file_path)
        return segments
        
    except Exception as err:
        _LOGGER.error("Error loading segments: %s", err, exc_info=True)
        return []


async def async_save_model(
    hass: HomeAssistant,
    entry_id: str,
    model: ThermalModel,
) -> None:
    """Save thermal model to JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{MODEL_FILE.replace('.json', '')}_{entry_id}.json")
        
        model_data = model.to_dict()
        
        def _write_file():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(model_data, f, indent=2, default=str)
        
        await hass.async_add_executor_job(_write_file)
        _LOGGER.debug("Saved model to %s", file_path)
        
    except Exception as err:
        _LOGGER.error("Error saving model: %s", err, exc_info=True)


async def async_load_model(hass: HomeAssistant, entry_id: str) -> ThermalModel | None:
    """Load thermal model from JSON file."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{MODEL_FILE.replace('.json', '')}_{entry_id}.json")
        
        def _file_exists():
            return file_path.exists()
        
        if not await hass.async_add_executor_job(_file_exists):
            return None
        
        def _read_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        model_data = await hass.async_add_executor_job(_read_file)
        model = ThermalModel.from_dict(model_data)
        
        _LOGGER.debug("Loaded model from %s", file_path)
        return model
        
    except Exception as err:
        _LOGGER.error("Error loading model: %s", err, exc_info=True)
        return None


async def async_save_checkpoint(
    hass: HomeAssistant,
    entry_id: str,
    last_processed_timestamp: datetime,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Save checkpoint with last processed timestamp and metadata."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{CHECKPOINT_FILE_PREFIX}{entry_id}.json")
        
        checkpoint_data = {
            "entry_id": entry_id,
            "last_processed_timestamp": last_processed_timestamp.isoformat(),
            "last_update_success": True,
            "version": VERSION,  # Store code version for compatibility checking
            "created_at": metadata.get("created_at", last_processed_timestamp.isoformat()) if metadata else last_processed_timestamp.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add metadata fields if provided
        if metadata:
            checkpoint_data.update({
                "segments_count": metadata.get("segments_count", 0),
                "model_id": metadata.get("model_id", ""),
                "config_hash": metadata.get("config_hash", ""),
            })
        
        def _write_file():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2, default=str)
        
        await hass.async_add_executor_job(_write_file)
        _LOGGER.debug("Saved checkpoint to %s", file_path)
        
    except Exception as err:
        _LOGGER.error("Error saving checkpoint: %s", err, exc_info=True)


async def async_load_checkpoint(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """Load checkpoint for a specific entry."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{CHECKPOINT_FILE_PREFIX}{entry_id}.json")
        
        def _file_exists():
            return file_path.exists()
        
        if not await hass.async_add_executor_job(_file_exists):
            return None
        
        def _read_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        checkpoint_data = await hass.async_add_executor_job(_read_file)
        
        # Convert ISO string back to datetime
        if "last_processed_timestamp" in checkpoint_data and isinstance(checkpoint_data["last_processed_timestamp"], str):
            checkpoint_data["last_processed_timestamp"] = datetime.fromisoformat(checkpoint_data["last_processed_timestamp"])
        
        _LOGGER.debug("Loaded checkpoint from %s", file_path)
        return checkpoint_data
        
    except Exception as err:
        _LOGGER.warning("Error loading checkpoint (will start fresh): %s", err, exc_info=True)
        return None


async def async_delete_checkpoint(hass: HomeAssistant, entry_id: str) -> None:
    """Delete checkpoint for a specific entry (e.g., on config change)."""
    try:
        file_path = _get_entry_file_path(hass, entry_id, f"{CHECKPOINT_FILE_PREFIX}{entry_id}.json")
        
        def _delete_file():
            if file_path.exists():
                file_path.unlink()
                return True
            return False
        
        if await hass.async_add_executor_job(_delete_file):
            _LOGGER.info("Deleted checkpoint for entry %s", entry_id)
        
    except Exception as err:
        _LOGGER.error("Error deleting checkpoint: %s", err, exc_info=True)


def _make_serializable(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: _make_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    else:
        return obj


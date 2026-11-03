"""Data models for Intelligent HVAC."""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from .const import (
    SEGMENT_TYPE_PASSIVE_DECAY,
    SEGMENT_TYPE_PASSIVE_GAIN,
    SEGMENT_TYPE_ACTIVE_HEATING,
    SEGMENT_TYPE_ACTIVE_COOLING,
    SEGMENT_TYPE_STABLE,
)


@dataclass
class ThermalSegment:
    """Represents a thermal segment (heating, cooling, passive decay/gain)."""
    
    type: str  # Passive_Decay, Passive_Gain, Active_Heating, Active_Cooling, Stable
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    start_indoor_temp: float | None
    end_indoor_temp: float | None
    avg_outdoor_temp: float | None
    total_temp_change: float  # ΔTindoor
    rate: float  # ΔTindoor / Duration (in °F/hr)
    is_valid: bool  # True if valid for learning (has outdoor temp, meets thresholds)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert datetime to ISO format strings
        data["start_time"] = self.start_time.isoformat()
        data["end_time"] = self.end_time.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThermalSegment":
        """Create from dictionary."""
        # Convert ISO strings back to datetime
        if isinstance(data["start_time"], str):
            data["start_time"] = datetime.fromisoformat(data["start_time"])
        if isinstance(data["end_time"], str):
            data["end_time"] = datetime.fromisoformat(data["end_time"])
        return cls(**data)


@dataclass
class ThermalModel:
    """Represents the learned thermal model."""
    
    model_id: str
    training_days: int
    confidence_score: float  # 0.0 to 1.0 (composite score)
    time_to_target_functions: dict[str, Any]  # Heating, cooling, passive decay functions
    last_updated: datetime
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["last_updated"] = self.last_updated.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThermalModel":
        """Create from dictionary."""
        if isinstance(data["last_updated"], str):
            data["last_updated"] = datetime.fromisoformat(data["last_updated"])
        return cls(**data)


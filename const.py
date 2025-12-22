"""Constants for Intelligent HVAC integration."""
import logging

_LOGGER = logging.getLogger(__name__)

# Domain
DOMAIN = "intelligent_hvac"

# Platform
PLATFORM = "sensor"
PLATFORMS = ["sensor"]

# Version
VERSION = "1.0.0"
ISSUE_URL = "https://github.com/yourusername/intelligent_hvac/issues"

# Configuration keys
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_INDOOR_SENSORS = "indoor_sensors"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_TRAINING_DAYS = "training_days"
CONF_INDOOR_SENSOR_WEIGHTS = "indoor_sensor_weights"
CONF_SAMPLING_INTERVAL_MINUTES = "sampling_interval_minutes"

# Defaults
DEFAULT_TRAINING_DAYS = 30
DEFAULT_SAMPLING_INTERVAL_MINUTES = 5

# Segment Boundary Rules (CRITICAL - prevents garbage learning)
MIN_SEGMENT_DURATION_MINUTES = 10  # Ignore segments shorter than this
TREND_DEADBAND_F = 0.1  # |ΔT| < this → Stable (exclude from learning)
MIN_TEMP_CHANGE_F = 0.5  # Minimum |ΔT| to be considered a segment
HVAC_CYCLE_MERGE_MINUTES = 5  # Merge short off/on bursts

# Confidence Calculation Weights
CONFIDENCE_WEIGHT_R2 = 0.5  # R² importance
CONFIDENCE_WEIGHT_SAMPLES = 0.3  # Sample count importance
CONFIDENCE_WEIGHT_TEMP_RANGE = 0.2  # Temperature range coverage

# Minimum Data Requirements
MIN_PASSIVE_DECAY_SEGMENTS = 10
MIN_HEAT_CYCLES = 10  # Lowered from 50 for MVP - allows learning with fewer cycles
MIN_TRAINING_DAYS = 14

# Segment Types (algorithmic definitions)
SEGMENT_TYPE_PASSIVE_DECAY = "Passive_Decay"
SEGMENT_TYPE_PASSIVE_GAIN = "Passive_Gain"
SEGMENT_TYPE_ACTIVE_HEATING = "Active_Heating"
SEGMENT_TYPE_ACTIVE_COOLING = "Active_Cooling"
SEGMENT_TYPE_STABLE = "Stable"  # Excluded from learning

# Storage paths (relative to HA config directory)
STORAGE_DIR = "intelligent_hvac"
RAW_DATA_FILE = "raw_data.json"
SEGMENTS_FILE = "segments.json"
MODEL_FILE = "model.json"
CHECKPOINT_FILE_PREFIX = "checkpoint_"

# Incremental learning settings
MIN_INCREMENTAL_FETCH_HOURS = 24  # Always fetch at least 24h to catch missed updates

# Sensor entity IDs (output sensors)
SENSOR_CONFIDENCE = "sensor.thermal_model_confidence"
SENSOR_LEARNED_INDOOR_TEMP = "sensor.learned_indoor_temp"

# Heating sensors (winter)
SENSOR_HEATING_RATE_32F = "sensor.heating_rate_at_32f"
SENSOR_TIME_TO_TARGET_5F_HEAT = "sensor.time_to_target_5f_heat"

# Cooling sensors (summer)
SENSOR_COOLING_RATE_75F = "sensor.cooling_rate_at_75f"
SENSOR_TIME_TO_TARGET_5F_COOL = "sensor.time_to_target_5f_cool"

# Passive sensors (year-round)
SENSOR_PASSIVE_DECAY_RATE_32F = "sensor.passive_decay_rate_at_32f"  # Winter: home loses heat
SENSOR_PASSIVE_GAIN_RATE_75F = "sensor.passive_gain_rate_at_75f"  # Summer: home gains heat


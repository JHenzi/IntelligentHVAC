"""Constants for Intelligent HVAC integration."""
import logging

_LOGGER = logging.getLogger(__name__)

# Domain
DOMAIN = "intelligent_hvac"

# Platform
PLATFORM = "sensor"
PLATFORMS = ["sensor"]

# Version
VERSION = "1.1.0"  # Cooling model uses temperature difference and exposes pre-cool guidance
ISSUE_URL = "https://github.com/JHenzi/IntelligentHVAC/issues"

# Configuration keys
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_INDOOR_SENSORS = "indoor_sensors"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_TRAINING_DAYS = "training_days"
CONF_INDOOR_SENSOR_WEIGHTS = "indoor_sensor_weights"
CONF_SAMPLING_INTERVAL_MINUTES = "sampling_interval_minutes"
CONF_COMFORT_COOL_TEMP = "comfort_cool_temp"
CONF_NIGHT_SETBACK_TEMP = "night_setback_temp"
CONF_MAX_PRECOOL_OFFSET = "max_precool_offset"
CONF_PRECOOL_LEAD_MINUTES = "precool_lead_minutes"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"

# Defaults
DEFAULT_TRAINING_DAYS = 30
DEFAULT_SAMPLING_INTERVAL_MINUTES = 5
DEFAULT_COMFORT_COOL_TEMP = 72.0
DEFAULT_NIGHT_SETBACK_TEMP = 74.0
DEFAULT_MAX_PRECOOL_OFFSET = 2.0
DEFAULT_PRECOOL_LEAD_MINUTES = 60
DEFAULT_NIGHT_START = "22:00"
DEFAULT_NIGHT_END = "07:00"

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

# Version of the on-disk segment store. Bump when stored segments from older
# code must not be learned from. Old files are archived (renamed), never deleted:
# recorder history is often only ~10-30 days, so they may be the only record.
#   1: pre-2026-11 (hvac_action history collapsed by last_changed; ~32x duplicates)
#   2: last_updated ordering, clock-anchored grid, complete runs only
STORE_SCHEMA_VERSION = 2

# Segments older than this are pruned so the model follows changes to the house
# (new windows, new furnace) while still covering a full heating season.
SEGMENT_RETENTION_DAYS = 400

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
SENSOR_COOLING_HOLDING_MARGIN = "sensor.cooling_holding_margin"
SENSOR_PRECOOL_LEAD_TIME = "sensor.precool_lead_time"
SENSOR_RECOMMENDED_COOLING_SETPOINT = "sensor.recommended_cooling_setpoint"
SENSOR_COOLING_STRATEGY = "sensor.cooling_strategy"

# Passive sensors (year-round)
SENSOR_PASSIVE_DECAY_RATE_32F = "sensor.passive_decay_rate_at_32f"  # Winter: home loses heat
SENSOR_PASSIVE_GAIN_RATE_75F = "sensor.passive_gain_rate_at_75f"  # Summer: home gains heat

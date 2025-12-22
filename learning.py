"""Thermal model learning using linear regression."""
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import homeassistant.util.dt as dt_util

from .const import (
    CONFIDENCE_WEIGHT_R2,
    CONFIDENCE_WEIGHT_SAMPLES,
    CONFIDENCE_WEIGHT_TEMP_RANGE,
    MIN_PASSIVE_DECAY_SEGMENTS,
    MIN_HEAT_CYCLES,
    MIN_TRAINING_DAYS,
    SEGMENT_TYPE_PASSIVE_DECAY,
    SEGMENT_TYPE_ACTIVE_HEATING,
    SEGMENT_TYPE_ACTIVE_COOLING,
)
from .models import ThermalModel, ThermalSegment

_LOGGER = logging.getLogger(__name__)


def learn_thermal_model(
    segments: list[ThermalSegment],
    training_days: int,
    first_data_timestamp: datetime | None = None,
) -> ThermalModel:
    """
    Learn thermal model from segments using linear regression.
    
    Args:
        segments: List of thermal segments
        training_days: Number of training days configured
        first_data_timestamp: Timestamp of first data point (for age calculation)
    
    Returns:
        Learned thermal model
    """
    now = dt_util.utcnow()
    
    # Filter segments by type
    passive_decay_segments = [
        seg for seg in segments if seg.type == SEGMENT_TYPE_PASSIVE_DECAY and seg.is_valid
    ]
    active_heating_segments = [
        seg for seg in segments if seg.type == SEGMENT_TYPE_ACTIVE_HEATING and seg.is_valid
    ]
    active_cooling_segments = [
        seg for seg in segments if seg.type == SEGMENT_TYPE_ACTIVE_COOLING and seg.is_valid
    ]
    
    _LOGGER.info(
        "Learning from %d passive decay, %d active heating, %d active cooling segments",
        len(passive_decay_segments),
        len(active_heating_segments),
        len(active_cooling_segments),
    )
    
    # Learn passive decay model
    passive_decay_function = _learn_passive_decay(passive_decay_segments)
    
    # Learn active heating model
    active_heating_function = _learn_active_heating(active_heating_segments)
    
    # Learn active cooling model
    active_cooling_function = _learn_active_cooling(active_cooling_segments)
    
    # Build time_to_target_functions dict
    time_to_target_functions = {}
    if passive_decay_function:
        time_to_target_functions["passive_decay"] = passive_decay_function
    if active_heating_function:
        time_to_target_functions["heating"] = active_heating_function
    if active_cooling_function:
        time_to_target_functions["cooling"] = active_cooling_function
    
    # Calculate composite confidence score
    confidence_score = _calculate_confidence(
        segments,
        passive_decay_function,
        active_heating_function,
        training_days,
        first_data_timestamp,
    )
    
    # Generate model ID
    model_id = _generate_model_id(segments, now)
    
    return ThermalModel(
        model_id=model_id,
        training_days=training_days,
        confidence_score=confidence_score,
        time_to_target_functions=time_to_target_functions,
        last_updated=now,
    )


def _learn_passive_decay(
    segments: list[ThermalSegment],
) -> dict[str, Any] | None:
    """Learn passive decay model: Rate(Tout) = C1 * (Ti - Tout) + C2"""
    if len(segments) < MIN_PASSIVE_DECAY_SEGMENTS:
        _LOGGER.debug(
            "Insufficient passive decay segments: %d < %d",
            len(segments),
            MIN_PASSIVE_DECAY_SEGMENTS,
        )
        return None
    
    # Prepare data: X = (Ti - Tout), y = Rate
    X = []
    y = []
    
    for seg in segments:
        if seg.avg_outdoor_temp is not None and seg.start_indoor_temp is not None:
            temp_diff = seg.start_indoor_temp - seg.avg_outdoor_temp
            X.append(temp_diff)
            y.append(seg.rate)
    
    if len(X) < MIN_PASSIVE_DECAY_SEGMENTS:
        return None
    
    X = np.array(X)
    y = np.array(y)
    
    # Linear regression: y = C1 * X + C2
    # Using numpy's polyfit for degree 1 (linear)
    try:
        coeffs = np.polyfit(X, y, 1)
        C1 = float(coeffs[0])
        C2 = float(coeffs[1])
        
        # Calculate R²
        y_pred = C1 * X + C2
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        r2 = max(0.0, min(1.0, r2))  # Clamp to [0, 1]
        
        _LOGGER.info(
            "Passive decay model: Rate = %.4f * (Ti - Tout) + %.4f, R² = %.3f",
            C1,
            C2,
            r2,
        )
        
        return {
            "function_type": "linear_regression",
            "formula": "Rate(Tout) = C1 * (Ti - Tout) + C2",
            "parameters": {
                "C1": C1,
                "C2": C2,
                "R2": float(r2),
            },
        }
    except Exception as err:
        _LOGGER.warning("Error learning passive decay model: %s", err, exc_info=True)
        return None


def _learn_active_heating(
    segments: list[ThermalSegment],
) -> dict[str, Any] | None:
    """Learn active heating model: Time(dT, Tout) = C1 * dT + C2 * Tout + C3"""
    if len(segments) < MIN_HEAT_CYCLES:
        _LOGGER.debug(
            "Insufficient active heating segments: %d < %d",
            len(segments),
            MIN_HEAT_CYCLES,
        )
        return None
    
    # Prepare data: X = [dT, Tout], y = Time (duration in minutes)
    X = []
    y = []
    
    for seg in segments:
        if (
            seg.avg_outdoor_temp is not None
            and seg.start_indoor_temp is not None
            and seg.end_indoor_temp is not None
        ):
            dT = abs(seg.end_indoor_temp - seg.start_indoor_temp)
            if dT > 0:  # Only include segments with actual temperature change
                X.append([dT, seg.avg_outdoor_temp])
                y.append(seg.duration_minutes)
    
    if len(X) < MIN_HEAT_CYCLES:
        return None
    
    X = np.array(X)
    y = np.array(y)
    
    # Multiple linear regression: y = C1 * X[0] + C2 * X[1] + C3
    try:
        # Add column of ones for intercept
        X_with_intercept = np.column_stack([X, np.ones(len(X))])
        
        # Solve using least squares
        coeffs, residuals, _, _ = np.linalg.lstsq(X_with_intercept, y, rcond=None)
        C1 = float(coeffs[0])
        C2 = float(coeffs[1])
        C3 = float(coeffs[2])
        
        # Calculate R²
        y_pred = C1 * X[:, 0] + C2 * X[:, 1] + C3
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        r2 = max(0.0, min(1.0, r2))  # Clamp to [0, 1]
        
        _LOGGER.info(
            "Active heating model: Time = %.2f * dT + %.2f * Tout + %.2f, R² = %.3f",
            C1,
            C2,
            C3,
            r2,
        )
        
        return {
            "function_type": "linear_regression",
            "formula": "Time(dT, Tout) = C1 * dT + C2 * Tout + C3",
            "parameters": {
                "C1": C1,
                "C2": C2,
                "C3": C3,
                "R2": float(r2),
            },
        }
    except Exception as err:
        _LOGGER.warning("Error learning active heating model: %s", err, exc_info=True)
        return None


def _learn_active_cooling(
    segments: list[ThermalSegment],
) -> dict[str, Any] | None:
    """Learn active cooling model: Time(dT, Tout) = C1 * dT + C2 * Tout + C3"""
    # Similar to heating but for cooling segments
    if len(segments) < MIN_HEAT_CYCLES:  # Use same threshold
        _LOGGER.debug(
            "Insufficient active cooling segments: %d < %d",
            len(segments),
            MIN_HEAT_CYCLES,
        )
        return None
    
    # Prepare data: X = [dT, Tout], y = Time (duration in minutes)
    X = []
    y = []
    
    for seg in segments:
        if (
            seg.avg_outdoor_temp is not None
            and seg.start_indoor_temp is not None
            and seg.end_indoor_temp is not None
        ):
            dT = abs(seg.end_indoor_temp - seg.start_indoor_temp)
            if dT > 0:
                X.append([dT, seg.avg_outdoor_temp])
                y.append(seg.duration_minutes)
    
    if len(X) < MIN_HEAT_CYCLES:
        return None
    
    X = np.array(X)
    y = np.array(y)
    
    try:
        X_with_intercept = np.column_stack([X, np.ones(len(X))])
        coeffs, residuals, _, _ = np.linalg.lstsq(X_with_intercept, y, rcond=None)
        C1 = float(coeffs[0])
        C2 = float(coeffs[1])
        C3 = float(coeffs[2])
        
        y_pred = C1 * X[:, 0] + C2 * X[:, 1] + C3
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        r2 = max(0.0, min(1.0, r2))
        
        _LOGGER.info(
            "Active cooling model: Time = %.2f * dT + %.2f * Tout + %.2f, R² = %.3f",
            C1,
            C2,
            C3,
            r2,
        )
        
        return {
            "function_type": "linear_regression",
            "formula": "Time(dT, Tout) = C1 * dT + C2 * Tout + C3",
            "parameters": {
                "C1": C1,
                "C2": C2,
                "C3": C3,
                "R2": float(r2),
            },
        }
    except Exception as err:
        _LOGGER.warning("Error learning active cooling model: %s", err, exc_info=True)
        return None


def _calculate_confidence(
    segments: list[ThermalSegment],
    passive_decay_function: dict[str, Any] | None,
    active_heating_function: dict[str, Any] | None,
    training_days: int,
    first_data_timestamp: datetime | None,
) -> float:
    """
    Calculate composite confidence score (0.0 to 1.0).
    
    Formula:
    confidence = w1 * normalized_R2 + w2 * sample_count_score + w3 * temp_range_coverage
    """
    # Component 1: Normalized R² (from best available model)
    normalized_r2 = 0.0
    if passive_decay_function:
        r2 = passive_decay_function.get("parameters", {}).get("R2", 0.0)
        normalized_r2 = max(normalized_r2, r2)
    if active_heating_function:
        r2 = active_heating_function.get("parameters", {}).get("R2", 0.0)
        normalized_r2 = max(normalized_r2, r2)
    
    # Component 2: Sample count score
    passive_decay_segments = [
        seg for seg in segments if seg.type == SEGMENT_TYPE_PASSIVE_DECAY and seg.is_valid
    ]
    active_heating_segments = [
        seg for seg in segments if seg.type == SEGMENT_TYPE_ACTIVE_HEATING and seg.is_valid
    ]
    
    sample_count_score = 0.0
    if len(passive_decay_segments) >= MIN_PASSIVE_DECAY_SEGMENTS:
        sample_count_score += 0.5
    if len(active_heating_segments) >= MIN_HEAT_CYCLES:
        sample_count_score += 0.5
    
    # Component 3: Temperature range coverage
    outdoor_temps = [
        seg.avg_outdoor_temp
        for seg in segments
        if seg.is_valid and seg.avg_outdoor_temp is not None
    ]
    temp_range_coverage = 0.0
    if outdoor_temps:
        temp_range = max(outdoor_temps) - min(outdoor_temps)
        # Score based on range: 0-20°F = 0.0, 20-40°F = 0.5, 40+°F = 1.0
        if temp_range >= 40:
            temp_range_coverage = 1.0
        elif temp_range >= 20:
            temp_range_coverage = 0.5
        else:
            temp_range_coverage = temp_range / 40.0
    
    # Component 4: Data age (bonus for having enough historical data)
    data_age_score = 0.0
    if first_data_timestamp:
        age_days = (dt_util.utcnow() - first_data_timestamp).days
        if age_days >= training_days:
            data_age_score = 1.0
        elif age_days >= MIN_TRAINING_DAYS:
            data_age_score = age_days / training_days
    
    # Combine components with weights
    confidence = (
        CONFIDENCE_WEIGHT_R2 * normalized_r2
        + CONFIDENCE_WEIGHT_SAMPLES * sample_count_score
        + CONFIDENCE_WEIGHT_TEMP_RANGE * temp_range_coverage
        + 0.1 * data_age_score  # Small weight for data age
    )
    
    # Clamp to [0, 1]
    confidence = max(0.0, min(1.0, confidence))
    
    _LOGGER.debug(
        "Confidence calculation: R²=%.3f, samples=%.3f, range=%.3f, age=%.3f -> %.3f",
        normalized_r2,
        sample_count_score,
        temp_range_coverage,
        data_age_score,
        confidence,
    )
    
    return confidence


def _generate_model_id(segments: list[ThermalSegment], timestamp: datetime) -> str:
    """Generate unique model ID based on segments and timestamp."""
    # Create hash from segment count and timestamp
    data = f"{len(segments)}_{timestamp.isoformat()}"
    hash_obj = hashlib.md5(data.encode())
    hash_hex = hash_obj.hexdigest()[:8]
    return f"home_v1_{hash_hex}"


def predict_passive_decay_rate(
    model: ThermalModel,
    indoor_temp: float,
    outdoor_temp: float,
) -> float | None:
    """Predict passive decay rate using learned model."""
    func = model.time_to_target_functions.get("passive_decay")
    if not func:
        return None
    
    params = func.get("parameters", {})
    C1 = params.get("C1", 0.0)
    C2 = params.get("C2", 0.0)
    
    temp_diff = indoor_temp - outdoor_temp
    rate = C1 * temp_diff + C2
    
    return rate


def predict_time_to_target_heat(
    model: ThermalModel,
    temp_change: float,
    outdoor_temp: float,
) -> float | None:
    """Predict time to reach target temperature (heating) in minutes."""
    func = model.time_to_target_functions.get("heating")
    if not func:
        return None
    
    params = func.get("parameters", {})
    C1 = params.get("C1", 0.0)
    C2 = params.get("C2", 0.0)
    C3 = params.get("C3", 0.0)
    
    time_minutes = C1 * temp_change + C2 * outdoor_temp + C3
    
    return max(0.0, time_minutes)  # Don't return negative time


def predict_time_to_target_cool(
    model: ThermalModel,
    temp_change: float,
    outdoor_temp: float,
) -> float | None:
    """Predict time to reach target temperature (cooling) in minutes."""
    func = model.time_to_target_functions.get("cooling")
    if not func:
        return None
    
    params = func.get("parameters", {})
    C1 = params.get("C1", 0.0)
    C2 = params.get("C2", 0.0)
    C3 = params.get("C3", 0.0)
    
    time_minutes = C1 * temp_change + C2 * outdoor_temp + C3
    
    return max(0.0, time_minutes)


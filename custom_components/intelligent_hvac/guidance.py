"""Pure cooling guidance logic for Intelligent HVAC."""
from __future__ import annotations

from typing import Any


def build_guidance_message(strategy: str) -> str:
    """Return a short human-readable guidance message."""
    messages = {
        "open_windows": "It's cool out. Open the windows.",
        "coast_no_cooling": "It's cool out. Stop active cooling and let the house coast.",
        "anticipate_warming": "It's warming later. Hold cooling at comfort to stay ahead.",
        "night_setback": "Cooling demand is low. Relax to the nighttime setback.",
        "comfort_hold": "Hold the normal cooling target.",
        "recover_comfort": "The house is warm. Bring cooling back to comfort.",
        "start_precool": "Recovery time is shrinking. Start moving back to comfort.",
        "precool_now": "Cooling may fall behind. Pre-cool more aggressively now.",
    }
    return messages.get(strategy, "Follow the current Intelligent HVAC cooling recommendation.")


def determine_cooling_strategy(
    *,
    indoor_temp: float,
    outdoor_temp: float,
    comfort_temp: float,
    night_setback_temp: float,
    max_precool_offset: float,
    precool_lead_minutes: float,
    in_night_window: bool,
    current_target: float | None,
    holding_margin: float | None,
    passive_decay_rate: float | None,
    forecast_high_temp: float | None,
    indoor_trend_rate: float | None,
    outdoor_trend_rate: float | None,
) -> dict[str, Any]:
    """Determine the cooling strategy and recommended target."""
    strategy = "comfort_hold"
    recommended_temp = comfort_temp
    precool_temp = max(comfort_temp - max_precool_offset, 60.0)

    passive_cooling_rate = max(0.0, passive_decay_rate or 0.0)
    passive_gain_rate = abs(min(0.0, passive_decay_rate or 0.0))
    temp_gap_to_outdoor = indoor_temp - outdoor_temp
    outdoor_cooler_than_comfort = outdoor_temp <= comfort_temp
    outdoor_near_comfort = outdoor_temp >= comfort_temp - 1.5
    outdoor_warming_now = outdoor_trend_rate is not None and outdoor_trend_rate >= 0.35
    passive_cooling_weak = passive_cooling_rate <= 0.1 or temp_gap_to_outdoor <= 2.0
    forecast_heat_excess = (
        max(0.0, forecast_high_temp - comfort_temp)
        if forecast_high_temp is not None
        else 0.0
    )
    house_is_materially_hot = indoor_temp >= comfort_temp + 1.5
    house_is_super_hot = indoor_temp >= comfort_temp + 4.0
    indoor_temp_falling = indoor_trend_rate is not None and indoor_trend_rate <= -0.15
    indoor_temp_rising = indoor_trend_rate is not None and indoor_trend_rate >= 0.15
    indoor_temp_stable = (
        indoor_trend_rate is not None
        and not indoor_temp_falling
        and not indoor_temp_rising
    )

    natural_cooling_favorable = (
        temp_gap_to_outdoor >= 1.5
        and outdoor_temp <= comfort_temp + 3.0
        and (passive_cooling_rate >= 0.05 or temp_gap_to_outdoor >= 2.5)
    )
    window_favorable = (
        natural_cooling_favorable
        and outdoor_temp <= comfort_temp
        and forecast_heat_excess < 5.0
        and not house_is_materially_hot
    )

    forecast_risk_score = 0.0
    anticipate_warming = False
    if forecast_high_temp is not None:
        outdoor_trend_position = max(0.0, outdoor_temp - (comfort_temp - 8.0))
        margin_pressure = max(0.0, 0.75 - (holding_margin or 0.0))
        forecast_risk_score = (
            forecast_heat_excess
            + outdoor_trend_position
            + passive_gain_rate * 2.0
            + margin_pressure * 2.0
            - passive_cooling_rate * 4.0
        )
        anticipate_warming = (
            forecast_heat_excess >= 6.0
            and indoor_temp >= comfort_temp - 1.0
            and forecast_risk_score >= 6.0
            and not indoor_temp_falling
            and (
                outdoor_temp >= comfort_temp + 0.5
                or (outdoor_near_comfort and outdoor_warming_now)
                or (
                    outdoor_near_comfort
                    and passive_cooling_weak
                    and forecast_risk_score >= 8.0
                )
            )
        )

    if house_is_materially_hot:
        strategy = "recover_comfort"
        recommended_temp = comfort_temp
    elif in_night_window and outdoor_temp > comfort_temp:
        strategy = "comfort_hold"
        recommended_temp = comfort_temp
    elif in_night_window and natural_cooling_favorable:
        if window_favorable:
            strategy = "open_windows"
            recommended_temp = max(night_setback_temp, indoor_temp + 1.5)
        else:
            strategy = "coast_no_cooling"
            recommended_temp = max(night_setback_temp, indoor_temp + 1.0)
    elif natural_cooling_favorable and not anticipate_warming:
        if window_favorable:
            strategy = "open_windows"
            recommended_temp = max(night_setback_temp, indoor_temp + 1.5)
        else:
            strategy = "coast_no_cooling"
            recommended_temp = max(night_setback_temp, indoor_temp + 1.0)
    elif (
        not in_night_window
        and outdoor_cooler_than_comfort
        and current_target is not None
        and current_target > comfort_temp
        and not house_is_materially_hot
        and not anticipate_warming
    ):
        strategy = "coast_no_cooling"
        recommended_temp = max(current_target, night_setback_temp, indoor_temp + 1.0)
    elif anticipate_warming and not house_is_super_hot and not in_night_window:
        strategy = "anticipate_warming"
        recommended_temp = comfort_temp
    elif in_night_window:
        strategy = "night_setback"
        recommended_temp = night_setback_temp

        if (
            holding_margin is not None
            and holding_margin <= 0
            and forecast_heat_excess >= 6.0
            and not indoor_temp_falling
        ):
            strategy = "precool_now"
            recommended_temp = precool_temp
        elif (
            holding_margin is not None
            and holding_margin > 0
            and current_target is not None
            and current_target > comfort_temp
            and not indoor_temp_falling
        ):
            temp_buffer = max(0.0, current_target - indoor_temp)
            if temp_buffer <= 0:
                lead_time = 0.0
            else:
                lead_time = (temp_buffer / holding_margin) * 60.0

            if lead_time <= precool_lead_minutes:
                strategy = "start_precool"
                recommended_temp = comfort_temp
    elif indoor_temp > comfort_temp + 0.5:
        strategy = "recover_comfort"
        recommended_temp = comfort_temp

    recommended_temp = round(min(max(recommended_temp, 60.0), 85.0), 1)

    return {
        "recommended_temp": recommended_temp,
        "strategy": strategy,
        "guidance_message": build_guidance_message(strategy),
        "passive_decay_rate": round(passive_decay_rate, 3) if passive_decay_rate is not None else None,
        "passive_cooling_rate": round(passive_cooling_rate, 3),
        "passive_gain_rate": round(passive_gain_rate, 3),
        "forecast_high_temp": forecast_high_temp,
        "forecast_heat_excess": round(forecast_heat_excess, 3),
        "forecast_risk_score": round(forecast_risk_score, 3),
        "natural_cooling_favorable": natural_cooling_favorable,
        "window_favorable": window_favorable,
        "anticipate_warming_active": anticipate_warming,
        "indoor_trend_rate": round(indoor_trend_rate, 3) if indoor_trend_rate is not None else None,
        "outdoor_trend_rate": round(outdoor_trend_rate, 3) if outdoor_trend_rate is not None else None,
        "indoor_temp_falling": indoor_temp_falling,
        "indoor_temp_rising": indoor_temp_rising,
        "indoor_temp_stable": indoor_temp_stable,
        "house_is_materially_hot": house_is_materially_hot,
        "house_is_super_hot": house_is_super_hot,
        "window_guidance_advisory_only": True,
    }

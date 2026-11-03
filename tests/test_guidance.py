"""Focused tests for spring cooling guidance logic."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "intelligent_hvac" / "guidance.py"
SPEC = importlib.util.spec_from_file_location("intelligent_hvac_guidance", MODULE_PATH)
guidance = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(guidance)


class DetermineCoolingStrategyTests(unittest.TestCase):
    def test_cool_morning_mild_day_prefers_open_windows_or_coast(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=65.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=1.2,
            passive_decay_rate=0.4,
            forecast_high_temp=74.0,
            indoor_trend_rate=-0.5,
            outdoor_trend_rate=0.0,
        )
        self.assertIn(result["strategy"], {"open_windows", "coast_no_cooling"})

    def test_cool_morning_hot_day_anticipates_warming(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=71.5,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=0.6,
            passive_decay_rate=0.2,
            forecast_high_temp=80.0,
            indoor_trend_rate=0.1,
            outdoor_trend_rate=0.7,
        )
        self.assertEqual(result["strategy"], "anticipate_warming")

    def test_cool_morning_but_house_hot_recovers_comfort(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=75.0,
            outdoor_temp=65.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=1.0,
            passive_decay_rate=0.5,
            forecast_high_temp=80.0,
            indoor_trend_rate=0.3,
            outdoor_trend_rate=0.4,
        )
        self.assertEqual(result["strategy"], "recover_comfort")

    def test_forecast_unavailable_falls_back_to_current_conditions(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=65.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=1.2,
            passive_decay_rate=0.4,
            forecast_high_temp=None,
            indoor_trend_rate=-0.4,
            outdoor_trend_rate=-0.2,
        )
        self.assertIn(result["strategy"], {"open_windows", "coast_no_cooling"})

    def test_cool_night_does_not_precool_to_70(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=63.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=True,
            current_target=74.0,
            holding_margin=-0.5,
            passive_decay_rate=0.4,
            forecast_high_temp=80.0,
            indoor_trend_rate=-0.6,
            outdoor_trend_rate=-0.4,
        )
        self.assertIn(result["strategy"], {"open_windows", "coast_no_cooling"})
        self.assertGreaterEqual(result["recommended_temp"], 73.0)

    def test_falling_indoor_temp_blocks_anticipate_warming(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=66.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=0.2,
            passive_decay_rate=0.3,
            forecast_high_temp=82.0,
            indoor_trend_rate=-0.5,
            outdoor_trend_rate=0.2,
        )
        self.assertIn(result["strategy"], {"open_windows", "coast_no_cooling"})

    def test_warm_10pm_outdoor_air_blocks_night_setback(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=73.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=True,
            current_target=72.0,
            holding_margin=0.7,
            passive_decay_rate=-0.1,
            forecast_high_temp=82.0,
            indoor_trend_rate=0.0,
            outdoor_trend_rate=-0.1,
        )
        self.assertEqual(result["strategy"], "comfort_hold")
        self.assertEqual(result["recommended_temp"], 72.0)

    def test_cool_early_morning_keeps_setback_until_warming_starts(self) -> None:
        result = guidance.determine_cooling_strategy(
            indoor_temp=72.0,
            outdoor_temp=68.0,
            comfort_temp=72.0,
            night_setback_temp=74.0,
            max_precool_offset=2.0,
            precool_lead_minutes=60.0,
            in_night_window=False,
            current_target=74.0,
            holding_margin=0.4,
            passive_decay_rate=0.05,
            forecast_high_temp=84.0,
            indoor_trend_rate=0.0,
            outdoor_trend_rate=0.2,
        )
        self.assertEqual(result["strategy"], "coast_no_cooling")
        self.assertEqual(result["recommended_temp"], 74.0)


if __name__ == "__main__":
    unittest.main()

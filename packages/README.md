# Comfort package

`intelligent_hvac_comfort.yaml` is the glue that turns the integration's learned model into behaviour: *"I want 70°F at 7:00 AM — have it ready."* It is a standard Home Assistant [package](https://www.home-assistant.io/docs/configuration/packages/), so it is one file to drop in and one file to remove.

> The integration alone gives you the sensors and `apply_smart_cooling_setpoint`. **This package is what provides the helpers (wake-up time, target temperature, setbacks…) and the heating-season automations** — the dashboard depends on it.

## What's inside

| Kind | What |
|---|---|
| Helpers | Wake-up time, target temperature, fallback & max recovery lead, setback temperatures & cold thresholds, season-switch thresholds, calculated recovery values |
| Template sensors | `sensor.outdoor_temperature_most_recent` + cold-threshold binary sensors |
| Script | `script.intelligent_hvac_update_recovery` — the one place the recovery math lives |
| Automations | Season mode from forecast · Smart cooling · Nightly setback · Recovery updates · Morning heat-up |

## How the morning heat-up works

1. At 22:00 the house sets back (deeper when it's mild, gentler when it's very cold).
2. The integration's `Time to Target 5°F Heat` sensor says how long the house takes to gain 5°F *right now*.
3. The script scales that to the real setback→target gap, adds your buffer, and subtracts it from your wake-up time → **`Smart Recovery Start Time`**.
4. At that moment the thermostat is raised to your target. You wake up warm; the house wasn't heated all night.

The recovery start time is not recalculated between 05:00 and 10:00, so a late update can never schedule the start in the past.

## Install

1. Install and configure the Intelligent HVAC integration first.
2. In `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Copy `intelligent_hvac_comfort.yaml` into `<config>/packages/`.
4. Replace entity IDs if yours differ (find & replace):

   | Default | Replace with |
   |---|---|
   | `climate.thermostat` | your thermostat |
   | `weather.home` | a weather entity that supports daily forecasts |
   | `sensor.my_outdoor_temperature` | an outdoor sensor, if you have one (optional — falls back to the weather entity) |
   | `sensor.intelligent_hvac_time_to_target_5degf_heat` | the integration's *Time to Target 5°F Heat* sensor (check Developer Tools → States) |

5. Check configuration (Developer Tools → YAML) and restart Home Assistant.
6. Add the dashboard: see [../dashboards/README.md](../dashboards/README.md).

## First run

The helpers deliberately have no `initial:` value, so Home Assistant **restores your settings across restarts** instead of resetting them. On first start they sit at their minimum — set your **wake-up time, target temperature, setbacks and thresholds** (the dashboard has them all) before relying on the automations.

Suggested starting values for the safety limits: **Fallback Recovery Lead 90 min**, **Maximum Recovery Lead 180 min**. Watch a few mornings and adjust.

## Safety net: fallback heat-up

The learned model decides *when* heat-up starts, but within limits you set:

- The smart start is never **earlier** than *wake − Maximum Recovery Lead* (a model that's too pessimistic can't heat the house half the night).
- The smart start is never **later** than *wake − Fallback Recovery Lead*.
- **Smart Recovery: Fallback Heat Up** checks every 5 minutes inside that fallback window. If the thermostat is in `heat` and still below the target, it raises the setpoint and leaves a notification. That covers a missing or implausible model, a stale start time, or HA being restarted at the start minute.

If you see the fallback notification often, the model isn't doing its job. Please open an issue with your Thermal Model Confidence and Time to Target values.

## Notes

- Units are °F, matching the integration.
- If you already have thermostat automations that set the same setpoint, disable them — competing automations will fight.
- Heating automations only act when the thermostat is in `heat`; the cooling automation only in `cool` / `heat_cool`. The forecast automation switches between them each midnight.

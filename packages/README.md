# Comfort package

`intelligent_hvac_comfort.yaml` is the glue that turns the integration's learned model into behaviour: *"I want 70°F at 7:00 AM — have it ready."* It is a standard Home Assistant [package](https://www.home-assistant.io/docs/configuration/packages/), so it is one file to drop in and one file to remove.

> The integration alone gives you the sensors and `apply_smart_cooling_setpoint`. **This package is what provides the helpers (wake-up time, target temperature, setbacks…) and the heating-season automations** — the dashboard depends on it.

## What's inside

| Kind | What |
|---|---|
| Helpers | Wake-up time, target temperature, fallback & max recovery lead, setback minimum & longest heat-up, cold thresholds (informational), season-switch thresholds, calculated recovery values |
| Template sensors | `sensor.outdoor_temperature_most_recent`, cold-threshold binary sensors, `sensor.intelligent_hvac_night_setback` (tonight's setback), `sensor.intelligent_hvac_recovery_speed_factor` (learned) |
| Script | `script.intelligent_hvac_update_recovery` — the one place the recovery math lives |
| Automations | Season mode from forecast · Smart cooling · Nightly setback · Recovery updates · Morning heat-up · Fallback heat-up · Measure heat-up |

## How the morning heat-up works

1. At *Setback Starts* (e.g. 22:00) the house sets back to tonight's calculated setback (see below). Until then, the start time is planned for the coming night from the forecast low and tonight's setback, so it doesn't drift with the daytime temperature. After the setback it tracks the real house every 5 minutes and locks in 15 minutes before the start.
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
   | `weather.home` | a weather entity with daily forecasts (hourly too, ideally) |
   | `sensor.my_outdoor_temperature` | an outdoor sensor, if you have one (optional — falls back to the weather entity) |
   | `sensor.intelligent_hvac_time_to_target_5degf_heat` | the integration's *Time to Target 5°F Heat* sensor (check Developer Tools → States) |

5. Check configuration (Developer Tools → YAML) and restart Home Assistant.
6. Add the dashboard: see [../dashboards/README.md](../dashboards/README.md).

## First run

The helpers deliberately have no `initial:` value, so Home Assistant **restores your settings across restarts** instead of resetting them. On first start they sit at their minimum — set your **wake-up time, target temperature, setback start time, setback minimum, longest heat-up and thresholds** (the dashboard has them all) before relying on the automations.

Suggested starting values: **Fallback Recovery Lead 90 min**, **Maximum Recovery Lead 180 min**, **Longest Morning Heat-up 90 min**. Watch a few mornings and adjust.

## Safety net: fallback heat-up

The learned model decides *when* heat-up starts, but within limits you set:

- The smart start is never **earlier** than *wake − Maximum Recovery Lead* (a model that's too pessimistic can't heat the house half the night).
- If the model has no usable answer (sensor unavailable, or outside 5–300 min per 5°F), the start is *wake − Fallback Recovery Lead*. The fallback lead does **not** floor a working model, so a house that recovers quickly starts later.
- **Smart Recovery: Fallback Heat Up** checks every 5 minutes from 5 minutes after the smart start time until wake-up. If the thermostat is in `heat` and still below the target, it raises the setpoint and leaves a notification. That covers HA being down or restarting at the start minute.

If you see the fallback notification often, the model isn't doing its job. Please open an issue with your Thermal Model Confidence and Time to Target values.

## Night setback: as deep as the furnace can recover from

`sensor.intelligent_hvac_night_setback` works out tonight's setback every 15 minutes:

- **Night Setback Minimum** is the coolest you want the house overnight. On mild nights the house coasts toward it and often never gets there, so the furnace doesn't run until the morning heat-up.
- On cold nights the setback is raised to **the lowest temperature the furnace can recover from within *Longest Morning Heat-up*** (default 90 min) at the forecast overnight low. Same physics as the start time: `minutes = 60/k · ln((P − k(S − Tout)) / (P − k(Target − Tout)))`, solved for the setback S.
- It stays on the safe side: the time budget is reduced by the model's backtest error, and heating power is derated 0.5% per °F (up to 25%) when the low is colder than the model has seen.
- The forecast low is the coldest hourly temperature between *Setback Starts* and wake-up (or the daily low if the weather entity has no hourly forecast), or the current outdoor temperature if that is colder.

A gas furnace always saves energy with a deeper setback (a cooler house loses less heat), so the limit is comfort and a reliable recovery, not efficiency. With a heat pump, a long recovery can bring on electric backup heat. Keep *Longest Morning Heat-up* short or the minimum higher.

## Learning from every morning

*Smart Recovery: Measure Heat-up* times each heat-up of 3°F or more that starts within 4 hours of wake-up, and compares it with what the physics model predicted for that start temperature and outdoor temperature. `sensor.intelligent_hvac_recovery_speed_factor` keeps a running average of actual ÷ predicted, with each new morning counting 30%. Below 1 means the furnace beats the model. The start time and the setback both scale the model by it. Runs cut short (setpoint lowered) or longer than 4 hours are discarded. To reset it, fire the event `ihvac_recovery_measured` with data `factor: 1`.

## Nighttime setback only ever lowers

The setback only **lowers** the setpoint. If you've already turned the thermostat down further by hand (a warm day, and you don't want the heat coming on), it leaves your setting alone instead of raising it and starting the heat.

> **Thermostat schedules fight back.** Most thermostats (ecobee included, over HomeKit too) treat a setpoint change as a hold that ends at the next schedule transition. If the setpoint jumps around with no automation behind it, set the thermostat's hold action to *until I change it*, or flatten its schedule, so Home Assistant is the only scheduler.

## Notes

- Units are °F, matching the integration.
- If you already have thermostat automations that set the same setpoint, disable them — competing automations will fight.
- Heating automations only act when the thermostat is in `heat`; the cooling automation only in `cool` / `heat_cool`. The forecast automation switches between them each midnight.

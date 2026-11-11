# Changelog

Versions follow Home Assistant's format: `1.2.0b1` is a beta (published as a GitHub pre-release), `1.2.0` is stable.


## Unreleased
- Smart start is dynamic: it predicts the indoor temperature at heat-up time
  (coasting toward outdoor, floored at the thermostat's real setpoint, using the
  larger of the model's and tonight's observed heat loss), re-plans every 5 min,
  and commits 15 min before the start. If the house cools faster than predicted
  and the start is already due, it heats immediately. Replaces the fixed
  05:00-10:00 lockout and the setback-helper-based delta.

## Unreleased

### Fixed
- **The heat-up start time was pinned to *wake − Fallback Recovery Lead* every day.** The smart start was clamped so it could never be later than the fallback lead, so any house that recovers faster than that (here: ~44 min per 5°F against a 90 min fallback) started at the same fixed time each morning and the model was ignored. The model's answer now stands, capped only by *Maximum Recovery Lead*. The fallback lead is used only when the model has no usable answer, and the fallback heat-up now fires only if the smart start time has passed by 5 minutes without the setpoint going up.

## 1.2.0b2

**Beta.** Fixes from the first overnight run on the real house, and a rebuilt dashboard.

### Fixed
- **Fallback heat-up fired every morning.** The smart start is clamped to *wake − Fallback Recovery Lead*, which is exactly when the fallback window opens, so both automations fired in the same second and you got a "fallback heat-up" notification even though smart recovery had worked. The fallback now waits a minute and re-checks the setpoint first.
- **The "at 32°F" / "at 75°F" rate sensors used today's weather.** *Heating Rate at 32°F*, *Cooling Rate at 75°F*, *Passive Decay Rate at 32°F* and *Passive Gain Rate at 75°F* all evaluated the model at the current outdoor temperature, so they changed with the weather and didn't mean what their names said. They now evaluate at the named temperature, and the current outdoor temperature is kept as the `current_outdoor_temp_f` attribute. *Passive Gain Rate* also reported `abs(rate)`, which showed heat loss as "gain". It is now signed (positive = warming) and rounded.
- **Nighttime setback could turn the heat on.** If you'd turned the thermostat below the setback by hand, the 22:00 setback raised it. Setback now only ever lowers the setpoint.

### Added
- The dashboard is rebuilt: tonight's plan as a timeline, *Pause / Resume* for everything that touches the thermostat, every package automation with its last run, all settings in one column, and a band showing what the model has learned and when, plus a 7-day heat-up / cool-down chart. The test buttons now ask before changing the thermostat.
- *Thermal Model Status* gains `passive_gain_segments`, `newest_segment_end` and `model_last_updated`, so "did it learn last night?" has an answer.

## 1.2.0b1

**Beta.** The heating model has been rebuilt, and the fixes were checked against 21 days of real thermostat history. It has run on one real house (Ecobee, gas heat) since 2026-11-03. It hasn't been through a cold snap yet, so please report your mornings.

Much of the original code was generated with OpenAI Codex. This release fixes the oversights that made its heating model wrong. The details are in [documentation/ROADMAP.md](documentation/ROADMAP.md).

### Fixed
- **The heating model never saw the furnace cycle.** Thermostat history was ordered by `last_changed`, but `hvac_action` (heating/idle) is an attribute and changing it doesn't move that timestamp. On a real install, 1,457 heating/idle transitions in 21 days collapsed into 36 records. History is now ordered by `last_updated` (#6).
- Segments were stored about 32 times over, because the resample grid moved on every update. The grid is now anchored to the clock, only complete runs are stored, and segments are de-duplicated by start time and type (#6).
- A short-cycle merge relabelled heating plus the idle time after it as "heating", so heating durations included coasting time. That merge is removed (#6).
- The thermostat's reading was counted twice in the indoor average when it was also listed as an indoor sensor (#6).
- Learning ran on Home Assistant's event loop. It now runs in the executor (#6).
- A missing indoor temperature at startup is logged as a warning instead of an error (#6).

### Changed
- **Physics heating model** (#7). A first-order house model: idle periods give the heat-loss rate, sustained heating runs give the furnace's heating power, and recovery time is solved from those two numbers. Colder weather now means slower recovery, by construction. The old regression predicted the opposite.
  - On the real house: 65→70°F takes about 44 min at 30°F outside (the old model said 144 min). The mean error was 4.3–4.7 min, scored on 8 real heat-ups, each predicted without that heat-up in the training data.
  - **Confidence** now comes from that backtest error and from how much data there is.
  - If no physically plausible fit exists, the integration publishes **no** heating prediction. It no longer publishes a wrong one.
  - New attributes on *Time to Target 5°F Heat*: `heat_loss_per_hour`, `heating_power_f_per_hour`, `heating_power_source`, `backtest_error_minutes`, `backtest_runs`, `outdoor_range_seen_f`, `extrapolating`, `notes`.
- **Stored data format v2.** On first start after upgrading, the old learned data is **archived, not deleted**: renamed to `*.legacy-v1.json` in `<config>/intelligent_hvac/`. The model then relearns from your recorder history, which takes a few minutes after the restart.
- Segments are kept for 400 days (`SEGMENT_RETENTION_DAYS`), so the model can follow changes to the house.

### Added
- **Comfort package: fallback heat-up** (#5). If the thermostat is in heat mode and still below target at *wake-up − fallback lead*, the package raises the setpoint itself and leaves a notification. It checks every 5 minutes and when HA starts. The smart start time is also clamped between the fallback lead and a new maximum lead. New helpers: `input_number.fallback_recovery_lead` and `input_number.max_recovery_lead` (suggested values: 90 and 180 min).
- [documentation/ROADMAP.md](documentation/ROADMAP.md).

### Upgrading
1. Update the integration, copy the new `packages/intelligent_hvac_comfort.yaml`, and restart.
2. Set the two new lead helpers on the dashboard.
3. If you previously had these automations in your own YAML, remove the old copies. Otherwise the package's versions get `_2` entity IDs.

### Known limitations
- **Cooling** still uses the previous regression model. It's next on the roadmap.
- Predictions outside the outdoor temperatures seen so far are extrapolated, and the sensor flags them `extrapolating: true`.
- °F only.

## 1.1.0

First public release: smart cooling guidance and setpoint service, pre-cool sensors, comfort package, dashboard, HACS metadata.

# Architecture

Intelligent HVAC learns a thermal model of your home from Home Assistant history and exposes it as sensors, so that predictive HVAC automation becomes simple and safe.

The integration is built in layers. Each layer has one job and a validated output.

```
 Layer 0   Configuration         climate entity, indoor sensors, outdoor source
 Layer 1   Normalization         history -> uniform timesteps -> one composite indoor temp
           Segmentation          timesteps -> labelled thermal segments
 Layer 2   Learning              segments -> physics heating model (+ backtest)
 Layer 3   Exposure              model -> HA sensors + persisted JSON
 Layer 4   Smart cooling         model + forecast -> recommended cooling setpoint
```

## Layer 0 — Configuration

| Input | Notes |
|---|---|
| Climate entity | Primary thermostat. Needs `hvac_action` and `current_temperature` (see [COMPATIBILITY.md](COMPATIBILITY.md)). |
| Indoor sensors | One or more, optionally weighted. Combined into a single composite temperature. |
| Outdoor source | A sensor, climate, or weather entity. |
| Training days | How much history to pull (default 30). Fetches as much as is available. |
| Sampling interval | Canonical timestep (default 5 min). HA history is irregular; all learning assumes uniform deltas. |
| Comfort / setback / pre-cool / night window | Drive the smart-cooling logic. |

## Layer 1 — Normalization & segmentation

History is ordered by `last_updated` (a climate entity's `hvac_action` is an *attribute*, so `last_changed` doesn't move when the furnace cycles) and resampled onto a clock-anchored grid (multiples of the interval), so overlapping fetches produce identical timestamps. The indoor sensors are merged into one `Learned Indoor Temperature`.

The segmentation engine cuts the timeline into **complete runs of one `hvac_action`**:

| Type | Meaning |
|---|---|
| `Active_Heating` | HVAC heating (kept down to one sample: furnace cycles are often < 10 min, and the thermostat's 1°F resolution can show +0°F) |
| `Active_Cooling` | HVAC cooling |
| `Passive_Decay` | idle, house losing heat (≥ 10 min) |
| `Passive_Gain` | idle, house gaining heat (≥ 10 min) |

The run truncated by the start of the fetch window and the run still open at its end are never stored. Segments are keyed by `(start, type)`, kept for 400 days, and runs are never merged. The store has a schema version: incompatible older stores are archived (`*.legacy-vN.json`), never deleted.

## Layer 2 — Learning

### Heating: first-order physics

```
dTi/dt = P·u − k·(Ti − To) + g        [°F/h]
```

- `k` (heat loss, 1/h) and `g` (free gains) come from idle runs ≥ 60 min: weighted least squares of rate on `Ti − To`.
- `P` (heating power) comes from sustained heating runs (≥ 20 min, ≥ 1°F rise): `P = rate + k·(Ti − To) − g`, median. With fewer than 3 such runs it falls back to **duty cycle** over 4-hour windows, which tends to under-estimate `P` (short cycles deliver heat less efficiently), so it errs toward starting early.
- Recovery from T1 to T2 has a closed form: `Teq = To + (P + g)/k`, `t = ln((Teq − T1)/(Teq − T2)) / k`. Colder outdoor air is slower by construction; a target above `Teq` is reported as unreachable.
- Guardrails: `k` must be in 0.003–0.5/h and `P` in 1–40°F/h, or no heating prediction is published (the comfort package's fallback and clamp then decide).

Passive decay/gain uses the same `k` and `g`. Cooling still uses the older regression (next on the roadmap).

### Honest confidence

Every fit is **backtested**: each sustained heating run is predicted by a model fitted without it (leave-one-out), and the mean absolute error in minutes is published (`backtest_error_minutes` on *Time to Target 5°F Heat*).

`confidence = 0.65 × accuracy + 0.35 × coverage`, where accuracy is `1 − MAE/30 min` (once ≥ 3 runs are backtested; 0.3 before that) and coverage reflects the number of idle runs and heating evidence. Without a physics fit, confidence is capped at 30%.

## Layer 3 — Exposure

Learned values are exposed as standard HA sensors (see the README for the full list) and persisted as JSON under the HA config directory so restarts don't discard learning. Incremental updates fetch only new history.

## Layer 4 — Smart cooling

Pure decision logic (`guidance.py`, `smart_control.py`) turns the model, the current conditions and the forecast into a recommended cooling setpoint and a human-readable strategy. See [SMART_COOLING.md](SMART_COOLING.md).

## Design principles

- **Learn first, control second.** The sensors are useful on their own; control is an opt-in service call.
- **The automation is the messenger, the integration is the decision maker.** No HVAC intelligence lives in YAML.
- **Be honest about uncertainty.** Confidence is a first-class output.
- **Keep the model simple.** Linear, explainable, debuggable.

### Out of scope (for now)

Multi-zone modeling, solar gain, humidity corrections, PID control.

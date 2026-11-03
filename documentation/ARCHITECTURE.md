# Architecture

Intelligent HVAC learns a thermal model of your home from Home Assistant history and exposes it as sensors, so that predictive HVAC automation becomes simple and safe.

The integration is built in layers. Each layer has one job and a validated output.

```
 Layer 0   Configuration         climate entity, indoor sensors, outdoor source
 Layer 1   Normalization         history -> uniform timesteps -> one composite indoor temp
           Segmentation          timesteps -> labelled thermal segments
 Layer 2   Learning              segments -> linear-regression thermal model
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

History is resampled to a uniform interval, and the indoor sensors are merged into one `Learned Indoor Temperature` sensor (the authoritative input for everything downstream).

The segmentation engine walks `hvac_action` and indoor temperature to produce **thermal segments**:

| Type | Meaning |
|---|---|
| `Active_Heating` | HVAC heating, indoor temperature rising |
| `Active_Cooling` | HVAC cooling, indoor temperature falling |
| `Passive_Decay` | HVAC off, house losing heat |
| `Passive_Gain` | HVAC off, house gaining heat |

Each segment records start/end time, start/end indoor temperature, average outdoor temperature, total change, and rate. The segmenter is deliberately opinionated: very short segments (< 10 min) and tiny changes (< 0.5°F) are discarded so they don't poison learning.

## Layer 2 — Learning

Linear regression only (numpy; no scipy). The model learns:

- **Time-to-target** for active heating and cooling, as a function of the temperature change needed and the outdoor temperature (cooling uses the outdoor-minus-indoor delta).
- **Passive decay / gain rate**, as a function of the indoor–outdoor difference.

### Honest confidence

There is no hard "needs two weeks" gate. The model starts producing output immediately and reports a **composite confidence score** that rises as evidence accumulates:

| Component | Weight |
|---|---|
| Goodness of fit (R²) | 50% |
| Sample count | 30% |
| Temperature range coverage | 20% |

Treat low-confidence output as a preview, not a promise.

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

Multi-zone modeling, solar gain, humidity corrections, PID control, non-linear regression.

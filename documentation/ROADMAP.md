# Roadmap: making the heating model trustworthy

_Status as of 2026-11-03. Each item ships as its own PR, with tests, after passing the sandbox E2E. Merge order: #5 → #6 → #7._

## Why this exists

An audit of a real install (8 months of data, one Ecobee, gas heat) found that the learned heating model was **wrong in a fail-safe way**:

| Finding | Effect |
|---|---|
| Heating regression has the wrong sign (predicts *faster* recovery when colder), R² 0.16 | Recovery started ~2–3 h early (03:42 for a 07:00 wake-up). The house was warm, but it was heated for half the night. In a severe cold snap the error shrinks, then could flip to late. |
| The same segments were stored ~32× (the resample grid was anchored to "now − 24 h" on every 15-min update, so keys never matched) | 176k stored segments for ~500 real heating cycles. Training data is dominated by repeated and truncated copies. |
| The short-cycle merge relabels heating + idle as "heating" with the combined duration | Heating "durations" include coasting time; some "heating" segments show the temperature *falling*. |
| Segments are never pruned; `training_days` only limits the first fetch | The model never forgets: a new furnace or new windows would be averaged with the old house forever. |
| Confidence gives near-full credit for having samples, almost regardless of fit | It showed 58% for a model that explains 16% of the variance. |
| Recovery is computed from *current* indoor/outdoor temperatures, and the package scales a 5°F estimate linearly | The calculation doesn't describe the actual morning (setback → target, at the overnight low). |

Recorder history is usually short (21 days on that install, and climate entities have no long-term statistics), so the integration's own segment store is often the **only** record of past seasons. Fixes must migrate and dedupe it, never discard it.

## P0: safe and correct this winter

- [x] **Fallback heat-up (package)** — #5. A fixed-lead safety net: if the house hasn't been told to heat up by *wake-up − fallback lead*, raise the setpoint anyway. Guards against the model being unavailable, implausible, or too late. The smart start time is also clamped to a plausible window.
- [x] **History + segment store fixes** — #6. Root cause found while fixing: history was ordered by `last_changed`, so every `hvac_action` change within a heating season collapsed into one record (1,457 real transitions → 36 in 21 days). Now: `last_updated` ordering, clock-anchored grid, complete runs only, no merge, 400-day retention. Old stores are archived and relearned (deduping them wasn't worth it: they were built on the collapsed history).
- [x] **Physics heating model** — #7. First-order (lumped RC) house model: idle periods give the heat-loss coefficient, sustained heating runs give the heating power, and recovery time follows analytically, so colder = slower by construction. Guardrails: physically impossible fits are rejected (no heating prediction; the package fallback/clamp decide), and predictions outside the outdoor range seen so far are flagged `extrapolating`.
- [x] **Backtest + honest confidence** — #7. On real history: 4.7 min mean error over 8 heat-ups. Score predictions against real past recoveries (held out). Confidence becomes "typical error in minutes", not a weighted checklist.
- [x] **Smart start no longer pinned to the fallback lead (package).** The #5 clamp meant the start could never be later than *wake − fallback lead*, so a house faster than that (~44 min per 5°F against a 90 min fallback) started at 05:30 every day and the model was ignored. The model's answer now stands, capped only by the max lead. The fallback lead is used only without a usable model, and the fallback heat-up fires only once the smart start has passed by 5 min.
- [ ] **Recover from the indoor temperature you'll actually have, not the setback.** The package assumes the house has fallen all the way to the setback (65°F) by heat-up time, so delta = target − setback. On mild nights the real house is still 69–71°F at 05:30 (2026-11-02/03 history), so the real recovery is 0–1°F and the start is still too early. Predict the indoor temperature at the start time from the learned passive-decay rate and the overnight outdoor low, then size the recovery from that. Mild nights start later; cold nights stay early. Fits naturally into the next item.
- [ ] **Recovery start computed by the integration.** A `Recovery Start Time` sensor for *setback → target at the forecast overnight low*. The package script becomes a thin pass-through.

## P1: closed loop

- [ ] Morning outcome tracking: when the target was actually reached vs the wake-up time (sensor + dashboard card), auto-tuning the buffer.
- [x] Learning runs in the executor; linear-time segmentation — #6.
- [ ] Indoor sensor weights honoured; option to exclude the thermostat's own reading.
- [ ] Manual-override detection (heating and cooling); documented conflicts with Ecobee Smart Recovery / Follow Me.
- [ ] **Passive gain looks near zero.** *Passive Gain Rate at 75°F* reads +0.01 °F/h on the real house (2026-11-04) despite 173 coasting-warming segments. A house left alone at 75°F outside should warm noticeably. Check the passive-gain fit (sign, outdoor range of the segments, whether solar/internal gains are being averaged away) before the cooling model is rebuilt on it.

## P2: broaden

- [ ] °C support
- [ ] Heating strategy guidance to match cooling
- [ ] Native helper entities (comfort package optional)
- [ ] Multi-stage / auxiliary heat awareness
- [ ] HACS default repository, brands icon

## Dashboard

- [ ] *Rates (HVAC on / off)* card: three of the four rows have no icon, so their labels don't line up with the fourth (*Coasting, 75°F out*). Give every row an icon.

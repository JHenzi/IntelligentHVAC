<h1 align="center">🏠 Intelligent HVAC</h1>

<p align="center">
  <b>Your thermostat follows a schedule. Your house follows physics.<br>
  Intelligent HVAC learns the physics.</b>
</p>

<p align="center">
  <img alt="Home Assistant" src="https://img.shields.io/badge/Home%20Assistant-custom%20integration-41BDF5?logo=home-assistant&logoColor=white">
  <img alt="Release" src="https://img.shields.io/github/v/release/JHenzi/IntelligentHVAC?include_prereleases&sort=semver">
  <img alt="IoT class" src="https://img.shields.io/badge/iot__class-local__polling-green">
  <img alt="Cloud" src="https://img.shields.io/badge/cloud-none-lightgrey">
  <a href="https://github.com/JHenzi/IntelligentHVAC/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/JHenzi/IntelligentHVAC/actions/workflows/tests.yml/badge.svg"></a>
  <a href="https://github.com/JHenzi/IntelligentHVAC/actions/workflows/validate.yml"><img alt="Validate" src="https://github.com/JHenzi/IntelligentHVAC/actions/workflows/validate.yml/badge.svg"></a>
  <a href="https://hacs.xyz"><img alt="HACS" src="https://img.shields.io/badge/HACS-custom-orange"></a>
</p>

<p align="center">
  <img src="documentation/images/dashboard.png" alt="Intelligent HVAC dashboard" width="900">
</p>

## 🌅 Imagine waking up and it's already warm

You said you wanted **70°F at 7:00 AM**. That's all you did.

Overnight the house relaxed to a cool setback — you slept comfortably and didn't pay to heat an empty-feeling house all night. Then, at exactly the right minute (not "5 AM, just in case"), the heat came on, because Intelligent HVAC *knows how long **your** house takes to climb from 65°F to 70°F at today's outdoor temperature*. You wake up to 70°F.

In summer it flips: on a cool night it tells you to **open the windows** and lets the AC rest, then holds comfort early on mornings that are about to turn hot. Same brain, different season — and a daily forecast check switches the mode for you.

## 🤔 Why not just use the thermostat's "learning" mode?

"Smart" and "learning" thermostats mostly learn *your schedule*, and what they learn lives in someone else's cloud. Logins get blocked, APIs go away, and what they know about your house is never exposed to you.

Intelligent HVAC learns something different and more useful — **your house's physics** — and keeps all of it local, visible, and usable in your own automations.

---

Intelligent HVAC is a Home Assistant custom integration that learns a thermal model of your specific home from your own recorder history — how fast it heats, how fast it cools, how quickly it leaks heat outdoors — and turns that model into sensors and smart setpoints.

No cloud. No guesswork. No hard-coded "start heating at 6 AM". Just *"it takes this house 38 minutes to gain 5°F at the current outdoor temperature, so start at 6:22."*

## ✨ What it does

- 🧠 **Learns from the history you already have.** Pulls as much recorder history as exists and starts learning immediately — no two-week waiting period.
- 📐 **Models four kinds of thermal behaviour:** active heating, active cooling, passive heat loss, and passive heat gain.
- 🎯 **Answers the questions automations care about:** *How long to recover 5°F? Can the AC keep up today? When should I start pre-cooling?*
- ⏰ **A smart start that watches the night unfold.** The heat-up time is planned from where the house will *actually* be at dawn: your real setpoint, the indoor and outdoor temperatures, and how fast the house is losing heat tonight. It re-plans every 5 minutes, then locks in shortly before the start so it never drifts late.
- 🌬️ **Knows when to do nothing.** Cool night and the house is already shedding heat? It tells you to open the windows or coast instead of burning AC.
- 📈 **Honest about uncertainty.** Every fit is backtested against your own past heat-ups; confidence is driven by the measured error in minutes.
- 🔌 **Works with Ecobee and HomeKit thermostats** — anything exposing `hvac_action` and `current_temperature`. See [COMPATIBILITY.md](documentation/COMPATIBILITY.md).
- 🏡 **Stays local.** Everything runs inside Home Assistant.

## 🚀 Installation

Two parts: the **integration** (learns the model, provides sensors) and the **comfort package** (the helpers and automations that act on it — wake-up time, setbacks, morning heat-up). You want both.

### 1. The integration

**HACS (custom repository):** HACS → ⋮ → *Custom repositories* → `https://github.com/JHenzi/IntelligentHVAC`, category *Integration* → install → restart.

**Manual:** copy `custom_components/intelligent_hvac/` from this repo into `config/custom_components/` → restart.

Then **Settings → Devices & Services → Add Integration → Intelligent HVAC**:

| Option | Description | Default |
|---|---|---|
| Climate entity | Your thermostat | — |
| Indoor temperature sensors | One or more, optionally weighted | — |
| Outdoor temperature source | Sensor, climate, or weather entity | — |
| Training days | History to learn from | 30 |
| Sampling interval | Canonical timestep | 5 min |
| Comfort cooling temp | Target when cooling | 72°F |
| Night setback temp | Relaxed overnight target | 74°F |
| Max pre-cool offset | How far below comfort pre-cooling may go | 2°F |
| Pre-cool lead time | Minutes of lead | 60 |
| Night window | Start / end | 22:00 / 07:00 |

### 2. The comfort package

Adds the helpers and automations. Five minutes: [packages/README.md](packages/README.md).

### 3. The dashboard (optional, recommended)

A ready-made dashboard ships in [`dashboards/intelligent_hvac_dashboard.yaml`](dashboards/intelligent_hvac_dashboard.yaml). It uses core cards only, so no HACS frontend cards are needed.

1. **Settings → Dashboards → Add dashboard** → *New dashboard from scratch* → open it.
2. **⋮ → Edit dashboard → ⋮ → Raw configuration editor**, replace everything with the file's contents, and save.
3. Using a different thermostat than `climate.thermostat`? Find & replace it.

What you get:

- **Tonight**: the plan as a timeline (setback, heat-up, wake-up) and the gauges behind it.
- **Controls**: *Pause / Resume* for everything that changes your thermostat, and each automation with a one-line note on what it does and when it last ran.
- **Settings**: wake-up time, target, heat-up limits, setback temperatures, cold thresholds and season-switch thresholds.
- **What it has learned**: when the model last refit, how much it has seen (heat-ups, AC runs, coasting), heating power, heat loss, backtest error in minutes. A 7-day heat-up / cool-down chart sits under Controls. Mild nights with no heating still teach heat loss.
- **Smart cooling**: summer advice and the cooling sensors.

More in [dashboards/README.md](dashboards/README.md).

> **Heads up:** the model needs some history to be trustworthy. Check *Thermal Model Confidence* — below ~50% treat the output as a preview.

## 📊 Sensors

| Sensor | Tells you |
|---|---|
| Thermal Model Status | Whether the model is trained |
| Thermal Model Confidence | How much to trust the model (0–100%) |
| Learned Indoor Temperature | Composite of your indoor sensors |
| Heating Rate at 32°F | How fast the home heats in the cold |
| Cooling Rate at 75°F | How fast the home cools |
| Passive Decay Rate at 32°F | Heat loss with the HVAC off |
| Passive Gain Rate at 75°F | Heat gain with the HVAC off |
| Time to Target 5°F Heat / Cool | Minutes to move 5°F at current conditions |
| Time to Drop 5°F Passive | How long the house takes to drift down on its own |
| Cooling Holding Margin | Net cooling headroom in °F/hr — can the AC outrun the heat load? |
| Pre-cool Lead Time | Minutes needed to get back to comfort |
| Recommended Cooling Setpoint | What Intelligent HVAC wants the thermostat at |
| Cooling Strategy | *Why* — see below |

## 🧊 Smart cooling (summer)

`sensor.intelligent_hvac_cooling_strategy` explains each decision in plain terms:

| Strategy | Meaning |
|---|---|
| `open_windows` | Outdoor air is favourable; open up and back off the AC |
| `coast_no_cooling` | Let the house drift naturally |
| `anticipate_warming` | Cool now, hot later — hold comfort to stay ahead |
| `night_setback` | Cooling is easy; relax overnight |
| `comfort_hold` | Hold the normal target |
| `recover_comfort` | House drifted warm; bring it back |
| `start_precool` / `precool_now` | Start early so cooling doesn't fall behind |

The logic lives in the integration, so the automation stays trivial (included in the comfort package):

```yaml
alias: Intelligent HVAC Smart Cooling
triggers:
  - trigger: homeassistant
    event: start
  - trigger: time_pattern
    minutes: /15
conditions:
  - condition: state
    entity_id: climate.thermostat
    state: [cool, heat_cool]
actions:
  - action: intelligent_hvac.apply_smart_cooling_setpoint
    data:
      climate_entity: climate.thermostat
```

Full details: [documentation/SMART_COOLING.md](documentation/SMART_COOLING.md).

## 🔧 Services

| Service | Purpose |
|---|---|
| `intelligent_hvac.apply_smart_cooling_setpoint` | Compute and apply the recommended cooling setpoint (cooling-safe only; never switches HVAC mode) |
| `intelligent_hvac.force_update` | Re-fetch history and relearn now |

## 🔬 How it works

History → uniform resampling → thermal segments → physics model (heat loss + heating power), backtested → sensors.
Read [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) for the layered design, segment definitions and confidence scoring.

## ⏰ Smart start (winter mornings)

Every 5 minutes overnight the comfort package re-plans the morning heat-up:

1. **Predict the indoor temperature at heat-up time.** The house coasts toward the outdoor temperature but never below the thermostat's live setpoint, so a 60°F setback plans differently from a 64°F one.
2. **Use tonight's real heat loss.** A one-hour trend sensor (`sensor.intelligent_hvac_indoor_trend`) measures how fast the house is cooling right now; the plan uses whichever is larger, that or the learned coefficient.
3. **Solve the heat-up time** with the physics model (heating power vs. heat loss), iterated so the start time and the predicted start temperature agree.
4. **Commit 15 minutes before the start.** After that the time is fixed and the morning automation fires on schedule.
5. **Never skip a morning.** If a colder-than-expected night means the start is already due, it heats immediately; a fallback automation backs it up if Home Assistant was down at the start minute.

Wake-up time, target, buffer and the maximum lead are all on the dashboard's Settings view.

## 🗺️ Roadmap

The heating model is being rebuilt on physics (heat-loss + heating-power) and backtested against real recoveries; until that lands, the comfort package includes a **fixed-lead fallback heat-up** so mornings are warm regardless. Details and status: [documentation/ROADMAP.md](documentation/ROADMAP.md).

Feedback and issues very welcome: <https://github.com/JHenzi/IntelligentHVAC/issues>

## 🤝 Contributing

See [documentation/DEVELOPMENT.md](documentation/DEVELOPMENT.md).

## 📄 License

[GPL-3.0](LICENSE)

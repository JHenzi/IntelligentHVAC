# Intelligent HVAC Smart Cooling

This document explains how the `intelligent_hvac` custom component learns your home's thermal behavior, how it decides on cooling setpoints, and how the Home Assistant automation ties into that logic.

## What The Integration Learns

The integration watches three categories of entities:

- `climate` entity
  Usually `climate.thermostat`
- indoor temperature sensors
  For example `sensor.bedroom_temperature`
- outdoor temperature source
  For example `weather.home`

It pulls historical data, resamples it into uniform timesteps, and identifies thermal segments:

- `Active_Heating`
  HVAC is heating and indoor temperature is rising
- `Active_Cooling`
  HVAC is cooling and indoor temperature is falling
- `Passive_Decay`
  HVAC is off and the home is losing heat
- `Passive_Gain`
  HVAC is off and the home is gaining heat

From those segments it learns:

- active heating time-to-target
- active cooling time-to-target
- passive heat loss / passive heat gain rate

## Why Cooling Uses Indoor And Outdoor Delta

The cooling model now uses the temperature difference between outdoors and indoors, not just outdoor temperature by itself.

That matters because cooling difficulty depends on heat load:

- if it is `95°F` outside and `72°F` inside, cooling is harder
- if it is `78°F` outside and `72°F` inside, cooling is easier

So the model estimates cooling time from:

- how many degrees you want to move
- how hot it is outside relative to inside

## Sensors And What They Mean

### `sensor.intelligent_hvac_cooling_holding_margin`

This estimates net cooling headroom in `°F/hr`.

Interpretation:

- positive
  Cooling can still outrun current heat gain
- near zero
  The system is on the edge
- negative
  The house may warm up even while cooling

This is one of the main signals used for deciding whether nighttime setback is safe.

### `sensor.intelligent_hvac_recommended_cooling_setpoint`

This is the temperature `intelligent_hvac` currently wants the thermostat to use.

It is not just a copy of the thermostat setpoint. It is the output of the integration's decision logic.

### `sensor.intelligent_hvac_cooling_strategy`

This tells you why the integration picked the current recommendation.

Current strategies include:

- `open_windows`
  Outdoor air is favorable enough that the home should naturally shed heat, so the system recommends opening windows and backing off AC
- `coast_no_cooling`
  Outdoor air is cool enough that the AC should stop fighting the house and let the home drift naturally
- `anticipate_warming`
  It is cool now, but the forecast and learned model say waiting will likely fall behind later, so hold cooling at comfort early
- `night_setback`
  Conditions are easy enough that the thermostat can relax overnight
- `comfort_hold`
  Hold the normal comfort target
- `recover_comfort`
  Bring the house back to comfort because it has drifted warm
- `start_precool`
  Begin moving back toward comfort before the home falls behind
- `precool_now`
  Conditions are hard enough that the system should cool more aggressively now

### `sensor.intelligent_hvac_pre_cool_lead_time`

This sensor answers:

"How many minutes of lead time are needed to cool from the current setback or indoor temperature back to comfort?"

That means:

- if the house is already at comfort, it can be `0`
- if the thermostat is set warmer overnight, it shows how long the system needs to get back to comfort
- if cooling is slow under current load, the number increases

This is different from the first broken implementation, which only tried to show a value after cooling had already fallen behind.

## Smart Cooling Decision Flow

The cooling controller reads:

- current normalized indoor temperature
- current outdoor temperature
- current thermostat target
- learned thermal model
- configured comfort cooling temperature
- configured night setback temperature
- configured max pre-cool offset
- configured pre-cool lead time
- configured night start / night end window

Then it decides:

1. Is outdoor air favorable enough for natural cooling right now?
2. Does the learned passive model say the home should naturally lose heat or avoid gain?
3. Is the house already warm enough that passive cooling is not enough?
4. Is the forecasted warm-up strong enough that waiting will create recovery risk?
5. Are we inside the night window?
6. Can cooling keep up, based on `cooling_holding_margin`?
7. Should we open windows, coast without cooling, hold comfort, or pre-cool now?

In plain language:

- if outdoor air is cool and the learned model says the house should naturally lose heat, the controller can recommend `open_windows` or `coast_no_cooling`
- if the house is already too warm, it skips passive-cooling guidance and recovers comfort
- if it is cool now but the forecast high is hot enough that waiting would likely fall behind, the controller switches to `anticipate_warming`
- if nighttime is mild and cooling is easy, it can still use the normal setback / pre-cool behavior

## What Happens

This section describes the actual behavior the component now aims for.

### Cool Outside And The House Is Already Cooling Down

If outdoor air is cooler than indoors, and the recent indoor trend shows the house is already drifting down on its own:

- the controller should not push aggressive AC
- it prefers `open_windows` or `coast_no_cooling`
- it raises the cooling target high enough that the AC can idle
- if the thermostat is in `heat_cool`, it only changes the cooling side and does not create a heating call

In practice this means a cool night should usually result in a higher cooling target, not a lower one.

### Cool Outside But The House Is Not Cooling Fast Enough

If outdoor air is favorable, but the house is not actually dropping on its own:

- the controller may still choose `coast_no_cooling`
- it avoids fighting the house with unnecessary AC
- window guidance remains advisory only

This is the “let the house breathe a bit” case.

### Cool Morning Now, Hot Afternoon Later

If it feels cool now, but the forecast high is materially above comfort and the model thinks waiting will create recovery risk:

- the controller can choose `anticipate_warming`
- it holds cooling at the normal comfort target instead of fully relaxing
- it does not overcool below comfort just because the afternoon will be warm

This is the “stay ahead without being wasteful” case.

### Nighttime Behavior

Nighttime no longer means “drop the AC lower just because pre-cool exists.”

Instead:

- if it is still warmer outside than the comfort target, the controller should keep the normal comfort target and wait before allowing the night setback
- if natural cooling is favorable, nighttime should prefer `open_windows` or `coast_no_cooling`
- if the house is already cooling down, that observed trend blocks `anticipate_warming`, `start_precool`, and `precool_now`
- `precool_now` should only happen when outdoor conditions are not naturally helping enough and the forecast heat pressure is genuinely high

So if it is cool at night and the home is already trending downward, the controller should back off cooling rather than drive the setpoint lower.

Morning no longer means “go back to `72°F` exactly when the night window ends.”

Instead:

- if the thermostat is still relaxed from overnight and the outdoor air is still cooler than the comfort target, the controller should continue coasting
- it should only switch back to comfort when warming is actually starting to matter, such as when outdoor temperature is near comfort and rising, or passive cooling help is nearly gone

### If The House Is Already Too Warm

If the indoor temperature is already materially above comfort:

- the controller skips passive-cooling guidance
- it uses `recover_comfort`
- the goal becomes getting the house back to normal comfort rather than coasting

This prevents “it is cool out” from overriding the obvious fact that the house is still too warm.

### What The Guidance Means

The guidance strategies are intended to be human-readable:

- `open_windows`: outdoor conditions are good enough that opening windows is worth considering
- `coast_no_cooling`: stop actively cooling and let the house drift naturally
- `anticipate_warming`: stay at comfort now so the house does not fall behind later

Also inspect these attributes on the cooling sensors when debugging:

- `guidance_message`
- `forecast_high_temp_f`
- `forecast_risk_score`
- `passive_cooling_rate_f_per_hour`
- `indoor_trend_rate_f_per_hour`
- `indoor_temp_falling`
- `indoor_temp_rising`
- `indoor_temp_stable`

## Example

Suppose:

- comfort temp = `72°F`
- night setback = `74°F`
- outdoor = `75°F`
- indoor = `73°F`
- holding margin = `1.33°F/hr`

That means the AC still has extra capacity. The integration will usually allow the overnight setback because there is no need to strain the system for `72°F` yet.

If tomorrow becomes much hotter and the model believes recovery from `74°F` to `72°F` will take too long, the strategy shifts from `night_setback` to `start_precool` or `precool_now`.

## What The Automation Does

The Home Assistant automation should be simple. It should not contain HVAC intelligence itself.

Its job is only:

1. run every 15 minutes and at startup
2. check that the thermostat is in a cooling-capable mode
3. call `intelligent_hvac.apply_smart_cooling_setpoint`

The service then:

1. resolves the configured `intelligent_hvac` entry from the human-readable climate entity, or from the single installed entry
2. calculates the recommended cooling setpoint and guidance state
3. calls `climate.set_temperature`
4. applies only cooling-safe temperature changes without switching HVAC mode

So:

- the automation is the messenger
- the custom component is the decision maker

## Important Operational Rule

If you want `intelligent_hvac` to be in charge, avoid overlapping thermostat automations that also change the same setpoint on their own.

Otherwise the system can fight itself:

- one automation sets `74`
- another sets `72`
- the predictor sets something else 10 minutes later

The cleanest setup is one thermostat automation for smart cooling, and that automation should only call the `intelligent_hvac` service.

## Recommended Single Automation

```yaml
- id: '1770000000001'
  alias: Intelligent HVAC Smart Cooling
  description: Let Intelligent HVAC set the thermostat based on learned cooling behavior.
  triggers:
  - trigger: homeassistant
    event: start
  - trigger: time_pattern
    minutes: /15
  conditions:
  - condition: template
    value_template: >-
      {{ states('climate.thermostat') in ['cool', 'heat_cool'] }}
  actions:
  - action: intelligent_hvac.apply_smart_cooling_setpoint
    data:
      climate_entity: climate.thermostat
  mode: single
```

If you have only one Intelligent HVAC entry installed, the service can also be called without any `data` block.

## How To Inspect The System Live

Use these entities together:

- `sensor.intelligent_hvac_cooling_strategy`
- `sensor.intelligent_hvac_recommended_cooling_setpoint`
- `sensor.intelligent_hvac_cooling_holding_margin`
- `sensor.intelligent_hvac_pre_cool_lead_time`
- `climate.thermostat`

That gives you:

- what the integration wants
- why it wants it
- how hard cooling currently is
- how much recovery lead time is needed
- what the thermostat is actually set to

When outdoor weather is mild, look for `sensor.intelligent_hvac_cooling_strategy` to show
`open_windows` or `coast_no_cooling`.
That means the controller thinks the home can naturally lose heat and does not need aggressive AC right now.

Spring guidance strategies to look for:

- `open_windows`
- `coast_no_cooling`
- `anticipate_warming`

Also check the `guidance_message` attribute on the cooling sensors for the human-readable recommendation.

## Short Summary

`intelligent_hvac` is doing two separate jobs:

- learning how your house heats and cools
- turning that learned model into smarter cooling setpoints

The goal is not just "make it colder."

The goal is:

- keep comfort when the system needs help
- relax at night when the home is easy to manage
- pre-cool early enough to avoid falling behind
- avoid unnecessary strain when the model says the house is under control

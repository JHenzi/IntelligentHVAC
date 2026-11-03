# Development

## Hot reload (no container restart)

Mount `custom_components` into your HA container, e.g. `./custom_components:/config/custom_components`, then after editing Python files reload the config entry:

- **UI:** Settings → Devices & Services → Intelligent HVAC → ⋮ → Reload
- **Service:** `homeassistant.reload_config_entry` targeting the entry

## Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.intelligent_hvac: debug
```

## Layout

```
intelligent_hvac/
├── __init__.py        setup, services
├── config_flow.py     configuration UI
├── const.py           constants & thresholds
├── coordinator.py     async data coordinator
├── history.py         HA history API wrapper
├── resampling.py      uniform time-series resampling
├── normalization.py   composite indoor temperature
├── segmentation.py    thermal segmentation
├── learning.py        linear-regression model + confidence
├── models.py          ThermalSegment / ThermalModel
├── storage.py         raw data & model persistence
├── guidance.py        pure cooling-strategy logic
├── smart_control.py   recommended setpoint logic
├── sensor.py          output sensors
└── tests/
```

## Build / validation order

When changing the pipeline, validate each stage before the next:

1. **Ingestion + resampling** — inspect persisted raw data; timesteps should be uniform, gaps visible.
2. **Segmentation** — no segments < 10 min, every segment has an outdoor temperature, types match definitions.
3. **Learning** — confidence starts low and rises with data; parameters are physically plausible.
4. **Sensors** — all update; model JSON is complete.

## Troubleshooting

- *Integration not found:* directory name must equal the domain (`intelligent_hvac`) and `manifest.json` must be valid.
- *Changes not appearing:* confirm the volume mount and that the entry was reloaded; check logs for import errors.
- *First load is slow:* the initial history fetch can take 30–60 s; later updates are incremental.

## Tests

```bash
pytest tests/
```

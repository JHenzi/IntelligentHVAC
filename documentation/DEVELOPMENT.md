# Development

## Layout

```
custom_components/intelligent_hvac/   the integration (what HACS installs)
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
├── strings.json       UI text (translations/en.json is a copy)
packages/              comfort package (helpers, script, automations)
dashboards/            Lovelace dashboard
tests/                 pytest (pytest-homeassistant-custom-component)
dev/sandbox/           throw-away HA instance + end-to-end checks
scripts/deploy.sh      install a checkout into an HA config dir
```

## Three levels of testing

| Level | What it proves | Command | Runs in CI |
|---|---|---|---|
| Unit / HA harness | guidance logic, config flow against an in-memory HA | `pytest` | ✔ |
| Sandbox E2E | integration loads in a real HA, package config is valid, recovery math, setback bands, heat-up gating, forecast switch, helpers survive restart | `dev/sandbox/e2e.py --restart` | ✔ (HA stable + beta) |
| Your house | the model learns real physics; you wake up warm | soak test on production | — |

### pytest

Needs the Python version the current HA release uses (3.14 for 2026.x). Without that locally, use Docker:

```bash
docker run --rm -v "$PWD:/src:ro" -w /tmp python:3.14-slim sh -c \
  'cp -r /src r && cd r && pip install -q -r requirements_test.txt && pytest -q -p no:logging'
```

### Sandbox

A second Home Assistant on **http://localhost:8125**. It is safe to run next to production: it uses bridge networking (separate port, no discovery on the host network) and **no real devices**. `demo:` provides a fake thermostat and weather, and sliders stand in for the outdoor sensor and the learned model.

```bash
dev/sandbox/prepare.sh                    # build dev/sandbox/config from the repo
(cd dev/sandbox && docker compose up -d)  # HA_VERSION=beta to try the next release
python3 dev/sandbox/e2e.py --restart      # automated checks; exit code = failures
```

Log in at http://localhost:8125 as `sandbox` / `sandbox-only-password` (created by `e2e.py`) to poke at things by hand. Re-run `prepare.sh` + restart the container after code changes. To start from scratch: `docker compose down && rm -rf dev/sandbox/config`.

> **Never point the sandbox at a copy of your production config.** It would sign in to your real thermostat and cloud accounts and run your real automations a second time.

The sandbox can't test learning (it has no history) or anything clock-bound (the 05:00–10:00 lockout, the 22:00 setback firing on its own). Those go in the production soak.

## Deploying to your own HA

```bash
scripts/deploy.sh /path/to/ha/config            # integration only, like HACS
scripts/deploy.sh /path/to/ha/config --package  # + comfort package, verbatim
```

Then reload the entry (Settings → Devices & Services → Intelligent HVAC → ⋮ → Reload) for Python changes, or restart HA.

## Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.intelligent_hvac: debug
```

## Pipeline validation order

When changing the pipeline, validate each stage before the next:

1. **Ingestion + resampling**: inspect persisted raw data. Timesteps should be uniform and gaps visible.
2. **Segmentation**: no segments < 10 min, every segment has an outdoor temperature, types match definitions.
3. **Learning**: confidence starts low and rises with data, and parameters are physically plausible.
4. **Sensors**: all update, and the model JSON is complete.

## Releasing

1. Bump `version` in `custom_components/intelligent_hvac/manifest.json` (and `VERSION` in `const.py`).
2. Merge to `master` with CI green.
3. `git tag vX.Y.Z && git push --tags`. The release workflow checks the tag matches the manifest and publishes a GitHub release, which is what HACS offers users.

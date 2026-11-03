# Security policy

Intelligent HVAC runs inside Home Assistant and can change your thermostat setpoint, so bugs that cause it to act unexpectedly count as security issues too.

## Reporting

Please **don't open a public issue**. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository. I'll acknowledge within a week.

## Supported versions

Only the latest release gets fixes.

## Scope

- The integration (`custom_components/intelligent_hvac/`): it reads recorder history, writes JSON files under `<config>/intelligent_hvac/`, and calls `climate.set_temperature` / `climate.set_hvac_mode`.
- The comfort package and dashboard YAML.

There are no network calls, cloud services or credentials in this project.

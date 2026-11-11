#!/usr/bin/env bash
# Build dev/sandbox/config/ from the repo: sandbox configuration, fakes, the
# integration (via deploy.sh) and the comfort package with entity IDs
# rewritten to the demo/fake entities. Safe to re-run; HA state in
# config/.storage is kept.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CFG="$HERE/config"

mkdir -p "$CFG/packages"
cp "$HERE/configuration.yaml" "$CFG/configuration.yaml"
cp "$HERE/sandbox_fakes.yaml" "$CFG/packages/sandbox_fakes.yaml"
"$REPO/scripts/deploy.sh" "$CFG" >/dev/null

# The same find & replace packages/README.md asks users to do.
sed -e 's/climate\.thermostat\b/climate.hvac/g' \
    -e 's/weather\.home\b/weather.demo_weather_south/g' \
    -e 's/sensor\.my_outdoor_temperature\b/sensor.sandbox_outdoor_temperature/g' \
    -e 's/sensor\.intelligent_hvac_time_to_target_5degf_heat\b/sensor.sandbox_time_to_target_heat/g' \
    -e 's/sensor\.intelligent_hvac_learned_indoor_temperature\b/sensor.sandbox_indoor_temperature/g' \
    -e "s/states('sensor\.intelligent_hvac_indoor_trend')/states('sensor.sandbox_indoor_trend')/g" \
    "$REPO/packages/intelligent_hvac_comfort.yaml" > "$CFG/packages/intelligent_hvac_comfort.yaml"

echo "sandbox config ready: $CFG"

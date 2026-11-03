#!/usr/bin/env bash
# Install this checkout into a Home Assistant config directory the same way
# HACS would: only custom_components/intelligent_hvac/ is copied.
#
#   scripts/deploy.sh /path/to/ha/config            # integration only
#   scripts/deploy.sh /path/to/ha/config --package  # + packages/intelligent_hvac_comfort.yaml
#
# The comfort package is copied verbatim. If your entity IDs differ from the
# defaults, keep your edited copy in the HA config and don't use --package.
set -euo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
[[ $# -ge 1 ]] || usage

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$(cd "$1" && pwd)"
WITH_PACKAGE=0
[[ "${2:-}" == "--package" ]] && WITH_PACKAGE=1

SRC="$REPO/custom_components/intelligent_hvac/"
DEST="$CONFIG/custom_components/intelligent_hvac/"

[[ -f "$CONFIG/configuration.yaml" ]] || { echo "error: $CONFIG has no configuration.yaml" >&2; exit 1; }

# Safety: the legacy setup kept the git checkout *inside* the HA config.
# rsync --delete would wipe it, so refuse until it has been moved aside.
if [[ -e "$DEST/.git" ]]; then
  echo "error: $DEST is a git checkout (legacy layout)." >&2
  echo "       Move it aside first, e.g.: mv '$DEST' '${DEST%/}.legacy'" >&2
  exit 1
fi

mkdir -p "$DEST"
rsync -a --delete --exclude '__pycache__/' "$SRC" "$DEST"
echo "integration -> $DEST ($(jq -r .version "$SRC/manifest.json" 2>/dev/null || echo '?'))"

if [[ $WITH_PACKAGE -eq 1 ]]; then
  mkdir -p "$CONFIG/packages"
  cp "$REPO/packages/intelligent_hvac_comfort.yaml" "$CONFIG/packages/"
  echo "package     -> $CONFIG/packages/intelligent_hvac_comfort.yaml"
fi

echo "Now: Developer Tools -> YAML -> Check configuration, then restart HA."

#!/usr/bin/env bash
# Run the Wine Cellar app locally (no Home Assistant needed).
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/dev.sh
# Optional: WINE_LED_DRIVER=wled WINE_WLED_HOST=192.168.1.50
#           or HA_URL=http://homeassistant.local:8123/api HA_TOKEN=<long-lived token> WINE_LED_DRIVER=esphome
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r wine_cellar/requirements.txt pytest
export WINE_DATA_DIR="${WINE_DATA_DIR:-$PWD/.devdata}"
export WINE_OPTIONS="${WINE_OPTIONS:-$PWD/.devdata/options.json}"
export WINE_LED_DRIVER="${WINE_LED_DRIVER:-none}"
cd wine_cellar
exec ../.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port "${PORT:-8099}"

#!/usr/bin/with-contenv bashio
# with-contenv exposes SUPERVISOR_TOKEN so the app can call Home Assistant.
bashio::log.info "Starting Wine Cellar"
cd /opt/wine
exec python3 -m uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port 8099 --proxy-headers --forwarded-allow-ips='*' --no-access-log

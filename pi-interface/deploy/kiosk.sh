#!/bin/sh
# Open the printer UI full-screen on the Pi's DSI touchscreen once the local
# service answers. Started from ~/.config/labwc/autostart (docs/deployment.md).
# Local only: http://localhost — no internet needed.
URL="${MUDPRINTER_URL:-http://localhost:8000}"
i=0
while [ $i -lt 120 ]; do
  curl -fs -o /dev/null "$URL/api/state" && break
  i=$((i + 1))
  sleep 1
done
BROWSER="$(command -v chromium || command -v chromium-browser)"
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-pinch --overscroll-history-navigation=0 \
  --password-store=basic --incognito "$URL"

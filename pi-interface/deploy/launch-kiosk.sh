#!/usr/bin/env bash
#
# Mud Printer Controller — desktop launcher
# ------------------------------------------
# Launched by the ~/Desktop/Mud Printer Controller.desktop entry (double-tap
# on the 7" touchscreen or double-click on an attached mouse). Opens the
# controller in Chromium kiosk mode. The backend (mudprinter.service) keeps
# running independently.
#
# Order of operations:
#   1. If a kiosk is ALREADY running from a previous launch, do nothing.
#   2. Start the mudprinter.service if it isn't active. Uses passwordless
#      sudo configured in /etc/sudoers.d/mudprinter-controller; if that is
#      not installed yet the user may be prompted graphically or the start
#      is skipped and Chromium shows a connection error.
#   3. Wait up to 30 s for /api/state to answer.
#   4. Launch Chromium in --kiosk, write its PID to a shared file so the
#      backend's /api/exit_kiosk endpoint can signal it to close without
#      stopping the service.
#   5. When Chromium exits, remove the PID file. The service stays running.
#
# This script is DIFFERENT from pi-interface/deploy/kiosk.sh:
#   - kiosk.sh is called from ~/.config/labwc/autostart at boot and simply
#     waits for the service that systemd started, then exec's Chromium.
#   - launch-kiosk.sh is the operator-triggered path; it also starts the
#     service if needed, and records Chromium's PID for the exit endpoint.
#
set -u

PIDFILE=/tmp/mudprinter-kiosk.pid
URL="${MUDPRINTER_URL:-http://localhost:8000}"

# ---- 1. Reuse existing kiosk if one is already running ---------------
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
    # A kiosk is already running — raise it to the top if possible, else
    # just exit silently. Attempting to start a second Chromium kiosk fails.
    if command -v wmctrl >/dev/null 2>&1; then
        wmctrl -a chromium 2>/dev/null || true
    fi
    exit 0
fi

# ---- 2. Start the service if it isn't running -------------------------
if ! systemctl is-active --quiet mudprinter; then
    # -n = non-interactive. If sudoers isn't configured yet this fails
    # silently; the HTTP wait below will time out and Chromium shows an
    # error page the operator can act on.
    sudo -n systemctl start mudprinter 2>/dev/null || true
fi

# ---- 3. Wait for the HTTP endpoint ------------------------------------
for _ in $(seq 1 60); do
    if curl -fsS -m 1 "$URL/api/state" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# ---- 4. Launch Chromium kiosk; record PID -----------------------------
BROWSER="$(command -v chromium || command -v chromium-browser)"
if [ -z "$BROWSER" ]; then
    # Fall back: tell the operator via a graphical dialog if available.
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --text="Chromium is not installed on this Pi."
    fi
    exit 1
fi

"$BROWSER" \
    --kiosk --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-pinch \
    --overscroll-history-navigation=0 \
    --password-store=basic --incognito \
    "$URL" &
CHROMIUM_PID=$!
echo "$CHROMIUM_PID" > "$PIDFILE"

# ---- 5. Wait for Chromium to exit, then clean up ----------------------
wait "$CHROMIUM_PID" 2>/dev/null || true
rm -f "$PIDFILE"

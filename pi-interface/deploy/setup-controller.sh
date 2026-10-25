#!/usr/bin/env bash
#
# Mud Printer Controller — one-time Pi setup.
# Installs:
#   /etc/sudoers.d/mudprinter-controller   (narrow passwordless sudo for
#                                           systemctl mudprinter)
#   ~/Desktop/Mud Printer Controller.desktop  (double-tap launcher)
#   pi-interface/deploy/launch-kiosk.sh is marked executable (git should
#       already do this, but we verify)
# After this script succeeds the Pi is ready for autonomous Claude Code
# deployments and the desktop icon works.
#
# RUN ONCE from the Mac (after a normal Deploy to Pi.command so the files
# exist on the Pi):
#   ssh -t dirtworks@192.168.1.144 \
#       sudo bash /home/dirtworks/mudprinter-controller/pi-interface/deploy/setup-controller.sh
#
# Safe to run multiple times — it is idempotent.
#
set -euo pipefail

REPO="${REPO:-/home/dirtworks/mudprinter-controller}"
DEPLOY_DIR="$REPO/pi-interface/deploy"
TARGET_USER="${TARGET_USER:-dirtworks}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

say()  { printf "\n==> %s\n" "$*"; }
die()  { printf "\nFAIL: %s\n" "$1" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Must be run as root (e.g. 'sudo bash $(basename "$0")')"
fi

# ---------------------------------------------------------------------------
# 1. Install sudoers rule for mudprinter.service.
# ---------------------------------------------------------------------------
say "Installing /etc/sudoers.d/mudprinter-controller ..."
SUDOERS_SRC="$DEPLOY_DIR/mudprinter-controller.sudoers"
SUDOERS_DST=/etc/sudoers.d/mudprinter-controller
if [[ ! -f "$SUDOERS_SRC" ]]; then
    die "Source file missing: $SUDOERS_SRC"
fi

# Validate syntax BEFORE copying.
if ! visudo -cf "$SUDOERS_SRC" >/dev/null 2>&1; then
    die "Source sudoers file fails visudo validation: $SUDOERS_SRC"
fi

install -m 0440 -o root -g root "$SUDOERS_SRC" "$SUDOERS_DST"
visudo -c >/dev/null  # final whole-tree check
echo "   installed and validated."

# ---------------------------------------------------------------------------
# 2. Install the desktop launcher icon.
# ---------------------------------------------------------------------------
say "Installing desktop launcher ..."
DESKTOP_SRC="$DEPLOY_DIR/Mud Printer Controller.desktop"
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    die "Cannot find home directory for $TARGET_USER"
fi
DESKTOP_DIR="$TARGET_HOME/Desktop"
# Create Desktop/ if the window manager didn't make it.
install -d -o "$TARGET_USER" -g "$TARGET_USER" -m 0755 "$DESKTOP_DIR"

DESKTOP_DST="$DESKTOP_DIR/Mud Printer Controller.desktop"
install -m 0755 -o "$TARGET_USER" -g "$TARGET_USER" "$DESKTOP_SRC" "$DESKTOP_DST"
echo "   installed at $DESKTOP_DST"

# PCManFM / LXDE needs the file to be executable AND "trusted" so a
# double-click runs it directly instead of asking what to do with the
# text file. GNOME uses the same mechanism (gio set metadata). We set
# both to be safe; the gio call fails silently on bare labwc.
sudo -u "$TARGET_USER" gio set "$DESKTOP_DST" metadata::trusted true 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. Make sure launch-kiosk.sh is executable.
# ---------------------------------------------------------------------------
say "Ensuring launch-kiosk.sh is executable ..."
LAUNCHER="$DEPLOY_DIR/launch-kiosk.sh"
if [[ ! -f "$LAUNCHER" ]]; then
    die "Launcher missing: $LAUNCHER"
fi
chmod 0755 "$LAUNCHER"
echo "   $LAUNCHER is executable."

# ---------------------------------------------------------------------------
# 4. Verify the passwordless sudo rule actually works for the target user.
# ---------------------------------------------------------------------------
say "Verifying passwordless sudo for systemctl is-active mudprinter ..."
if sudo -u "$TARGET_USER" sudo -n systemctl is-active --quiet mudprinter 2>/dev/null; then
    echo "   OK — service is active and $TARGET_USER can query it without a password."
else
    rc=$?
    if [[ $rc -eq 3 ]]; then
        echo "   OK — passwordless sudo works (service currently inactive)."
    else
        echo "   WARN — exit $rc. Sudoers may need a logout/login or 'sudo -k'."
    fi
fi

echo ""
echo "Setup complete."
echo "The 'Mud Printer Controller' icon should appear on the desktop; double-"
echo "tap it to launch the kiosk. From the controller, hold the top-right"
echo "'Exit' button to leave kiosk mode without stopping the backend."

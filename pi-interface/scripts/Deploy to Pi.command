#!/usr/bin/env bash
#
# Mud Printer — one-click deploy from Mac to Pi.
#
# Double-click this file in Finder. macOS opens it in Terminal, this script
# SSHes into the Pi, runs scripts/deploy_pi.sh over the connection, and prints
# the result. The actual safety checks + git pull + pip install + service
# restart all run on the Pi (see deploy_pi.sh).
#
# One-time setup is documented in docs/one-click-deploy.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/deploy.config"
SAMPLE="$SCRIPT_DIR/deploy.config.sample"

cd "$SCRIPT_DIR"

printf "Mud Printer — one-click deploy\n"
printf "===============================\n\n"

if [[ ! -f "$CONFIG" ]]; then
    printf "No %s found.\n" "$CONFIG"
    printf "Copy %s to %s and edit PI_HOST / PI_USER before running.\n\n" \
        "deploy.config.sample" "deploy.config"
    read -r -p "Press Enter to close this window... " _
    exit 1
fi

# shellcheck source=./deploy.config.sample
source "$CONFIG"

: "${PI_HOST:?Set PI_HOST in deploy.config}"
: "${PI_USER:?Set PI_USER in deploy.config}"

# REPO_DIR is the ABSOLUTE path to the controller repo on the Pi. Deriving it
# here (Mac side) means the string the Pi receives has NO shell variables left
# to expand, so no quoting hazard across the ssh boundary. Default matches the
# consolidation-migration layout (/home/<PI_USER>/mudprinter-controller); the
# operator overrides via deploy.config when their layout differs.
REPO_DIR="${REPO_DIR:-/home/${PI_USER}/mudprinter-controller}"
REMOTE_SCRIPT="${REMOTE_SCRIPT:-${REPO_DIR}/pi-interface/scripts/deploy_pi.sh}"

# Build environment-override prefix. REPO_DIR / SERVICE / HOST_URL become env
# vars so deploy_pi.sh picks them up. Every value is quoted so paths with
# spaces survive the ssh hop (the Pi's shell re-parses this string).
remote_env=""
for v in REPO_DIR SERVICE HOST_URL; do
    if [[ -n "${!v:-}" ]]; then
        # shellcheck disable=SC2089
        remote_env+="$v=\"${!v}\" "
    fi
done

printf "Target : %s@%s\n" "$PI_USER" "$PI_HOST"
printf "Repo   : %s\n" "$REPO_DIR"
printf "Script : %s\n\n" "$REMOTE_SCRIPT"
printf "Connecting (sudo may prompt for the Pi's password) ...\n\n"

# -t allocates a TTY so interactive `sudo` on the Pi works.
# ServerAliveInterval keeps a long install from timing out behind a NAT.
# The remote command is a single string the Pi's login shell parses. We use
# DOUBLE quotes around $REMOTE_SCRIPT so the Pi's shell sees them as syntax
# (not literal), preserves any spaces, and does not try to expand it as a
# variable (it is already a fully-resolved absolute path).
if ssh \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=4 \
    -t \
    "$PI_USER@$PI_HOST" \
    "${remote_env}bash \"${REMOTE_SCRIPT}\""
then
    rc=0
else
    rc=$?
fi

printf "\n"
case "$rc" in
    0)
        printf "DEPLOY SUCCEEDED\n"
        ;;
    2)
        printf "DEPLOY REFUSED — controller was not in a safe state.\n"
        printf "The Pi did not restart. Nothing was changed.\n"
        ;;
    3)
        printf "DEPLOY REFUSED — repository on the Pi needs attention.\n"
        printf "The Pi did not restart. Nothing was changed.\n"
        ;;
    4)
        printf "DEPLOY RAN BUT POST-RESTART VERIFICATION FAILED.\n"
        printf "The service may be down. Check it manually from the Pi:\n"
        printf "    ssh %s@%s 'sudo journalctl -u mudprinter -n 100 --no-pager'\n" \
            "$PI_USER" "$PI_HOST"
        ;;
    255)
        printf "DEPLOY FAILED — SSH connection problem.\n"
        printf "Check Pi hostname / network / SSH keys.\n"
        ;;
    *)
        printf "DEPLOY FAILED (exit %d).\n" "$rc"
        ;;
esac

printf "\n"
read -r -p "Press Enter to close this window... " _
exit "$rc"

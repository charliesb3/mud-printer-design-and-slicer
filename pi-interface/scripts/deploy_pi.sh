#!/usr/bin/env bash
#
# Pi-side one-click deploy — called from the Mac launcher over SSH.
#
# Does, in order:
#   1. Reads /api/state and refuses to continue if a job or jog is active,
#      or if the controller state cannot be verified.
#   2. git pull --ff-only in the repo (refuses if the working tree is dirty).
#   3. pip install -r requirements.txt only if requirements.txt actually changed
#      since the last successful deploy.
#   4. sudo systemctl restart <SERVICE>.
#   5. Verifies the service is active AND the HTTP /api/state endpoint responds.
#
# Nothing in this script touches .env, /dev/*, GRBL firmware, or GRBL settings.
#
# Environment overrides (defaults in parens):
#   REPO_DIR    (~/mudprinter-controller — the consolidation-migration layout)
#   SERVICE     (mudprinter)
#   HOST_URL    (http://localhost:8000)
#
# Exit codes:
#   0  success
#   2  refused — controller not in a safe state
#   3  refused — repo state unsafe (dirty / detached / can't fast-forward)
#   4  deploy ran but post-restart verification failed
#   1  anything else

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/mudprinter-controller}"
PI_IFACE_DIR="$REPO_DIR/pi-interface"
VENV="$PI_IFACE_DIR/.venv"
SERVICE="${SERVICE:-mudprinter}"
HOST_URL="${HOST_URL:-http://localhost:8000}"

say()  { printf "\n==> %s\n" "$*"; }
warn() { printf "\n** %s\n" "$*" >&2; }
die()  { local code="${2:-1}"; printf "\nFAIL: %s\n" "$1" >&2; exit "$code"; }

# ---------------------------------------------------------------------------
# 1. Preflight — controller must be in a safe, verifiable state.
# ---------------------------------------------------------------------------
say "Checking current controller state at $HOST_URL/api/state ..."
state_json="$(curl -fsS -m 5 "$HOST_URL/api/state" 2>/dev/null || true)"
if [[ -z "$state_json" ]]; then
    die "Could not read $HOST_URL/api/state. Controller unreachable, so its state cannot be verified — refusing to restart." 2
fi

# The pass/block decision lives in a dedicated, unit-tested module.
PREFLIGHT="$PI_IFACE_DIR/scripts/preflight_state.py"
if [[ ! -x "$PREFLIGHT" ]]; then
    die "Preflight module missing or not executable: $PREFLIGHT" 1
fi
if ! printf '%s' "$state_json" | python3 "$PREFLIGHT"; then
    die "Preflight blocked deploy — see message above." 2
fi

# ---------------------------------------------------------------------------
# 2. Git pull.
# ---------------------------------------------------------------------------
say "Updating repo at $REPO_DIR ..."
cd "$REPO_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "$REPO_DIR is not a git repo — refusing to deploy." 3
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree at $REPO_DIR has uncommitted changes — refusing to pull. Resolve on the Pi and re-run." 3
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo DETACHED)"
if [[ "$BRANCH" == "HEAD" || "$BRANCH" == "DETACHED" ]]; then
    die "Pi repo is in detached-HEAD state — refusing to pull. Check out a branch on the Pi first." 3
fi

BEFORE="$(git rev-parse HEAD)"
git fetch --quiet
if ! git pull --ff-only --quiet; then
    die "git pull failed (non-fast-forward or network error) on branch $BRANCH." 3
fi
AFTER="$(git rev-parse HEAD)"

if [[ "$BEFORE" == "$AFTER" ]]; then
    say "Already up to date on $BRANCH ($AFTER)."
    NO_CODE_CHANGES=1
else
    say "Updated $BRANCH: $BEFORE → $AFTER"
    NO_CODE_CHANGES=0
fi

# ---------------------------------------------------------------------------
# 3. Install deps iff requirements.txt changed.
# ---------------------------------------------------------------------------
cd "$PI_IFACE_DIR"
if [[ ! -d "$VENV" ]]; then
    die "Virtualenv missing at $VENV. Run first-time deployment (docs/deployment.md) before using this script." 3
fi

REQ_HASH_FILE="$VENV/.deployed-requirements.sha256"
CUR_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
LAST_HASH="$(cat "$REQ_HASH_FILE" 2>/dev/null || true)"

if [[ "$CUR_HASH" != "$LAST_HASH" ]]; then
    say "requirements.txt changed since last deploy — installing dependencies ..."
    "$VENV/bin/pip" install --disable-pip-version-check -q -r requirements.txt
    echo "$CUR_HASH" > "$REQ_HASH_FILE"
else
    say "requirements.txt unchanged — skipping pip install."
fi

# ---------------------------------------------------------------------------
# 4. Restart.
# ---------------------------------------------------------------------------
say "Restarting systemd service: $SERVICE ..."
sudo systemctl restart "$SERVICE"

# ---------------------------------------------------------------------------
# 5. Verify.
# ---------------------------------------------------------------------------
say "Waiting for $SERVICE to come back up ..."
HTTP_OK=0
for _ in $(seq 1 30); do
    sleep 0.5
    if curl -fsS -m 2 "$HOST_URL/api/state" >/dev/null 2>&1; then
        HTTP_OK=1
        break
    fi
done

if ! systemctl is-active --quiet "$SERVICE"; then
    die "systemd reports $SERVICE is NOT active after restart. Check: sudo journalctl -u $SERVICE -n 100 --no-pager" 4
fi

if [[ "$HTTP_OK" -ne 1 ]]; then
    die "$SERVICE is active but $HOST_URL/api/state did not respond within 15 s. Check: sudo journalctl -u $SERVICE -n 100 --no-pager" 4
fi

say "DEPLOY OK — $SERVICE active, HTTP responsive, head at $AFTER"
if [[ "$NO_CODE_CHANGES" -eq 1 ]]; then
    echo "    (no new commits pulled)"
fi
exit 0

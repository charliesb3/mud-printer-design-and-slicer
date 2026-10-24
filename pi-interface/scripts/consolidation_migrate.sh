#!/bin/bash
#
# Consolidation migration — Phase M4 atomic directory swap, verify,
# auto-rollback on failure. Documented in
# `pi-interface/docs/consolidation-migration.md`.
#
# Invoke on the Pi, as root:
#     sudo bash <repo>/pi-interface/scripts/consolidation_migrate.sh
#
# Expects M1-M3 to have already been completed:
#   - ~/mudprinter-controller.NEW  = fresh checkout of the target branch
#   - ~/mudprinter-controller.NEW/pi-interface/.venv  with runnable
#     uvicorn (shebangs may reference the .NEW staging path — this
#     script rewrites them to the live path after the rename).
#
# Preserves:
#   - Running controller's .env, jobs/, machine_profile.json, GRBL
#     capture logs (all in .NEW already after M2).
#   - GRBL firmware, GRBL settings — this script never touches them.
#   - systemd unit file — never edited; this is a mv of directories at
#     the path ExecStart already references.
#
# Revisions:
#  2026-10-23 a — initial version (buggy preflight heredoc, abandoned)
#  2026-10-23 b — preflight calls preflight_state.py; heredoc bugs fixed
#  2026-10-23 c — venv shebang rewrite step after mv (fixes 203/EXEC) +
#                 pre-start sanity check that uvicorn actually runs;
#                 rolls back BEFORE systemctl start if the venv is
#                 broken, so the operator never sees a flapping
#                 failed-start loop.
#
# Exits:  0 success  2 preflight refused  4 auto-rolled back  5 rollback failed

set -u

HOME_DIR=/home/dirtworks
LIVE="$HOME_DIR/mudprinter-controller"
NEW="$HOME_DIR/mudprinter-controller.NEW"
STAGING="$NEW"                               # staging path baked into venv binaries during M3
NEW_PREFLIGHT="$NEW/pi-interface/scripts/preflight_state.py"
LIVE_PREFLIGHT="$LIVE/pi-interface/scripts/preflight_state.py"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
BAK="$HOME_DIR/mudprinter-controller.BAK-$STAMP"
LOG="/tmp/m4_swap-$STAMP.log"

: > "$LOG"
say()  { printf "\n== %s ==\n" "$*" | tee -a "$LOG"; }
warn() { printf "\n!! %s\n" "$*" | tee -a "$LOG" >&2; }
info() { printf "   %s\n" "$*" | tee -a "$LOG"; }

rollback() {
    local reason="$1"
    say "VERIFICATION FAILED ($reason) — rolling back"
    local failed="$HOME_DIR/mudprinter-controller.FAILED-$STAMP"
    # Stop whatever we started (ignore errors; may already be dead).
    systemctl stop mudprinter 2>/dev/null || true
    sleep 1
    mv "$LIVE" "$failed"
    mv "$BAK"  "$LIVE"
    systemctl start mudprinter
    local http_ok=0
    for i in $(seq 1 30); do
        sleep 1
        if curl -fsS -m 2 http://localhost:8000/api/state >/dev/null 2>&1; then
            http_ok=1; break
        fi
    done
    if systemctl is-active --quiet mudprinter && [[ $http_ok -eq 1 ]]; then
        say "Rollback OK: previous controller restored"
        info "Failed tree preserved at: $failed"
        info "Log: $LOG"
        exit 4
    else
        warn "ROLLBACK FAILED — service not active or HTTP unreachable."
        warn "Defensive backup still at $HOME_DIR/mudprinter-controller.backup-*."
        warn "Manual recovery required. Log: $LOG"
        exit 5
    fi
}

say "M4 atomic swap — $(date)"
info "log: $LOG"

# ---------------------------------------------------------------------------
# Pre-swap guard
# ---------------------------------------------------------------------------
say "Pre-swap guard"
[[ -d "$LIVE" ]] || { warn "LIVE $LIVE does not exist. Aborting."; exit 2; }
[[ -d "$NEW"  ]] || { warn "NEW $NEW does not exist. Aborting (run M1-M3 first)."; exit 2; }
[[ -x "$NEW/pi-interface/.venv/bin/uvicorn" ]] || {
    warn "NEW venv missing uvicorn. Aborting."; exit 2; }
[[ -f "$NEW/pi-interface/.env" ]] || {
    warn "NEW .env missing (M2 did not run). Aborting."; exit 2; }
[[ -f "$NEW_PREFLIGHT" ]] || {
    warn "preflight module missing at $NEW_PREFLIGHT. Aborting."; exit 2; }

STATE_RAW="$(curl -fsS -m 5 http://localhost:8000/api/state 2>/dev/null)"
if [[ -z "$STATE_RAW" ]]; then
    warn "/api/state unreachable — state unverifiable. Aborting."; exit 2
fi
info "/api/state responded (${#STATE_RAW} chars)."

printf '%s' "$STATE_RAW" | python3 "$NEW_PREFLIGHT" 2>&1 | tee -a "$LOG"
if [[ ${PIPESTATUS[1]} -ne 0 ]]; then
    warn "Controller not deployable (preflight refused)."
    exit 2
fi

OLD_PID=$(systemctl show mudprinter --property=MainPID --value)
info "Running mudprinter PID (before swap): $OLD_PID"

# ---------------------------------------------------------------------------
# Swap
# ---------------------------------------------------------------------------
say "Stopping mudprinter.service"
systemctl stop mudprinter
sleep 1
info "systemctl stop returned."

say "Rename: LIVE -> BAK, NEW -> LIVE"
mv "$LIVE" "$BAK"
mv "$NEW"  "$LIVE"
info "Previous controller preserved at: $BAK"

# ---------------------------------------------------------------------------
# Venv shebang rewrite (M3 baked /mudprinter-controller.NEW into wrapper
# scripts; after mv that path no longer exists → systemd 203/EXEC).
# Idempotent: if no stale references, nothing changes.
# ---------------------------------------------------------------------------
say "Rewrite venv shebangs: $STAGING -> $LIVE"
VENVDIR="$LIVE/pi-interface/.venv"
STALE_COUNT=$(grep -rlI --include='*' "$STAGING" "$VENVDIR" 2>/dev/null | wc -l)
info "venv files with stale staging path: $STALE_COUNT"
if [[ $STALE_COUNT -gt 0 ]]; then
    grep -rlI --include='*' "$STAGING" "$VENVDIR" 2>/dev/null \
        | xargs -r sed -i "s|${STAGING}|${LIVE}|g"
    REMAIN=$(grep -rlI --include='*' "$STAGING" "$VENVDIR" 2>/dev/null | wc -l)
    info "after rewrite: $REMAIN (expect 0)"
    [[ $REMAIN -eq 0 ]] || { rollback "venv shebang rewrite left $REMAIN stale references"; }
fi

# Pre-start sanity: uvicorn must be launchable before we tell systemd to try.
say "Pre-start sanity: can uvicorn actually run from the live path?"
if ! "$LIVE/pi-interface/.venv/bin/uvicorn" --version 2>&1 | tee -a "$LOG"; then
    rollback "uvicorn not launchable from live path"
fi

# ---------------------------------------------------------------------------
# Start + verify
# ---------------------------------------------------------------------------
say "Starting mudprinter.service"
systemctl start mudprinter

say "Verification"
HTTP_OK=0
for i in $(seq 1 30); do
    sleep 1
    if curl -fsS -m 2 http://localhost:8000/api/state >/dev/null 2>&1; then
        HTTP_OK=1
        info "HTTP /api/state responded after ${i}s."
        break
    fi
done

ok=1
systemctl is-active --quiet mudprinter || { warn "mudprinter NOT active."; ok=0; }
[[ $HTTP_OK -eq 1 ]] || { warn "HTTP /api/state did not respond within 30s."; ok=0; }

if [[ $ok -eq 1 ]]; then
    if curl -fsS -m 5 http://localhost:8000/ -o /tmp/m4_index.html 2>/dev/null && \
       grep -q 'id="jog-speed-in-s"' /tmp/m4_index.html; then
        info "New jog-speed UI element present in /."
    else
        warn "New jog-speed UI element NOT present — wrong tree is serving?"
        ok=0
    fi
fi

if [[ $ok -eq 1 ]]; then
    STATE2="$(curl -fsS -m 5 http://localhost:8000/api/state 2>/dev/null)"
    if [[ -z "$STATE2" ]]; then
        warn "Post-swap /api/state empty."
        ok=0
    else
        info "Post-swap state:"
        python3 - "$STATE2" <<'PY' 2>&1 | tee -a "$LOG"
import json, sys
d = json.loads(sys.argv[1])
print(f"     grbl_state  = {d.get('grbl_state')!r}")
print(f"     connected   = {d.get('connected')}")
print(f"     job         = {d.get('job')!r}")
hw = d.get('hardware') or {}
print(f"     hardware    = backend={hw.get('backend')!r} gpio={hw.get('gpio')}")
rs = d.get('remote_stop') or {}
print(f"     remote_stop = latched={rs.get('latched')}")
PY
        printf '%s' "$STATE2" | python3 "$LIVE_PREFLIGHT" 2>&1 | tee -a "$LOG"
        if [[ ${PIPESTATUS[1]} -ne 0 ]]; then
            warn "Post-swap state is Run/Jog/Hold or similar — unexpected after a cold restart."
            ok=0
        fi
    fi
fi

if [[ $ok -eq 1 ]]; then
    grep -q '^JOG_SPEED_MM_MIN=0' "$LIVE/pi-interface/.env" || { warn "JOG_SPEED_MM_MIN=0 missing"; ok=0; }
    grep -q '^Z_PWM_DUTY=0'       "$LIVE/pi-interface/.env" || { warn "Z_PWM_DUTY=0 missing"; ok=0; }
fi

if [[ $ok -eq 1 ]]; then
    say "SUCCESS: M4 complete"
    info "Previous controller preserved at: $BAK"
    info "Defensive backup still at: $HOME_DIR/mudprinter-controller.backup-*"
    info "Log: $LOG"
    exit 0
fi

rollback "post-start verification failed"

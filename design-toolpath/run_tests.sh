#!/usr/bin/env bash
# Design + Toolpath TEST TIERS (mud_test_tiers.py). From anywhere:
#   ./run_tests.sh fast         inner loop: unit + cheap focused geometry (no golden, no node / Chrome)
#   ./run_tests.sh integration  the tests measured slow (multi-layer / real-Designer stacks)
#   ./run_tests.sh golden       the strict golden gate (GOLDEN_STRICT=1, hashes unchanged)
#   ./run_tests.sh browser      UI smoke tests under node / headless Chrome
#   ./run_tests.sh full         everything (both suites unfiltered + toolpath_proto)
#   ./run_tests.sh check        prove the four tiers partition FULL exactly (collection only)
#   ./run_tests.sh retime       re-measure every test, rewrite mud_test_tiers_slow.txt
# Extra arguments go to pytest (e.g. ./run_tests.sh fast -x -k wall).
# The two suites (design_proto/tests, layer_assembly/tests) run in parallel;
# each one's output is in $LOGS/<suite>.txt. The pi-interface is a separate
# application with its own suite (cd ../pi-interface && .venv/bin/python -m pytest -q).
set -u
cd "$(dirname "$0")"
PY=design_proto/.venv/bin/python
TIER="${1:-}"; shift || true
LOGS="${TMPDIR:-/tmp}/mud-test-logs"; mkdir -p "$LOGS"
SUITES="design_proto/tests layer_assembly/tests"

run_suites() {      # $1 = marker expression ('' = none); rest: pytest args
  local expr="$1"; shift
  local pids=() names=() rc=0 start=$SECONDS
  for s in $SUITES; do
    local name; name=$(dirname "$s")
    if [ -n "$expr" ]; then
      "$PY" -m pytest "$s" -q -m "$expr" "$@" > "$LOGS/$name.txt" 2>&1 &
    else
      "$PY" -m pytest "$s" -q "$@" > "$LOGS/$name.txt" 2>&1 &
    fi
    pids+=($!); names+=("$name")
  done
  for i in "${!pids[@]}"; do
    wait "${pids[$i]}"; local r=$?
    # (exit 5 = no test selected in that suite for this tier — not a failure)
    [ $r -ne 0 ] && [ $r -ne 5 ] && rc=1
    printf '%-16s %s\n' "${names[$i]}" "$(tail -1 "$LOGS/${names[$i]}.txt")"
  done
  echo "tier ${TIER}: $((SECONDS - start)) s wall (logs: $LOGS)"
  return $rc
}

case "$TIER" in
  fast|integration|browser)
    run_suites "$TIER" "$@" ;;
  golden)
    GOLDEN_STRICT=1 run_suites golden "$@" ;;
  full)
    rc=0
    GOLDEN_STRICT=1 run_suites '' "$@" || rc=1
    (cd toolpath_proto && .venv/bin/python -m pytest -q 2>&1 | tail -1) || rc=1
    exit $rc ;;
  check)
    "$PY" - <<'EOF'
import re, subprocess, sys
py = 'design_proto/.venv/bin/python'
bad = False
for suite in ('design_proto/tests', 'layer_assembly/tests'):
    def count(expr):
        args = [py, '-m', 'pytest', suite, '--collect-only', '-q'] + (['-m', expr] if expr else [])
        out = subprocess.run(args, capture_output=True, text=True).stdout
        m = re.search(r'(\d+)(?:/(\d+))? tests? (?:collected|selected)', out)
        if not m:
            m = re.search(r'(\d+) tests? collected', out)
        return int(m.group(1)) if m else 0
    total = count('')
    parts = {t: count(t) for t in ('fast', 'integration', 'golden', 'browser')}
    none = count('not fast and not integration and not golden and not browser')
    multi = count('(fast and (integration or golden or browser)) or (integration and (golden or browser)) or (golden and browser)')
    ok = sum(parts.values()) == total and none == 0 and multi == 0
    bad |= not ok
    print(f'{suite:22s} total {total:5d} = ' + ' + '.join(f'{t} {n}' for t, n in parts.items()) +
          f'   unclassified {none}, in two tiers {multi}   {"OK" if ok else "MISMATCH"}')
sys.exit(1 if bad else 0)
EOF
    ;;
  retime)
    REC="$LOGS/durations.tsv"; rm -f "$REC"
    MUD_TEST_RECORD="$PWD/$REC" run_suites '' "$@"
    [ -f "$REC" ] || REC="$LOGS/durations.tsv"
    MUD_TEST_RECORD="" "$PY" - "$REC" <<'EOF'
import sys
import mud_test_tiers as T
rows = [ln.rstrip('\n').split('\t', 1) for ln in open(sys.argv[1], encoding='utf-8') if '\t' in ln]
slow = sorted({k for t, k in rows if float(t) >= T.SLOW_SECONDS and not k.startswith(tuple(T.GOLDEN_FILES))})
hdr = [f'# INTEGRATION tier: tests measured >= {T.SLOW_SECONDS} s (setup + call + teardown) on the development',
       '# machine. Generated — regenerate with ./run_tests.sh retime (mud_test_tiers.py).',
       '# A test missing here simply runs in FAST.']
open(T.SLOW_MANIFEST, 'w', encoding='utf-8').write('\n'.join(hdr + slow) + '\n')
print(f'{len(slow)} slow tests written to {T.SLOW_MANIFEST}')
EOF
    ;;
  *)
    sed -n '2,13p' "$0"; exit 2 ;;
esac

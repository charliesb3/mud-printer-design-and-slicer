# Pi Migration — Non-Destructive Consolidation with Rollback

Purpose: move the Raspberry Pi off the non-git `~/mudprinter-controller`
tree onto a Git-tracked copy of the consolidated branch, **without any
forced checkout over the running controller** and **with a tested rollback
path at every step**.

Nothing in this document should be run on the Pi until the operator has
reviewed the plan and the systemd service is in a known-safe state
(controller `Idle`, no job, no jog, remote released).

The current, authoritative, running controller is:

```
systemd unit : mudprinter.service
WorkingDir   : /home/dirtworks/mudprinter-controller/pi-interface
venv         : /home/dirtworks/mudprinter-controller/pi-interface/.venv
.env         : /home/dirtworks/mudprinter-controller/pi-interface/.env
```

This path is **preserved by every step below.** systemd `ExecStart` is
never edited.

---

## What the migration achieves

- Puts the running controller under Git tracking (so `git pull` based
  one-click deploys become possible).
- Brings in the one-click Mac → Pi deploy launcher (`scripts/deploy_pi.sh`,
  `Deploy to Pi.command`, `preflight_state.py`).
- Brings in the first-motion jog-speed controls, in/s print-speed display,
  the `.env.firstmotion` template, and `arduino-grbl/steps-per-mm-calculation.md`.
- Preserves everything from `restart/oct16-base`: GPIO, Z, RF remote,
  REMOTE STOP, systemd `Type=notify` + `WatchdogSec`, `pinctrl` Z LOW
  pre/post, udev rule, kiosk, machine profile, lift workflow — unchanged.
- Preserves the Pi's live `.env`, `machine_profile.json`, `.venv/`, and
  any `grbl-capture-*.txt` logs.

---

## Preconditions on the Pi

- Controller is `Idle`, no running or held job, no jog or remote request
  active.
- `grbl-capture-*.txt`, `.env`, `machine_profile.json`, and `jobs/` are
  present where the running service expects them.
- At least ~500 MB free on the home partition for a full `.NEW` copy
  (parallel checkout). Verify with `df -h ~`.
- `ssh dirtworks@192.168.1.144` works passwordless from the Mac.

---

## Migration — Phase M1: parallel checkout, no service touch

Clone the consolidated branch into a NEW directory ALONGSIDE the running
tree. Nothing the service reads is modified in this phase.

```
# On the Pi (via SSH; non-destructive).
cd ~
git clone https://github.com/charliesb3/mud-printer-design-and-slicer.git \
    mudprinter-controller.NEW
cd mudprinter-controller.NEW
git checkout consolidate/trunk     # whatever the final merged / renamed branch is
```

At this point the Pi has:

```
~/mudprinter-controller         ← LIVE service runs from here (unchanged)
~/mudprinter-controller.NEW     ← git-tracked, consolidated branch
~/mudprintersoftware            ← legacy shadow with 25 dirty design-toolpath/ lines
```

No service action yet. Verify the new tree:

```
cd ~/mudprinter-controller.NEW/pi-interface
git log -1 --oneline
git status --short         # should be clean
```

**Rollback for M1:** simply `rm -rf ~/mudprinter-controller.NEW`. The live
service is untouched.

---

## Migration — Phase M2: bring runtime data into the new tree

Copy the Pi's live `.env`, the local `machine_profile.json`, and any
GRBL capture logs into the new tree. These files are gitignored and will
NOT appear as repo changes.

```
cp ~/mudprinter-controller/pi-interface/.env \
   ~/mudprinter-controller.NEW/pi-interface/.env
# Only if present:
[ -f ~/mudprinter-controller/pi-interface/machine_profile.json ] && \
  cp ~/mudprinter-controller/pi-interface/machine_profile.json \
     ~/mudprinter-controller.NEW/pi-interface/machine_profile.json
# Any GRBL capture logs the operator may want to keep:
cp ~/mudprinter-controller/pi-interface/grbl-capture-*.txt \
   ~/mudprinter-controller.NEW/pi-interface/ 2>/dev/null || true
```

Confirm (gitignored files should NOT show up):

```
cd ~/mudprinter-controller.NEW
git status --short         # still clean (no tracked files altered)
test -f pi-interface/.env && echo env OK
```

**Rollback for M2:** nothing to roll back — the new tree is still parallel
and inert.

---

## Migration — Phase M3: a dedicated venv for the new tree

> **Known failure mode: stale venv shebangs after M4 rename.**
> Python `venv` wrapper scripts (`uvicorn`, `fastapi`, `pytest`, `pip`, …)
> have an **absolute path to the Python interpreter baked into their
> shebang**. If the venv was created or copied at the staging path
> `~/mudprinter-controller.NEW/...`, every wrapper's shebang will contain
> `/mudprinter-controller.NEW/.venv/bin/python3` — a path that **ceases
> to exist the moment M4 renames the directory**. systemd then fails to
> exec uvicorn with status `203/EXEC` and the misleading message
> "No such file or directory" (referring to the shebang's interpreter,
> not the wrapper script).
>
> **Required mitigation:** M4 must rewrite the venv's baked paths from
> the staging path to the final live path immediately after the rename
> and before `systemctl start`. The committed migration script
> `pi-interface/scripts/consolidation_migrate.sh` does this with an
> idempotent `grep + sed` pass and verifies `uvicorn --version` runs
> before releasing the service.
>
> The venv's `python` symlink itself is fine — it resolves through to
> `/usr/bin/python3`, which is path-independent. Only the pip-installed
> console-script wrappers break.

Two acceptable ways to produce the `.NEW` venv — pick ONE:

**Option A — duplicate the running venv (recommended if you want
byte-identical dependencies and no PyPI dependency).** `cp -a` the live
venv, then rewrite the baked paths from the OLD live path to the
staging path (`.NEW`). Smoke tests then run under the new tree's venv
cleanly. M4 will later rewrite from `.NEW` to the final live path.

```
cp -a ~/mudprinter-controller/pi-interface/.venv \
      ~/mudprinter-controller.NEW/pi-interface/.venv

OLD=/home/dirtworks/mudprinter-controller
STAGING=/home/dirtworks/mudprinter-controller.NEW
grep -rlI --include='*' "$OLD" \
    ~/mudprinter-controller.NEW/pi-interface/.venv 2>/dev/null | \
    xargs -r sed -i "s|${OLD}|${STAGING}|g"
```

**Option B — fresh venv + pip install from PyPI.**

```
python3 -m venv --system-site-packages \
    ~/mudprinter-controller.NEW/pi-interface/.venv

~/mudprinter-controller.NEW/pi-interface/.venv/bin/pip install \
    -q -r ~/mudprinter-controller.NEW/pi-interface/requirements.txt
```

Either option **ends up with venv scripts pointing at the staging path
`.NEW`.** This is correct for M3 (so smoke tests work) but requires the
M4 rewrite step to be correct for the live service.

**Smoke test the new venv OUT OF BAND, with the service STILL running
from the old tree:**

```
# Collect tests only (does NOT start the server).
cd ~/mudprinter-controller.NEW/pi-interface
.venv/bin/python -m pytest --collect-only -q 2>&1 | tail -5

# Import the app module (does NOT bind the port, does NOT touch GPIO).
.venv/bin/python -c "from app.main import app; print('import OK')"

# Verify uvicorn's shebang resolves (sanity check for M4):
head -1 .venv/bin/uvicorn               # should point to .NEW/python3
.venv/bin/uvicorn --version             # should print the version
```

The running service continues to serve the controller UI the entire time.

**Rollback for M3:** `rm -rf ~/mudprinter-controller.NEW/.venv`.

---

## Migration — Phase M4: atomic directory swap

This is the only step that briefly stops the service. Prefer the
committed migration script, which encodes the correct order including
the venv shebang rewrite and automatic rollback:

```
# One command, runs the whole M4 flow with auto-rollback.
sudo bash <path-to-repo>/pi-interface/scripts/consolidation_migrate.sh
```

The script does, in this exact order:

1. **Preflight guard** — refuses to proceed if `/api/state` is not
   reachable, GRBL is `Run`/`Jog`/`Hold*`, the job is `running`/`held`,
   or REMOTE STOP is latched. Uses the same unit-tested
   `preflight_state.py` module as the one-click deploy launcher.
2. **`systemctl stop mudprinter`**.
3. **Atomic rename** — `mv ~/mudprinter-controller ~/mudprinter-controller.BAK-<ts>`
   then `mv ~/mudprinter-controller.NEW ~/mudprinter-controller`. Both
   `mv`s are atomic on the same filesystem.
4. **Rewrite venv shebangs** — `grep + sed` from the staging path
   (`.NEW`) to the final live path across every wrapper script. See
   the warning in Phase M3 for why this is required. The step is
   idempotent and verified (reports count of stale references before
   and after).
5. **Pre-start sanity** — `uvicorn --version` must succeed from the
   live path BEFORE systemd starts the service. If this fails, the
   script rolls back **before** systemd gets a chance to flap in a
   203/EXEC restart loop.
6. **`systemctl start mudprinter`**.
7. **Verification** — up to 30 s wait for `is-active` + HTTP
   `/api/state` to respond. New jog-speed UI element present in `/`.
   Post-start state is `Idle` / `Disconnected` / `Alarm` (not
   `Run` / `Jog` / `Hold`). `.env` still contains
   `JOG_SPEED_MM_MIN=0` and `Z_PWM_DUTY=0`.
8. **Auto-rollback** — on any failure, `systemctl stop`, rename the
   failed tree to `.FAILED-<ts>`, restore from `.BAK-<ts>`,
   `systemctl start`, verify the restored service.

**Equivalent manual sequence** (for reference — error-prone, avoid if
possible):

```
sudo systemctl stop mudprinter

mv ~/mudprinter-controller      ~/mudprinter-controller.BAK
mv ~/mudprinter-controller.NEW  ~/mudprinter-controller

# REQUIRED — rewrite the venv's baked staging path to the live path.
grep -rlI --include='*' /home/dirtworks/mudprinter-controller.NEW \
    ~/mudprinter-controller/pi-interface/.venv 2>/dev/null | \
    xargs -r sed -i "s|/home/dirtworks/mudprinter-controller.NEW|/home/dirtworks/mudprinter-controller|g"

# REQUIRED sanity — must succeed before systemctl start.
~/mudprinter-controller/pi-interface/.venv/bin/uvicorn --version

sudo systemctl start mudprinter
sudo systemctl is-active mudprinter
curl -fsS http://localhost:8000/api/state | python3 -m json.tool | head -20
```

Expected successful state:
- `is-active` → `active`
- `/api/state` returns a `grbl_state` field (likely `Idle` or
  `Disconnected`), `hardware.backend == "gpio"`, no exceptions in logs.

**Checklist before declaring M4 complete:**

- [ ] `sudo systemctl is-active mudprinter` → `active`
- [ ] `curl -fsS http://localhost:8000/api/state >/dev/null` returns 0
- [ ] UI loads at `http://192.168.1.144:8000`
- [ ] REMOTE STOP works (press CH1 on the handheld, UI shows latched,
      software CLEAR behaves correctly)
- [ ] Z UP / Z DOWN buttons behave as before (dead-man releases on
      button release; UP+DOWN = STOP)
- [ ] GRBL serial reconnect works (replug USB, UI reconnects)
- [ ] No ERROR entries in `sudo journalctl -u mudprinter -n 200`

**Rollback for M4** (if ANY of the above fails — the migration script
does this automatically):

```
sudo systemctl stop mudprinter
mv ~/mudprinter-controller      ~/mudprinter-controller.FAILED
mv ~/mudprinter-controller.BAK  ~/mudprinter-controller
sudo systemctl start mudprinter
sudo systemctl is-active mudprinter   # must be 'active' again
```

The rollback is also atomic; the service is back on the known-working
tree within seconds, with the failed `.FAILED` tree preserved for
inspection.

**Note:** the rollback above does NOT need to re-run the venv shebang
rewrite — the `.BAK` tree's shebangs still point at
`/mudprinter-controller/.venv/python3`, which is the restored live
path.

**Reusing a `.FAILED-<ts>` tree for another attempt.** The failed tree
is a byte-complete copy of what was going to be `.NEW`, with the only
material difference being venv shebangs that point at the live path
(from an in-place diagnostic rewrite) or at `.NEW` (if nothing was
touched after the rollback). To reuse:

```
# Rename back to the staging path.
mv ~/mudprinter-controller.FAILED-<ts> ~/mudprinter-controller.NEW

# Normalize the venv's baked paths to the staging path, whatever they
# were before (idempotent).
for stale in /home/dirtworks/mudprinter-controller \
             /home/dirtworks/mudprinter-controller.FAILED-<ts>; do
    grep -rlI --include='*' "$stale" \
        ~/mudprinter-controller.NEW/pi-interface/.venv 2>/dev/null | \
        xargs -r sed -i "s|${stale}|/home/dirtworks/mudprinter-controller.NEW|g"
done

# Smoke test.
~/mudprinter-controller.NEW/pi-interface/.venv/bin/uvicorn --version
```

Then re-run M4 (the script).

Keep `~/mudprinter-controller.BAK` for at least one successful
deploy cycle (several days of normal use). Remove it only when you are
sure the new tree is stable:

```
# Only after at least one successful deploy + a full print day.
rm -rf ~/mudprinter-controller.BAK
```

---

## Migration — Phase M5 (optional): clean up the shadow directory

The legacy `~/mudprintersoftware` directory with its 25 dirty
`design-toolpath/` lines is unrelated to the controller. Options:

- **Preferred — leave it alone.** It costs disk but is risk-free.
- **If you want it tidy** — commit/stash/discard the dirty work
  **yourself** (not something this plan should automate), then
  `mv ~/mudprintersoftware ~/mudprintersoftware.BAK`. Don't `rm -rf` until
  you've verified nothing in `~/mudprintersoftware/design-toolpath/`
  needs preserving.

---

## After M4: the one-click launcher now works

Edit the Mac-side `pi-interface/scripts/deploy.config` to point at the
consolidated tree:

```
PI_HOST="192.168.1.144"
PI_USER="dirtworks"
REPO_DIR="/home/dirtworks/mudprinter-controller"
```

(`REPO_DIR` is honoured by `deploy_pi.sh`; everything else — the
preflight, `git pull --ff-only`, `pip install` when
`requirements.txt` changed, `sudo systemctl restart mudprinter`,
post-restart `is-active` + `/api/state` verification — runs unchanged.)

Then `Deploy to Pi.command` from Finder brings in future commits
exactly as designed, with the same safety preflight you've already
tested.

---

## Risks + mitigations summary

| Risk | Mitigation |
|---|---|
| **Venv wrapper shebangs reference staging path after M4 rename → systemd 203/EXEC on start** | **Observed and fixed 2026-10-23.** M4 script now rewrites the baked staging path to the live path and verifies `uvicorn --version` BEFORE `systemctl start`. If that fails, the script rolls back before systemd gets to attempt a restart. See warning in Phase M3. |
| Service won't start from new tree | Pre-start `uvicorn --version` sanity check in M4; immediate auto-rollback if it fails. M3 import test runs first — fails fast long before M4. |
| `.env` not copied → GRBL port wrong | M2 explicit copy; `git status` after M2 proves it's gitignored and present. |
| `machine_profile.json` lost → envelope / preflight gaps | M2 copies it if present; if absent, the controller starts with defaults ("Unconfigured printer") and the operator re-enters settings in the UI. No motion is lost; preflight gets less strict. |
| GRBL capture logs lost | M2 copies `grbl-capture-*.txt`. Also kept in `~/mudprinter-controller.BAK` after M4. |
| Dependencies drift (lgpio / gpiozero version) | `cp -a` the running venv (Option A) gives byte-identical deps. If using fresh venv (Option B), `--system-site-packages` + `requirements.txt` matches what the hardware integration pass validated. |
| systemd ExecStart suddenly broken | Not changed. The migration is a directory swap at the exact path `ExecStart` already references. |
| Service restart during motion | Preflight (`preflight_state.py`, 18 pytest cases) refuses to proceed on anything other than Idle/Disconnected/Alarm. |
| Z PWM default-STOP missed during swap | `ExecStopPost=+/usr/bin/pinctrl set 18 op dl` (and GPIO23) runs on every service stop, including the M4 `systemctl stop` and any rollback. |

---

## What this migration does NOT do

- Does **not** modify GRBL firmware or `$` settings on the Arduino.
- Does **not** restart the controller except for the M4 atomic swap (and
  rollback, if needed).
- Does **not** initiate physical motion.
- Does **not** modify or discard the 25 dirty lines in
  `~/mudprintersoftware/design-toolpath/`.
- Does **not** change the systemd unit file.
- Does **not** change the udev rule, the kiosk script, or any other
  deploy artifact already installed on the Pi.

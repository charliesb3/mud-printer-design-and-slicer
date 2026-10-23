# One-Click Deploy — Mac → Pi

Simple, safe update path for the Raspberry Pi controller. Double-click a
file on the Mac, the Pi pulls the latest controller code from GitHub,
reinstalls dependencies if they changed, and restarts the systemd service.
Pre- and post-flight checks make sure a running job or jog is never
interrupted and that the service is actually healthy after the restart.

What this does NOT do: push code to GitHub, modify `.env`, touch GRBL
firmware or GRBL settings, deploy automatically on every git push.

What this DOES require: a one-time setup described below.

---

## Files

```
pi-interface/scripts/
├── Deploy to Pi.command      # Double-click this from Finder on the Mac
├── deploy.config.sample      # Template — copy to deploy.config
├── deploy.config             # Local config (gitignored, see below)
└── deploy_pi.sh              # Runs on the Pi, does the real work
```

The `.command` extension is what makes macOS let you launch the script by
double-clicking in Finder.

---

## Preflight — what the Pi script checks before touching anything

Before a single file is pulled or any service is restarted, `deploy_pi.sh`
reads `http://localhost:8000/api/state` and refuses to proceed if any of
these are true:

- `grbl_state` is `Run` or `Jog` (motion in progress).
- `grbl_state` starts with `Hold` (a job is paused and would be lost).
- `job.state` is `running` or `held`.
- The HTTP endpoint cannot be reached — the controller state is unknown,
  so a restart would be reckless.

The script also refuses to pull if the repo on the Pi has uncommitted
changes, is in detached-HEAD state, or cannot fast-forward.

---

## Postflight — what the Pi script checks after restarting

After `sudo systemctl restart mudprinter` the script waits up to 15 s for
BOTH of:

- `systemctl is-active mudprinter` reporting `active`
- A successful `GET /api/state`

If either fails, the launcher reports a clear "verification failed"
message with the command to check the service log.

---

## One-time setup

### 1. On the Mac — tell the launcher where the Pi is

```
cd ~/<somewhere>/mudprintersoftware/pi-interface/scripts
cp deploy.config.sample deploy.config
```

Open `deploy.config` in any text editor. Set:

```
PI_HOST="mudprinter.local"   # or the Pi's IP, e.g. 192.168.1.42
PI_USER="pi"                 # whichever user owns ~/mudprintersoftware
```

`deploy.config` is gitignored. Each operator's Mac has its own.

### 2. Set up passwordless SSH from the Mac to the Pi

On the Mac, generate a key if you don't already have one:

```
ssh-keygen -t ed25519    # press Enter through the prompts
```

Copy the key to the Pi (one-time password prompt):

```
ssh-copy-id pi@mudprinter.local
```

Confirm it works without a password:

```
ssh pi@mudprinter.local 'echo ok'
```

### 3. (Optional) Passwordless sudo for the systemd restart

Without this, the launcher will prompt once for the Pi's sudo password
during each deploy — fine for low-friction interactive use. If you want a
true single-click experience, add a narrow sudoers rule on the Pi:

```
sudo visudo -f /etc/sudoers.d/mudprinter-deploy
```

Add exactly:

```
pi ALL=(root) NOPASSWD: /bin/systemctl restart mudprinter, /bin/systemctl is-active mudprinter
```

Replace `pi` with the Pi user if different. This grants NOPASSWD for ONLY
restarting / status-checking the `mudprinter` service — nothing else.

### 4. Make the launcher double-clickable in Finder

Already executable (`chmod +x`) in git. If macOS Gatekeeper quarantines
the file on first download, strip the quarantine attribute:

```
xattr -d com.apple.quarantine "pi-interface/scripts/Deploy to Pi.command"
```

Then in Finder you should see "Deploy to Pi.command". A double-click
opens Terminal, runs the script, prints the result, and waits for Enter
before closing.

---

## Everyday use

Double-click **Deploy to Pi.command**. You will see:

```
Mud Printer — one-click deploy
===============================

Target : pi@mudprinter.local
Script : $HOME/mudprintersoftware/pi-interface/scripts/deploy_pi.sh

Connecting (sudo may prompt for the Pi's password) ...

==> Checking current controller state at http://localhost:8000/api/state ...
Preflight OK: GRBL=Idle  job=none

==> Updating repo at /home/pi/mudprintersoftware ...
==> Updated main: a1b2c3d → e4f5g6h

==> requirements.txt unchanged — skipping pip install.

==> Restarting systemd service: mudprinter ...

==> Waiting for mudprinter to come back up ...

==> DEPLOY OK — mudprinter active, HTTP responsive, head at e4f5g6h

DEPLOY SUCCEEDED

Press Enter to close this window...
```

If the controller is busy, you'll instead see something like:

```
==> Checking current controller state at http://localhost:8000/api/state ...
Controller not in a deployable state:
  - GRBL is 'Run' — motion in progress

DEPLOY REFUSED — controller was not in a safe state.
The Pi did not restart. Nothing was changed.
```

Nothing was changed. Wait for the job to finish (or stop it deliberately
from the UI), then re-run.

---

## Exit codes (shown in the Mac window)

| Code | Meaning |
|---:|---|
| 0  | Deploy succeeded. |
| 2  | Refused — controller not in a safe state (running / jogging / held / unreachable). Nothing changed. |
| 3  | Refused — repo on the Pi needs attention (uncommitted changes, detached HEAD, non-fast-forward pull). Nothing changed. |
| 4  | Deploy ran, but post-restart verification failed. Check the service manually. |
| 255 | SSH connection failed (hostname / network / keys). |

---

## What is preserved vs. updated

**Preserved on the Pi (never touched by this workflow):**
- `pi-interface/.env` — gitignored, each Pi has its own.
- `pi-interface/jobs/` — uploaded G-code files, gitignored.
- GRBL firmware / `$$` settings on the Arduino.
- The `.venv/` (dependencies are reinstalled into it in place, not recreated).

**Updated:**
- Any git-tracked file in `pi-interface/` or any other subproject at the
  current branch's `HEAD`.
- Python dependencies — only if `requirements.txt` actually changed since
  the last successful deploy (tracked by a sha256 sentinel inside the
  venv).
- The running `mudprinter` systemd service — restarted at the end.

---

## Branch selection

`deploy_pi.sh` runs `git pull --ff-only` on whatever branch the Pi has
checked out. If you want the Pi to track a feature branch during a
development cycle, check it out on the Pi once:

```
ssh pi@mudprinter.local
cd ~/mudprintersoftware
git fetch
git checkout <branch>
```

Thereafter every one-click deploy pulls that branch. Switch back to
`main` when you're done:

```
ssh pi@mudprinter.local 'cd ~/mudprintersoftware && git checkout main'
```

---

## Why NOT auto-deploy on git push

Explicitly out of scope. The operator is physically next to a machine
that moves. A manual, explicit action with pre- and post-flight checks
is the right ergonomic. Auto-deploy on push would make it possible for
an unrelated edit from the Mac to interrupt a live job on the Pi.

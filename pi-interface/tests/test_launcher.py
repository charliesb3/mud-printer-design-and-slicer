r"""
Regression tests for the Mac → Pi one-click deploy launcher
(`Deploy to Pi.command` at the REPO ROOT — moved from
`pi-interface/scripts/` on 2026-10-25).

The bug these tests pin: on first use, the launcher's remote command
contained a literal `$HOME` (because of an escaped `\$HOME` default in
the bash heredoc) wrapped in SINGLE quotes passed to the Pi over ssh.
The single quotes prevented the Pi's shell from expanding `$HOME`, and
the path also pointed at `mudprintersoftware/` which never existed on
the Pi. Result: `bash: $HOME/mudprintersoftware/.../deploy_pi.sh:
No such file or directory` and exit 127.

These tests spawn a bash shim that pretends to be `ssh` and captures
the exact argument string the launcher would hand to `ssh`. Nothing
over the network, nothing on the Pi.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


# The launcher lives at the REPO ROOT alongside PROJECT_OVERVIEW.md.
# `tests/test_launcher.py` is 2 levels down (pi-interface/tests/), so the
# repo root is parents[2].
LAUNCHER = (
    Path(__file__).resolve().parents[2] / "Deploy to Pi.command"
)


def _write_ssh_shim(shim_dir: Path, log_path: Path) -> None:
    """Place an executable `ssh` on PATH that logs its argv to log_path and exits 0."""
    shim = shim_dir / "ssh"
    # argv[1..] of ssh is captured verbatim, one per line, with a sentinel.
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do printf "%s\\n" "$arg"; done > "' + str(log_path) + '"\n'
        'exit 0\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)


def _run_launcher(tmp_path: Path, config_body: str) -> tuple[int, list[str], str, str]:
    """
    Copy the launcher + a scratch deploy.config to tmp_path and run it with a
    shim ssh on PATH. Returns (returncode, ssh_argv, stdout, stderr).
    """
    staged_scripts = tmp_path / "scripts"
    staged_scripts.mkdir()
    shutil.copy(LAUNCHER, staged_scripts / "Deploy to Pi.command")
    (staged_scripts / "Deploy to Pi.command").chmod(0o755)
    (staged_scripts / "deploy.config").write_text(config_body)

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    ssh_log = tmp_path / "ssh_argv.log"
    _write_ssh_shim(shim_dir, ssh_log)

    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    # The launcher trails with `read -r -p "Press Enter..."`; EOF closes it.
    r = subprocess.run(
        [str(staged_scripts / "Deploy to Pi.command")],
        env=env,
        input="\n",
        capture_output=True,
        text=True,
        timeout=15,
    )

    argv = ssh_log.read_text().splitlines() if ssh_log.exists() else []
    return r.returncode, argv, r.stdout, r.stderr


class TestLauncherPathResolution:
    """
    These tests pin the fix for the first failed deploy attempt (2026-10-23,
    exit 127: `No such file or directory` on `$HOME/mudprintersoftware/...`).
    """

    def test_remote_command_contains_absolute_path_not_dollar_home(self, tmp_path):
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n'
            'REPO_DIR="/home/dirtworks/mudprinter-controller"\n',
        )
        assert rc == 0, f"launcher exited {rc}"
        assert argv, "ssh shim was never invoked"
        remote_cmd = argv[-1]  # last arg is the remote command string
        assert "/home/dirtworks/mudprinter-controller/pi-interface/scripts/deploy_pi.sh" in remote_cmd, (
            f"remote command missing absolute script path. Got: {remote_cmd!r}"
        )
        assert "$HOME" not in remote_cmd, (
            f"remote command still contains literal '$HOME'; it will not expand "
            f"inside the Pi's shell quoting. Got: {remote_cmd!r}"
        )
        # The old wrong directory name must not appear:
        assert "mudprintersoftware" not in remote_cmd, (
            f"remote command still refers to the old 'mudprintersoftware' path. "
            f"Got: {remote_cmd!r}"
        )

    def test_remote_script_is_not_wrapped_in_single_quotes(self, tmp_path):
        """
        Single quotes around a path containing a variable prevent remote
        expansion. The fix uses double quotes (or no quotes) with a
        fully-resolved absolute path.
        """
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n'
            'REPO_DIR="/home/dirtworks/mudprinter-controller"\n',
        )
        assert rc == 0
        remote_cmd = argv[-1]
        # The script path must appear WITHOUT single-quote wrapping that
        # would prevent variable expansion if any variables did remain.
        bad = "'/home/dirtworks/mudprinter-controller/pi-interface/scripts/deploy_pi.sh'"
        assert bad not in remote_cmd, (
            f"remote command wraps the path in single quotes, which blocks any "
            f"variable expansion. Got: {remote_cmd!r}"
        )

    def test_default_repo_dir_matches_consolidation_layout(self, tmp_path):
        """When REPO_DIR is unset, the launcher must default to
        /home/<PI_USER>/mudprinter-controller — the consolidation layout."""
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n',
            # NB: REPO_DIR intentionally unset
        )
        assert rc == 0
        remote_cmd = argv[-1]
        assert "/home/dirtworks/mudprinter-controller/pi-interface/scripts/deploy_pi.sh" in remote_cmd

    def test_repo_dir_is_passed_as_env_to_remote(self, tmp_path):
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n'
            'REPO_DIR="/home/dirtworks/mudprinter-controller"\n',
        )
        assert rc == 0
        remote_cmd = argv[-1]
        # REPO_DIR must appear as an env assignment prefix so deploy_pi.sh picks
        # it up without falling back to its own default.
        assert 'REPO_DIR="/home/dirtworks/mudprinter-controller"' in remote_cmd, (
            f"REPO_DIR not passed to the Pi as env. Got: {remote_cmd!r}"
        )

    def test_launcher_uses_tty_for_remote_sudo(self, tmp_path):
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n',
        )
        assert rc == 0
        # -t appears in ssh options.
        assert "-t" in argv, f"ssh invocation missing -t (needed for interactive sudo). argv={argv!r}"

    def test_launcher_reports_target_host_and_user(self, tmp_path):
        rc, _, stdout, _ = _run_launcher(
            tmp_path,
            'PI_HOST="192.168.1.144"\n'
            'PI_USER="dirtworks"\n'
            'REPO_DIR="/home/dirtworks/mudprinter-controller"\n',
        )
        assert rc == 0
        assert "dirtworks@192.168.1.144" in stdout


class TestLauncherConfigValidation:
    def test_missing_config_exits_nonzero(self, tmp_path):
        staged = tmp_path / "scripts"
        staged.mkdir()
        shutil.copy(LAUNCHER, staged / "Deploy to Pi.command")
        (staged / "Deploy to Pi.command").chmod(0o755)
        # Intentionally no deploy.config.
        shim_dir = tmp_path / "bin"; shim_dir.mkdir()
        _write_ssh_shim(shim_dir, tmp_path / "ssh_argv.log")
        env = os.environ.copy()
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        r = subprocess.run(
            [str(staged / "Deploy to Pi.command")],
            env=env, input="\n", capture_output=True, text=True, timeout=10,
        )
        assert r.returncode != 0
        assert "deploy.config" in r.stdout

    def test_missing_pi_host_exits_nonzero(self, tmp_path):
        rc, argv, _, _ = _run_launcher(
            tmp_path,
            'PI_USER="dirtworks"\n'
            'REPO_DIR="/home/dirtworks/mudprinter-controller"\n',
            # PI_HOST missing
        )
        assert rc != 0
        assert not argv, "ssh should not have been invoked without PI_HOST"

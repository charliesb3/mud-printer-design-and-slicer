"""
The Assembly workspace UI (static/assembly.js + the workspace switch in
static/app.js) driven under node against the REAL backend, started here on
a free port (no browser).
"""
import os
import shutil
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_assembly_workspace_ui():
    from werkzeug.serving import make_server
    from app import app
    srv = make_server('127.0.0.1', 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        script = os.path.join(os.path.dirname(__file__), 'js', 'ui_assembly_smoke.js')
        proc = subprocess.run(['node', script, f'http://127.0.0.1:{srv.server_port}'],
                              capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI ASSEMBLY SMOKE PASSED' in proc.stdout
    assert proc.stdout.count('ok  ') >= 20

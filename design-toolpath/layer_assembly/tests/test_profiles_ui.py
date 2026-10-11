"""
ASSEMBLY PROFILES in the Assembly workspace UI (static/assembly.js) driven
under node against the REAL backend (started here on a free port).
"""
import os
import shutil
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_profiles_ui_smoke():
    from werkzeug.serving import make_server
    from app import app
    srv = make_server('127.0.0.1', 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        script = os.path.join(os.path.dirname(__file__), 'js', 'ui_profiles_smoke.js')
        proc = subprocess.run(['node', script, f'http://127.0.0.1:{srv.server_port}'],
                              capture_output=True, text=True, timeout=240)
    finally:
        srv.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI PROFILES SMOKE PASSED' in proc.stdout

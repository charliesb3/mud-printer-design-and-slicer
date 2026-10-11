"""
NUMERIC FIELDS (static/numeric.js) in a real browser: headless Chrome
driven over the DevTools protocol (tests/js/browser_numeric_fields.mjs)
against the real app, started here on a free port. Skipped when node or
Chrome is not installed (CHROME env var overrides the binary).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

_CANDIDATES = [os.environ.get('CHROME') or '',
               '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
               shutil.which('google-chrome') or '', shutil.which('chromium') or '', shutil.which('chromium-browser') or '']
CHROME = next((c for c in _CANDIDATES if c and os.path.exists(c)), None)


@pytest.mark.skipif(shutil.which('node') is None or CHROME is None, reason='node or Chrome not installed')
def test_numeric_fields_in_a_real_browser():
    from werkzeug.serving import make_server
    from app import app
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(os.path.dirname(__file__), 'js', 'browser_numeric_fields.mjs')
            proc = subprocess.run(['node', script, CHROME, f'http://127.0.0.1:{srv.server_port}/', tmp],
                                  capture_output=True, text=True, timeout=180)
    finally:
        srv.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'BROWSER NUMERIC FIELDS PASSED' in proc.stdout

"""
TEST TIERS (Design + Toolpath test infrastructure, 2026-10-16) — every
collected test of design_proto/tests and layer_assembly/tests gets EXACTLY ONE
tier marker, so `-m <tier>` selects a tier and no selection at all is FULL:

    golden       the strict golden regression (layer_assembly/tests/test_golden.py)
    browser      tests that drive the UI under node / headless Chrome (their
                 skip mark names node or Chrome: `skipif(which('node') …)`)
    integration  tests MEASURED slow (≥ SLOW_SECONDS, setup + call + teardown):
                 listed in mud_test_tiers_slow.txt (regenerate: run_tests.sh retime)
    fast         everything else — the default, so a test the manifest does
                 not know (new, renamed) is never lost: at worst it makes FAST
                 slower until the next retime

Precedence: golden > browser > integration > fast. Loaded by the two suites'
conftest.py files (pytest_configure / pytest_collection_modifyitems). Nothing
here changes what a test does — only which tier it belongs to.
Commands: run_tests.sh {fast|integration|golden|browser|full|check|retime}.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))           # design-toolpath/
SLOW_MANIFEST = os.path.join(ROOT, 'mud_test_tiers_slow.txt')
SLOW_SECONDS = 1.0
TIERS = ('fast', 'integration', 'golden', 'browser')
GOLDEN_FILES = {'layer_assembly/tests/test_golden.py'}

_slow = None


def test_key(item) -> str:
    """'<path relative to design-toolpath>::<class::>name' — the manifest key."""
    rel = os.path.relpath(str(item.fspath), ROOT)
    rest = item.nodeid.split('::', 1)[1] if '::' in item.nodeid else item.name
    return f'{rel}::{rest}'


def slow_keys() -> set:
    global _slow
    if _slow is None:
        _slow = set()
        if os.path.exists(SLOW_MANIFEST):
            with open(SLOW_MANIFEST, encoding='utf-8') as f:
                _slow = {ln.rstrip('\n') for ln in f if ln.strip() and not ln.startswith('#')}
    return _slow


def _drives_ui(item) -> bool:
    for m in item.iter_markers('skipif'):
        reason = str(m.kwargs.get('reason', '')) + ' '.join(str(a) for a in m.args if isinstance(a, str))
        if 'node' in reason or 'Chrome' in reason or 'chrome' in reason:
            return True
    return False


def tier_of(item) -> str:
    if os.path.relpath(str(item.fspath), ROOT) in GOLDEN_FILES:
        return 'golden'
    if _drives_ui(item):
        return 'browser'
    if test_key(item) in slow_keys():
        return 'integration'
    return 'fast'


def configure(config):
    for t in TIERS:
        config.addinivalue_line('markers', f'{t}: Design + Toolpath test tier (mud_test_tiers.py)')


def modify_items(items):
    for item in items:
        item.add_marker(tier_of(item))


# ---- retime: record every test's duration (MUD_TEST_RECORD=<file>) ---------------
_times = {}


def record(report, item_key):
    path = os.environ.get('MUD_TEST_RECORD')
    if not path:
        return
    _times[item_key] = _times.get(item_key, 0.0) + float(report.duration or 0.0)
    if report.when == 'teardown':
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f'{_times.pop(item_key):.4f}\t{item_key}\n')

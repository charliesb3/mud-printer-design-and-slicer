"""design_proto tests: the TEST TIER hooks (../../mud_test_tiers.py) — nothing else."""
import os
import sys


# ---- TEST TIERS (../../mud_test_tiers.py): one tier marker per test -----------------
import pytest  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import mud_test_tiers as _tiers  # noqa: E402


def pytest_configure(config):
    _tiers.configure(config)


def pytest_collection_modifyitems(config, items):
    _tiers.modify_items(items)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    _tiers.record(out.get_result(), _tiers.test_key(item))

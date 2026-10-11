"""SUPERSEDED-RESOLVE CANCELLATION (2026-10-09).

The Designer numbers every resolve request of one page (`X-Resolve-Channel`:
a random id per page load, `X-Resolve-Seq`: increasing). When a newer
request of the same channel has begun, an older one still computing is
SUPERSEDED: it stops at its next checkpoint (`check()`), and the endpoint
answers {'superseded': True} (HTTP 409) instead of a result nobody will
draw. A late-arriving older request is refused at once.

Checkpoints are cheap (a dict lookup) and sit between stages (geometry,
routing) and inside the long loops (per wall-system run, per route
component). Requests without the headers (tests, the Assembly, scripts)
are never cancelled: `check()` is then a no-op.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_latest: dict = {}                 # channel → newest seq that has begun
_lock = threading.Lock()
_local = threading.local()


class Superseded(Exception):
    """A newer resolve of the same page has begun."""


def begin(channel, seq):
    """Register this thread's request; Superseded if a newer one has begun."""
    if not channel or seq is None:
        _local.token = None
        return
    with _lock:
        if seq < _latest.get(channel, -1):
            _local.token = None
            raise Superseded()
        _latest[channel] = seq
        if len(_latest) > 256:     # (page loads come and go)
            for k in list(_latest)[:128]:
                _latest.pop(k, None)
    _local.token = (channel, seq)


def end():
    _local.token = None


def check():
    """Raise Superseded when a newer request of this thread's page began."""
    t = getattr(_local, 'token', None)
    if t is not None and _latest.get(t[0], -1) > t[1]:
        raise Superseded()


@contextmanager
def request_scope(headers):
    """begin / end around one request, from its headers."""
    ch = headers.get('X-Resolve-Channel')
    try:
        seq = int(headers.get('X-Resolve-Seq')) if ch else None
    except (TypeError, ValueError):
        seq = None
    begin(ch, seq)
    try:
        yield
    finally:
        end()

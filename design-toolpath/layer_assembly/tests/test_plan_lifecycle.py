"""PRINT PLAN LIFECYCLE (reliability pass 2026-10-09): the Print Simulation's
plan is a BACKGROUND job that never blocks Assembly editing and never
leaves a stale result.

- `build_plan(cancelled=…)` aborts before any layer once cancelled;
- a background job (`job`) is abandoned (409 cancelled, nothing cached)
  when a newer job starts, when the page cancels it, or when an Assembly
  request needs the Designer adapter — which then answers without waiting
  for the whole plan;
- requests without `job` (Assembly → Print plan, tests) are never cancelled;
- `rebuild` plans again from scratch (same result);
- the Designer adapter is used by one request at a time.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.dirname(__file__))

from test_print_plan import _stream_req                    # noqa: E402


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture
def client():
    import app as A
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    W._PLANS.clear()
    W._JOBS.update(latest=None)
    return A.app.test_client()


def _slow_routes(monkeypatch, delay, on_route=None):
    """Every layer's route takes `delay` s (a big building in miniature)."""
    from layer_assembly.designer_source import DesignerSource
    real = DesignerSource.route
    n = [0]

    def route(self, g, start=None, **kw):
        n[0] += 1
        if on_route:
            on_route(n[0])
        time.sleep(delay)
        return real(self, g, start, **kw)
    monkeypatch.setattr(DesignerSource, 'route', route)
    return n


def test_build_plan_stops_when_cancelled():
    from layer_assembly.web import _resolve_stack
    from layer_assembly.print_plan import build_plan, PlanCancelled
    st = _resolve_stack(_stream_req())
    seen = []

    def cancelled():
        seen.append(1)
        return len(seen) > 2                       # after the first layer
    with pytest.raises(PlanCancelled):
        build_plan(st['res'], st['src'], cancelled=cancelled)
    assert len(seen) == 3
    assert len(build_plan(st['res'], st['src'], cancelled=lambda: False).layers) == len(st['res'].instances)


def test_a_newer_job_abandons_the_running_one(client, monkeypatch):
    from layer_assembly import web as W
    # while job A routes its 2nd layer, job B starts (the page asked again)
    _slow_routes(monkeypatch, 0, on_route=lambda n: n == 2 and W._JOBS.update(latest='B'))
    r = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 'A'}))
    assert r.status_code == 409 and r.get_json()['cancelled'] is True
    assert not W._PLANS                            # nothing half-built is cached
    monkeypatch.undo()
    r = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 'B'}))
    assert r.status_code == 200 and r.get_json()['stream']['items']['kind']


def test_the_page_cancels_its_job(client, monkeypatch):
    from layer_assembly import web as W
    _slow_routes(monkeypatch, 0, on_route=lambda n: n == 1 and
                 client.post('/api/assembly/print_plan/cancel', json={'job': 'J'}))
    r = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 'J'}))
    assert r.status_code == 409
    # cancelling another job id changes nothing
    W._JOBS['latest'] = 'K'
    client.post('/api/assembly/print_plan/cancel', json={'job': 'J'})
    assert W._JOBS['latest'] == 'K'


def test_requests_without_a_job_are_never_cancelled(client, monkeypatch):
    from layer_assembly import web as W
    _slow_routes(monkeypatch, 0, on_route=lambda n: W._preempt_background() or W._JOBS.update(latest='X'))
    r = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'summary'}))
    assert r.status_code == 200 and r.get_json()['plan']['layers']


def test_assembly_editing_preempts_a_background_plan(monkeypatch):
    """A long Simulation plan is running; the user edits the Assembly: the
    resolve answers within about one layer's routing, not after the plan."""
    import app as A
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    W._PLANS.clear()
    req = _stream_req({'detail': 'stream', 'job': 'S'})
    req['assembly']['sections'] = [{'design_id': 'A', 'height': 45}]       # 30 layers
    started = threading.Event()
    _slow_routes(monkeypatch, 0.15, on_route=lambda n: started.set())       # ≥ 4.5 s in all
    out = {}

    def plan():
        out['plan'] = A.app.test_client().post('/api/assembly/print_plan', json=req)
    th = threading.Thread(target=plan)
    th.start()
    assert started.wait(30)
    t0 = time.perf_counter()
    r = A.app.test_client().post('/api/assembly/resolve', json={
        'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 6}]},
        'design_ids': ['A'], 'designs': req['designs']})
    waited = time.perf_counter() - t0
    th.join(60)
    assert r.status_code == 200 and r.get_json()['instances']
    assert waited < 2.0, waited                                             # not the whole plan
    assert out['plan'].status_code == 409 and out['plan'].get_json()['cancelled']
    assert not W._PLANS


def test_rebuild_plans_again_from_scratch(client, monkeypatch):
    from layer_assembly import web as W
    from layer_assembly import print_plan as PP
    calls = []
    real = PP.build_plan
    monkeypatch.setattr(PP, 'build_plan', lambda *a, **k: calls.append(1) or real(*a, **k))
    a = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 1})).get_json()
    client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 2}))
    assert len(calls) == 1                                                  # cached
    b = client.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream', 'job': 3,
                                                                  'rebuild': True})).get_json()
    assert len(calls) == 2                                                  # planned again …
    assert a['stream'] == b['stream'] and a['estimate'] == b['estimate']    # … to the same plan


def test_the_designer_adapter_is_used_by_one_request_at_a_time(monkeypatch):
    """Two plans (no job: never cancelled) at once never route concurrently."""
    import app as A
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    W._PLANS.clear()
    active, peak = [0], [0]
    from layer_assembly.designer_source import DesignerSource
    real = DesignerSource.route

    def route(self, g, start=None, **kw):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.01)
        try:
            return real(self, g, start, **kw)
        finally:
            active[0] -= 1
    monkeypatch.setattr(DesignerSource, 'route', route)
    reqs = [_stream_req(), _stream_req()]
    reqs[1]['assembly']['layer_height'] = 1.0
    ths = [threading.Thread(target=lambda q=q: A.app.test_client().post('/api/assembly/print_plan', json=q))
           for q in reqs]
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)
    assert peak[0] == 1
    assert len(W._PLANS) == 2


"""
Responsive Planar Top Cut editing (2026-10-08) — the server-side parts, which
must never change a result:
- lattice STATIONS only on designs that print a Wall Infill lattice (the only
  ones that read them): elsewhere they made every layer a distinct variant,
  rebuilt whenever the plane moved, with identical geometry;
- the support analysis reuses a layer boundary's sampling while both layers'
  geometry / placement / bead are unchanged (DesignerSource.support_cache):
  the report is identical to an uncached analysis.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Section, resolve
from layer_assembly.profiles import PlaneProfile, WallTarget, apply_profiles
from layer_assembly.source import stack_geometry
from layer_assembly.support import analyse_support
from layer_assembly.tests.test_profiles_plane_designer import building


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


PLANE = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.3, anchor=(230.0, 0.0), z_anchor=40.0)


def _cut(src, recs, profiles):
    a = Assembly(1.5, [Section('A', 60)], profiles=profiles)
    tg = {'A': {t.key: t for t in map(WallTarget.from_dict, src.walls('A'))}}
    res, rep = apply_profiles(resolve(a, ['A'], None), a.profiles, tg, 3.0, a.max_overhang, {'A': recs})
    return a, res


def test_stations_only_where_a_wall_infill_lattice_reads_them():
    gen = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('interleaved')}])
    web = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('skin_web')}])
    assert not any(r['lattice'] for r in gen.sources('A')) and all(r['lattice'] for r in web.sources('A'))
    _, rg = _cut(gen, gen.sources('A'), [PLANE])
    _, rw = _cut(web, web.sources('A'), [PLANE])
    assert not any(c[0] == 'station' for i in rg.instances for c in i.cuts)
    assert all(any(c[0] == 'station' for c in i.cuts) for i in rw.instances)
    # uncut layers of the generated design stay CANONICAL (no variant at all)
    assert any(not i.cuts for i in rg.instances)
    # … and the geometry is exactly what the stations gave before
    _, old = _cut(gen, [dict(r, lattice=True) for r in gen.sources('A')], [PLANE])
    for k in (0, len(rg.instances) // 2, len(rg.instances) - 1):
        a_ = gen.geometry('A', None, old.instances[k].cut_spec() or None).polylines
        b_ = gen.geometry('A', None, rg.instances[k].cut_spec() or None).polylines
        assert json.dumps(a_) == json.dumps(b_), k


def test_support_cache_gives_the_uncached_report():
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    P2 = PlaneProfile('Q', systems=('WS2',), direction=45.0, slope=0.2, anchor=(170.0, 10.0), z_anchor=55.0)
    for profs in ([PLANE], [PLANE, P2]):
        a, res = _cut(src, src.sources('A'), profs)
        geo = stack_geometry(res, src)
        ref = analyse_support(res, geo, a).to_dict()
        cache = {}
        first = analyse_support(res, geo, a, cache=cache).to_dict()
        n = len(cache)
        again = analyse_support(res, geo, a, cache=cache).to_dict()
        assert first == ref and again == ref and n > 0 and len(cache) == n
    # one plane moved: the boundaries it does not touch are reused, the result is still exact
    a, res = _cut(src, src.sources('A'), [PLANE, P2])
    cache = {}
    analyse_support(res, stack_geometry(res, src), a, cache=cache)
    # (Q only reaches the upper layers: moving it leaves every lower boundary as it was)
    moved = PlaneProfile('Q', systems=('WS2',), direction=45.0, slope=0.2, anchor=(176.0, 16.0), z_anchor=55.0)
    a2, res2 = _cut(src, src.sources('A'), [PLANE, moved])
    geo2 = stack_geometry(res2, src)
    n0 = len(cache)
    assert analyse_support(res2, geo2, a2, cache=cache).to_dict() == analyse_support(res2, geo2, a2).to_dict()
    assert len(cache) - n0 < len(res2.instances) - 1             # not every boundary re-sampled

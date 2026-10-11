"""
Regression (manual, 2026-10-08): overlapping circle + rectangle, both Wall
System members, the sections inside each other removed by Designer TRIM, then
a PLANAR TOP CUT. Plane-cut layers used to bring back the trimmed sections
(the cut opened the rectangle into a polyline, so the trims' topological
signatures — "between two contacts with the closed circle, inside it" — no
longer matched and resolved to nothing) → long interior walls, 140 in+
HEADER NEEDED spans. Invariant now: authored geometry incl. Trim → (pinned)
→ Wall System reference → plane clipping → local termination → support.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from app import app as flask_app
from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Section
from layer_assembly.profiles import PlaneProfile
from layer_assembly.support import analyse_support
from layer_assembly.tests.test_profiles_plane_designer import stackp, _changed, MAT, T


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _doc(kind, trims=()):
    d = {'id': 'l', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'x': 100, 'y': 100, 'w': 240, 'h': 160, 'closed': True},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Circle', 'cx': 340, 'cy': 180, 'radius': 80, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Link', 'start': [420, 180], 'end': [500, 180]}],
        'wall_systems': [{'id': 'WS1', 'name': 'WS1', 'type': kind, 'members': ['R'], 'thickness': T, 'params': {}},
                         {'id': 'WS2', 'name': 'WS2', 'type': kind, 'members': ['C', 'L'], 'thickness': T, 'params': {}}],
        'trims': list(trims)}
    if kind == 'skin_web':
        d['infills'] = []
        for ws, host in ((d['wall_systems'][0], 'R'), (d['wall_systems'][1], 'C')):
            ws['web'] = {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}
            d['infills'].append({'id': 'WEB' + host, 'path_id': host, 'pattern': 'zigzag', 'params': {'spacing': 20},
                                 'kind': 'wall', 'variation_index': 0, 'owner': ws['id']})
    return d


def trimmed_design(kind):
    """The design with Trim applied by the Designer's own sections (each
    shape's stretch inside the other removed)."""
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        net = c.post('/api/effective_paths', json=_doc(kind)).get_json()['network']
        trims = [{'id': 't' + s['source'], 'source_path_id': s['source'], 'start': s['start'], 'end': s['end'],
                  'inside': s['inside'], 'u_mid': s['u_mid']}
                 for s in net['trim_sections'] if s['source'] in 'RC' and s['inside'].get('C' if s['source'] == 'R' else 'R')]
        assert len(trims) == 2
        st = c.post('/api/effective_paths', json=_doc(kind, trims)).get_json()['network']['trims']
        assert all(v['status'] == 'ok' for v in st.values())
    return _doc(kind, trims)


def in_trimmed_region(q):
    """Strictly inside BOTH shapes, clear of every surviving wall band: only
    the trimmed-away sections could put material here."""
    x, y = q
    return math.hypot(x - 340, y - 180) < 80 - T - 2 and 100 + T + 2 < x < 340 - T - 2 and 100 + T + 2 < y < 260 - T - 2


PLANES = {
    'one': [PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.4, anchor=(420.0, 180.0), z_anchor=30.0)],
    'two': [PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.4, anchor=(420.0, 180.0), z_anchor=30.0),
            PlaneProfile('Q', systems=('WS1',), direction=0.0, slope=0.6, anchor=(100.0, 180.0), z_anchor=40.0)],
}


@pytest.mark.parametrize('kind', ['parallel', 'skin_web', 'interleaved'])
@pytest.mark.parametrize('planes', ['one', 'two'])
def test_planar_cut_never_resurrects_trimmed_geometry(kind, planes):
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': trimmed_design(kind)}])
    profs = PLANES[planes]
    a = Assembly(1.5, [Section('A', 120)], profiles=profs)
    res, rep, geo = stackp(src, a)
    assert all(r.valid for r in rep)
    canon = geo[res.instances[0].geometry_key][0]
    # ordinary Trim stays authoritative in the uncut design
    assert not [q for pl in canon for q in pl['pts'] if in_trimmed_region(q)]
    cut_layers = 0
    for inst in res.instances:
        if not any(c[0] == 'plane' for c in inst.cuts):
            continue
        cut_layers += 1
        polys = geo[inst.geometry_key][0]
        back = [(pl['id'], q) for pl in polys for q in pl['pts'] if in_trimmed_region(q)]
        assert not back, (inst.index, back[:3])
        built = src.lib.build('A', cuts=inst.cut_spec())
        assert all(t.get('pinned') for t in built.document.get('trims') or [])
        # coherent: far (> 2 T) from every plane's line the layer IS the uncut layer
        z = 0.5 * (inst.z_bottom + inst.z_top)
        lines = [P.line_at(z) for P in profs]
        def far(q):
            return all(ln in ('all',) or (ln != 'none' and abs((q[0] - ln[0][0]) * ln[1][0] + (q[1] - ln[0][1]) * ln[1][1]) > 2 * T)
                       for ln in lines)
        # lattice coherence (Skin + Web, Interleaved). (KNOWN, not Trim-related —
        # identical without trims: an ordinary Opening on a PARALLEL ring
        # re-connects a few lane ends elsewhere on that ring / at its junctions;
        # Parallel is checked for resurrection and spans only)
        if kind == 'parallel':
            continue
        tol = 2.0 if kind == 'interleaved' else 0.05
        bad = [q for q in _changed(polys, canon, tol)
               if far(q) and all(ln == 'all' or ln == 'none' or (q[0] - ln[0][0]) * ln[1][0] + (q[1] - ln[0][1]) * ln[1][1] > 0
                                 for ln in lines)]
        assert len(bad) <= (4 if kind == 'interleaved' else 0), (inst.index, bad[:4])
    assert cut_layers > 20
    # no giant false spans: every support finding is a local termination report
    fs = analyse_support(res, geo, a).findings
    assert all((f.span or 0) < 2 * T for f in fs), [(f.layer, f.kind, f.span) for f in fs if (f.span or 0) >= 2 * T]

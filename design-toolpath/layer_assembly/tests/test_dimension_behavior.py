"""
DIMENSION BEHAVIOR of Assembly scaling (2026-10-18; Assembly.dimension_behavior,
layer_assembly/model.py), through the REAL Assembly pipeline (the Flask
endpoints the page uses):

  * SCALE CONSTRUCTION ('scale' — the default, also when absent): the layer's
    resolved construction is placed by its similarity — ONE geometry for the
    whole stack; walls, bead spacing and lattice scale together (deposited
    width k (T − W) + W). Unchanged since d803f4a. Physical-bead feasibility
    is REPORTED (dimensions.py), never "fixed".
  * PRESERVE PHYSICAL DIMENSIONS ('physical'): the scale moves into the
    Designer (the whole architecture, about its footprint centre); the
    placement keeps the translation. Wall Thickness, Bead Width, Contact
    Overlap and opening widths stay physical; the Skin + Web web is carried
    through Z by the existing station carrying (wall_lattice §11).
Single centre only (Multiple centres: not supported with Preserve, warned).
"""
import math
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..', 'design_proto', 'tests')))
sys.path.append(os.path.abspath(os.path.join(HERE, '..', '..', 'toolpath_proto')))

from cross_z import circle, near, rect, skin_web_doc  # noqa: E402
from layer_assembly.profiles import OpeningProfile, WallRef  # noqa: E402

T, W, O = 10.0, 3.0, 0.75


@pytest.fixture(scope='module')
def client():
    os.environ['LAYER_ASSEMBLY_WORKERS'] = '0'
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _doc(paths=None, systems=None):
    return skin_web_doc(paths or [rect('R', 0, 0, 160, 100)], T=T, systems=systems)


def _assembly(mode=None, scale_in=0.25, height=81.0, profiles=(), center_mode='footprint', centers=None):
    a = {'layer_height': 1.0, 'max_overhang': 1.0,
         'sections': [{'design_id': 'b', 'height': height,
                       'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': scale_in}}],
         'center_mode': center_mode, 'objects': [], 'overrides': [], 'profiles': list(profiles),
         'opening_alignment': 'follow'}
    if centers is not None:
        a['centers'] = centers
    if mode is not None:
        a['dimension_behavior'] = mode
    return a


_CACHE = {}


def _run(c, doc, a):
    """(resolve result, {geometry key: polylines}) — through the endpoints."""
    import json
    key = json.dumps([doc, a], sort_keys=True)
    if key in _CACHE:
        return _CACHE[key]
    designs = [{'id': 'b', 'name': 'Base', 'parent': None, 'document': doc}]
    ws = doc['wall_systems']
    r = c.post('/api/assembly/resolve', json={'assembly': a, 'designs': designs, 'wall_systems': ws}).get_json()
    assert not r.get('error'), r.get('error')
    vs = r.get('variants') or {}
    keys = list(dict.fromkeys(i['geometry_key'] for i in r['instances']))
    g = c.post('/api/assembly/geometry', json={'designs': designs, 'wall_systems': ws, 'ids': ['b'],
                                               'variants': {k: vs[k] for k in keys if k in vs}}).get_json()['designs']
    _CACHE[key] = (r, {k: g[k] for k in keys})
    return _CACHE[key]


def _placed(r, g, li):
    inst = r['instances'][li]
    t = inst['transform']
    P = lambda q: (t['scale'] * q[0] + t['tx'], t['scale'] * q[1] + t['ty'])    # noqa: E731
    return [dict(pl, pts=[P(q) for q in pl['pts']]) for pl in g[inst['geometry_key']]['polylines']], g[inst['geometry_key']]


def _skins(polys, sid='R'):
    return [pl for pl in polys if (pl.get('id') or '').startswith(sid + '.')]


def _bottom(polys):
    """(outer skin y, inner skin y) of the rectangle's bottom wall, and the
    web points between its rails."""
    ys = sorted(min(q[1] for q in pl['pts']) for pl in _skins(polys))
    web = [q for pl in polys if not (pl.get('id') or '').startswith('R.') for q in pl['pts']
           if ys[0] + 0.05 < q[1] < ys[1] - 0.05]
    return ys[0], ys[1], web


LAYERS = (0, 20, 40, 60, 80)


# ---- SCALE CONSTRUCTION ---------------------------------------------------------------

def test_absent_field_is_scale_construction(client):
    from layer_assembly.model import Assembly
    assert Assembly.from_dict(_assembly()).dimension_behavior == 'scale'
    assert Assembly.from_dict(_assembly()).to_dict()['dimension_behavior'] == 'scale'
    r0, _ = _run(client, _doc(), _assembly())
    r1, _ = _run(client, _doc(), _assembly('scale'))
    assert r0['instances'] == r1['instances']


def test_scale_construction_places_one_resolved_construction(client):
    """One geometry for the whole stack (no Designer variant); its resolved
    construction scales: skin gap k (T − W), deposited k (T − W) + W, contact
    offset k (W − O); Bead Width physical downstream."""
    r, g = _run(client, _doc(), _assembly('scale'))
    assert len({i['geometry_key'] for i in r['instances']}) == 1 and not r.get('variants')
    for li in LAYERS:
        polys, geo = _placed(r, g, li)
        k = r['instances'][li]['transform']['scale']
        y0, y1, web = _bottom(polys)
        assert math.isclose(y1 - y0, k * (T - W), abs_tol=1e-6), li
        assert geo['bead_width'] == W
        assert min(q[1] - y0 for q in web) <= k * (W - O) + 0.2          # (contact scales; transfer aside)
    k80 = r['instances'][80]['transform']['scale']
    assert k80 < 0.8 and not math.isclose(_bottom(_placed(r, g, 80)[0])[1] - _bottom(_placed(r, g, 80)[0])[0] + W, T)
    assert r['support']['findings'] == []


def test_scale_construction_reports_when_the_bead_cannot_follow(client):
    """A strong taper: the scaled centreline construction asks the physical
    3 in bead for spacing it cannot make — reported, Bead Width untouched."""
    doc = _doc([rect('R', 0, 0, 60, 40)])
    r, _ = _run(client, doc, _assembly('scale', scale_in=0.5, height=52.0))     # (k → 0.15)
    kinds = {f['kind'] for f in (r.get('dimension') or {}).get('findings', [])}
    assert 'parallel_overlap' in kinds and 'web_pitch_floor' in kinds
    assert any('Scale construction' in w for w in r['warnings'])
    mild, _ = _run(client, _doc(), _assembly('scale'))
    assert not mild.get('dimension')


# ---- PRESERVE PHYSICAL DIMENSIONS ----------------------------------------------------------

def test_preserve_keeps_physical_dimensions(client):
    """Wall Thickness, Bead Width and Contact Overlap stay physical on every
    layer; the placement is a translation (the scale is in the variant)."""
    r, g = _run(client, _doc(), _assembly('physical'))
    assert r.get('variants') and len({i['geometry_key'] for i in r['instances']}) == 81
    for li in LAYERS:
        inst = r['instances'][li]
        assert inst['transform']['scale'] == 1.0
        polys, geo = _placed(r, g, li)
        y0, y1, web = _bottom(polys)
        assert math.isclose(y1 - y0, T - W, abs_tol=1e-6), li           # deposited = T
        assert geo['bead_width'] == W
        rails = sorted({round(q[1] - y0, 3) for q in web})
        assert W - O in rails and (T - W) - (W - O) in rails, (li, rails)
    assert r['support']['findings'] == []


def test_preserve_carries_the_interior_and_corners_consume(client):
    r, g = _run(client, _doc(), _assembly('physical'))
    mid = lambda li: sorted({round(q[0], 3) for q in _bottom(_placed(r, g, li)[0])[2] if 55 < q[0] < 105})  # noqa
    for li in LAYERS:
        assert mid(li) == mid(0), li                                  # interior stations persist
    n = lambda li: len(_bottom(_placed(r, g, li)[0])[2])              # noqa: E731
    assert n(80) < n(0)                                               # the corners consumed stations


def test_preserve_routes_close(client):
    from layer_design import DesignLibrary
    from cross_z import route
    doc = _doc()
    r, _ = _run(client, doc, _assembly('physical'))
    lib = DesignLibrary([{'id': 'b', 'name': 'Base', 'parent': None, 'document': doc}], doc['wall_systems'])
    vs = r['variants']
    for li in LAYERS[1:]:
        v = vs[r['instances'][li]['geometry_key']]
        b = lib.build('b', transforms={s: tuple(t) for s, t in v['transforms'].items()})
        ends, pieces = route(b)
        assert not ends and pieces == 1, li


def test_same_footprint_different_wall_thickness(client):
    """The SAME transform in both modes: the architecture's outer face lands
    in the same place; Scale construction tapers the wall, Preserve keeps it."""
    ra, ga = _run(client, _doc(), _assembly('scale'))
    rb, gb = _run(client, _doc(), _assembly('physical'))
    for li in LAYERS:
        ka = ra['instances'][li]['transform']['scale']
        ya0, ya1, _ = _bottom(_placed(ra, ga, li)[0])
        yb0, yb1, _ = _bottom(_placed(rb, gb, li)[0])
        assert math.isclose(ya0 - ka * W / 2, yb0 - W / 2, abs_tol=1e-6), li     # the same outer face
        if li:
            assert ya1 - ya0 < yb1 - yb0 - 0.1                                   # thickness differs
        assert math.isclose(yb1 - yb0, T - W, abs_tol=1e-6)


def _opening(pos=60.0, zb=10.0, w=20.0, half=16.0):
    return OpeningProfile('P', WallRef('R', 0), pos, zb, 0, w, upper='pointed', lower='pointed', middle=False,
                          upper_height=half, bottom_depth=half).to_dict()


def _gap(polys, y0, x0, x1):
    """The widest stretch of the bottom wall band (|y − y0| ≤ T / 2) between
    x0 and x1 that no bead covers (each segment ± ½ bead)."""
    iv = sorted((min(a[0], b[0]) - W / 2, max(a[0], b[0]) + W / 2)
                for pl in polys for a, b in zip(pl['pts'], pl['pts'][1:] + (pl['pts'][:1] if pl.get('closed') else []))
                if abs(a[1] - y0) <= T / 2 + 1e-6 and abs(b[1] - y0) <= T / 2 + 1e-6)
    best, x = 0.0, x0
    for a, b in iv:
        if b < x0:
            continue
        if a > x:
            best = max(best, min(a, x1) - x)
        x = max(x, b)
        if x >= x1:
            break
    return best


@pytest.mark.parametrize('mode', ['scale', 'physical'])
def test_self_supporting_opening_keeps_its_physical_width(client, mode):
    """An Assembly diamond opening on the bottom wall: its cut is its
    intended physical width in both modes (Scale construction compensates
    the placement scale; Preserve leaves widths physical — never twice)."""
    r, g = _run(client, _doc(), _assembly(mode, height=51.0, profiles=[_opening()]))
    prof = r['profiles'][0]
    assert prof['valid'], prof
    widest = max(L['width'] for L in prof['layers'])
    k = next(L for L in prof['layers'] if L['width'] == widest)['layer']
    inst = r['instances'][k]
    polys, _ = _placed(r, g, k)
    sk = _skins(polys)
    yb = min(q[1] for pl in sk for q in pl['pts']) + (T / 2 - W / 2) * inst['transform']['scale']
    # Follow Wall: the profile's width is in the layer's design coordinates —
    # Scale construction scales it with the layer (as before); Preserve keeps
    # it physical, never compensated twice
    # (the jambs' cap beads lie inside the cut: the band left uncovered is the
    # cut less one PHYSICAL bead)
    want = widest * inst['transform']['scale']
    assert math.isclose(_gap(polys, yb, 20, 120) + W, want, abs_tol=0.3), (mode, _gap(polys, yb, 20, 120), want)


def test_preserve_opening_changes_stay_local(client):
    """Away from the opening (> 25 in), each cut layer's web is the same
    layer variant without the cut (its lattice station kept — the ring's web
    is doubled for the opening on every layer, by design): Preserve physical
    dimensions keeps the opening a local change."""
    from layer_design import DesignLibrary
    doc = _doc()
    rp, gp = _run(client, doc, _assembly('physical', height=51.0, profiles=[_opening()]))
    assert rp['profiles'][0]['valid'] and len(rp['profiles'][0]['layers']) > 10
    assert rp['support']['findings'] == []
    lib = DesignLibrary([{'id': 'b', 'name': 'Base', 'parent': None, 'document': doc}], doc['wall_systems'])
    cut_layers = [L['layer'] for L in rp['profiles'][0]['layers'] if L['width'] > 0]
    for li in cut_layers[::6]:
        inst = rp['instances'][li]
        v = rp['variants'][inst['geometry_key']]
        stations = [c for c in v.get('cuts') or [] if c.get('op') == 'station']
        assert len(stations) < len(v.get('cuts') or []), li                 # this layer is cut
        uncut = lib.build('b', transforms={s_: tuple(t) for s_, t in v['transforms'].items()}, cuts=stations)
        t = inst['transform']
        ref = [[(t['scale'] * q.x + t['tx'], t['scale'] * q.y + t['ty']) for q in pl]
               for vv in uncut.lattice.values() for pl in vv]
        a, _ = _placed(rp, gp, li)
        far = [q for pl in a if not pl['id'].startswith('R.') for q in pl['pts'] if abs(q[0] - 60) > 25]
        assert far and all(near(q, ref, 0.1) for q in far), li


def test_preserve_isolated_circle(client):
    """An isolated Skin + Web circle: wall stays physical, every layer
    supported and closed, no corner appears (Step 8 rules carry it)."""
    doc = _doc([circle('R', 80, 80, 60)])
    r, g = _run(client, doc, _assembly('physical', scale_in=0.5, height=41.0))
    for li in (0, 20, 40):
        polys, _ = _placed(r, g, li)
        sk = _skins(polys)
        c = (80.0, 80.0)
        rads = sorted(sum(math.dist(q, c) for q in pl['pts']) / len(pl['pts']) for pl in sk)
        assert math.isclose(rads[1] - rads[0], T - W, abs_tol=0.05), li
    assert r['support']['findings'] == []


def test_preserve_circle_rectangle_junction_findings_stay_local(client):
    """KNOWN Step 9 limitation: an overlapping same-system circle + rectangle
    under Preserve — the junction's lens transition may leave support
    findings, but only at the junction."""
    doc = _doc([rect('R', 0, 0, 100, 80), circle('C', 120, 40, 30)], systems=[['R', 'C']])
    r, _ = _run(client, doc, _assembly('physical', scale_in=0.5, height=31.0))
    for f in r['support']['findings']:
        x, y = f['centre']
        assert 80 < x < 115 and 5 < y < 75, f


def _variant_web(r, g, li):
    """(design point → this layer's VARIANT frame, the layer's web
    polylines in that frame — the Designer's resolution, before the
    translation-only placement)."""
    inst = r['instances'][li]
    dt, t = inst.get('design_transform') or inst['transform'], inst['transform']
    f = lambda p: (dt['scale'] * p[0] + dt['tx'] - t['tx'], dt['scale'] * p[1] + dt['ty'] - t['ty'])  # noqa: E731
    polys = g[inst['geometry_key']]['polylines']
    return f, [pl['pts'] for pl in polys if (pl.get('id') or '').startswith('WEB')]


def test_preserve_circle_rectangle_construction_persists_away_from_the_junction(client):
    """TEST E (transitions milestone, WALL_ARCHITECTURE §Z.11): an overlapping
    same-system circle + rectangle under Preserve, single centre. The
    circle's web keeps its angles (radial host motion — never re-phased),
    the rectangle's bottom wall keeps its stations, every change of the
    deposited web lies at the junction or a moving corner, and the
    circle's repeat count never jumps."""
    doc = _doc([rect('R', 0, 0, 100, 80), circle('C', 120, 40, 30)], systems=[['R', 'C']])
    r, g = _run(client, doc, _assembly('physical', scale_in=0.5, height=41.0))
    S = (T - W - 2 * (W - O)) / math.tan(math.radians(20.0))
    m = S + 2 * T
    n = len(r['instances'])

    def circ(li):
        # (the circle's far half, away from both junctions: landing angles)
        f, web = _variant_web(r, g, li)
        c = f((120, 40))
        a = sorted(math.degrees(math.atan2(q[1] - c[1], q[0] - c[0])) for pl in web for q in pl
                   if math.dist(q, c) > 0.5 * math.dist(f((120, 40)), f((90, 40)))
                   and abs(math.degrees(math.atan2(q[1] - c[1], q[0] - c[0]))) < 90)
        return [x for i, x in enumerate(a) if i == 0 or x - a[i - 1] > 0.3]      # (one per landing)

    def bottom(li, at=None):
        f, web = _variant_web(r, g, li)
        fa = _variant_web(r, g, li if at is None else at)[0]
        yc = f((0, 0))[1] + T / 2
        lo, hi = max(fa((0, 0))[0], 0.0) + m, min(fa((100, 0))[0], 100.0) - m
        return sorted({round(q[0], 2) for pl in web for q in pl if abs(abs(q[1] - yc) - 1.25) < 0.05 and lo < q[0] < hi})

    a0 = circ(0)
    for li in range(n):
        got = circ(li)
        assert len(got) == len(a0), li                       # nothing born / dies away from the junctions
        if li:                                               # never re-phased (radial host motion: the
            prev = circ(li - 1)                              # contact points drift < ½° per layer)
            assert all(any(abs(a - b) <= 0.5 for b in got) for a in prev), li
        assert bottom(li) == bottom(0, at=li), li                     # (stations never move along)
        if li:
            f, web = _variant_web(r, g, li)
            _, prev = _variant_web(r, g, li - 1)
            marks = [f(p) for p in ((100, 40), (100, 17.6), (100, 62.4), (0, 0), (100, 0), (0, 80), (100, 80))]
            for pl in web:
                for a, b in zip(pl, pl[1:]):
                    k = max(1, int(math.dist(a, b) / 0.5))
                    for j in range(k + 1):
                        p = (a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k)
                        if not near(p, prev, 1.0):
                            assert any(math.dist(p, q) <= m for q in marks), (li, p)
    assert bottom(n - 1), 'no bottom-wall station far from the corners'


def test_multiple_centres_with_preserve_is_not_supported(client):
    a = _assembly('physical', center_mode='multiple', centers=[{'id': 'c1', 'x': 80, 'y': 50, 'sources': ['R']}])
    r, _ = _run(client, _doc(), a)
    assert any('not supported with Multiple centres' in w for w in r['warnings'])
    assert not r.get('variants')                                       # Scale construction used


def test_preview_and_print_plan_use_the_same_geometry(client):
    """The print plan prints each layer's own variant, placed by the same
    (translation) placement the page draws it with."""
    doc = _doc()
    a = _assembly('physical', height=11.0)
    r, _ = _run(client, doc, a)
    designs = [{'id': 'b', 'name': 'Base', 'parent': None, 'document': doc}]
    p = client.post('/api/assembly/print_plan', json={'assembly': a, 'designs': designs,
                                                     'wall_systems': doc['wall_systems']}).get_json()
    assert not p.get('error'), p
    for L, inst in zip(p['plan']['layers'], r['instances']):
        assert L['placement'] == inst['transform']
        assert L['placement']['scale'] == 1.0


def test_project_file_round_trips_the_setting(client):
    """Save Project / Open Project keep Preserve physical dimensions; a file
    without the field opens as Scale construction."""
    import json
    doc = _doc()
    for mode in (None, 'physical'):
        st = _assembly(mode)
        proj = {'name': 'p', 'designer': {'designs': [{'id': 'b', 'name': 'Base', 'parent': None, 'document': doc}],
                                          'active_design': 'b', 'id_counter': 1, 'design_counter': 1},
                'assembly': {'state': st, 'header_counter': 0}}
        saved = client.post('/api/project/save', json={'project': proj}).get_json()
        assert not saved.get('error'), saved
        text = saved.get('text') or json.dumps(saved.get('file') or saved)
        opened = client.post('/api/project/open', data=text).get_json()
        assert not opened.get('error'), opened
        got = opened['project']['assembly']['state'].get('dimension_behavior')
        assert (got or 'scale') == (mode or 'scale')
        from layer_assembly.model import Assembly
        assert Assembly.from_dict(opened['project']['assembly']['state']).dimension_behavior == (mode or 'scale')

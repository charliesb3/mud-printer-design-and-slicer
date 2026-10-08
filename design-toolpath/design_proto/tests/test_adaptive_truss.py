"""
ADAPTIVE TRUSS wall infill (pattern 'truss', 2026-10-07) — the wall-web
experiment's phase-field direction inside the production wall lattice:
station progression from Brace Angle and the local cavity width, a bond
along each skin with filleted transitions, Maximum Unsupported Span per
skin, contact identity carried through Z, deterministic Regenerate (solution
seed, hashed per feature). The topology machinery (runs, corners, caps,
junction hand-offs, doubled runs, jambs, lineage scaffold) is production's.
"""
import math
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer                 # noqa: E402  (path setup)
import wall_lattice as WL                         # noqa: E402
import wall_fixtures as WF                        # noqa: E402
import physical_fixtures as PF                    # noqa: E402
import reference_network as RN                    # noqa: E402
import multi_opening_fixtures as MO               # noqa: E402
from infill import _Region                        # noqa: E402
from layer_design import DesignLibrary            # noqa: E402
from graph import route_layer, compute_metrics, build_graph, closure_report   # noqa: E402
from model import (Vec2, PrintLayer, RectanglePath, RegionInfill, WallSpec, Opening,   # noqa: E402
                   LinePath)

C = 2.25            # contact separation of the physical defaults (W 3, O 0.75)
D = 40.0            # default Max Unsupported Span


def plan(rings, **tp):
    return WL.plan(rings, 20, 'truss', 0, True, None, contact=C, closed=True, bead=3.0, truss=tp)


def band(centre, half):
    """Open wall around a centre polyline (per-vertex half widths)."""
    left, right = [], []
    n = len(centre)
    for i in range(n):
        a, b = centre[max(0, i - 1)], centre[min(n - 1, i + 1)]
        t = (b - a) * (1.0 / (b - a).length())
        nrm = Vec2(-t.y, t.x)
        left.append(centre[i] + nrm * half[i])
        right.append(centre[i] - nrm * half[i])
    ring = right + list(reversed(left))
    area = sum(ring[i].x * ring[(i + 1) % len(ring)].y - ring[(i + 1) % len(ring)].x * ring[i].y
               for i in range(len(ring)))
    return [ring if area > 0 else ring[::-1]]


def line_rings(L=280.0, t0=10.0, t1=None):
    t1 = t0 if t1 is None else t1
    c = [Vec2(40 + L * k / 140, 200) for k in range(141)]
    return band(c, [(t0 + (t1 - t0) * k / 140) / 2 for k in range(141)])


def arc_rings(R=250.0, t=10.0):
    c = [Vec2(180 + R * math.cos(math.radians(238 + 64 * k / 100)),
              200 + R + R * math.sin(math.radians(238 + 64 * k / 100))) for k in range(101)]
    return band(c, [t / 2] * len(c))


def layer_rings(layer):
    out = []
    for lat in layer.network_summary()['lattice'].values():
        for reg in lat['regions']:
            if reg.get('rings'):
                out.append([[Vec2(x, y) for x, y in r] for r in reg['rings']])
    return out


def ring_layer(openings=(), seed=0, t=10.0):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(t)
    lay = PrintLayer('t', source_paths=[R],
                     infills=[RegionInfill('I', 'R', 'truss', {'seed': seed})])
    lay.openings = [Opening(f'o{k}', 'R', c, w) for k, (c, w) in enumerate(openings)]
    return PF._phys(lay)


# --- geometry probes ----------------------------------------------------------

def _seg_d(p, a, b):
    d = b - a
    L2 = d.x * d.x + d.y * d.y
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * d.x + (p.y - a.y) * d.y) / L2))
    return p.dist(a + d * t)


def nearest_face(rings, p):
    best = None
    for ri, r in enumerate(rings):
        s = 0.0
        for k in range(len(r)):
            a, b = r[k], r[(k + 1) % len(r)]
            d = _seg_d(p, a, b)
            if best is None or d < best[0]:
                L = a.dist(b)
                t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((p - a).x * (b - a).x + (p - a).y * (b - a).y) / (L * L)))
                best = (d, ri, s + t * L, (b - a) * (1.0 / max(L, 1e-12)))
            s += a.dist(b)
    return best


def samples(polys, step=0.5):
    out = []
    for pl in polys:
        for a, b in zip(pl, pl[1:]):
            n = max(1, int(math.ceil(a.dist(b) / step)))
            out += [a.lerp(b, k / n) for k in range(n)]
        out.append(pl[-1])
    return out


def inside(rings, polys):
    reg = _Region(rings, 4.0)
    return all(reg.inside(q) or reg.dist(q, 0.05) < 0.05 for q in samples(polys))


def brace_angles(rings, polys, deep=3.0):
    """Angle (deg) of the web to the nearest face where the web is deep in
    the cavity (mid-brace), wall-relative."""
    out = []
    for pl in polys:
        for a, b in zip(pl, pl[1:]):
            m = a.lerp(b, 0.5)
            fd = nearest_face(rings, m)
            if fd[0] < deep or a.dist(b) < 0.3:
                continue
            u = (b - a) * (1.0 / a.dist(b))
            out.append(math.degrees(math.asin(min(1.0, abs(u.x * fd[3].y - u.y * fd[3].x)))))
    return sorted(out)


def skin_spans(rings, polys):
    """Per ring (face centreline): gaps ALONG the face between consecutive
    contact samples (web within contact + 0.6 in of it)."""
    arcs = {}
    for q in samples(polys):
        d, ri, s, _ = nearest_face(rings, q)
        if d <= C + 0.6:
            arcs.setdefault(ri, []).append(s)
    gaps = []
    for ri, r in enumerate(rings):
        L = sum(r[k].dist(r[(k + 1) % len(r)]) for k in range(len(r)))
        xs = sorted(arcs.get(ri, []))
        if not xs:
            gaps.append(L)
            continue
        gaps += [(xs[(i + 1) % len(xs)] - xs[i]) % L for i in range(len(xs))]
    return gaps


def key(polys):
    return [[(round(q.x, 9), round(q.y, 9)) for q in pl] for pl in polys]


# --- straight / curved / variable / corner / short -------------------------------

def test_straight_wall_braces_at_the_brace_angle_and_both_skins_supported():
    rings = line_rings()
    lp = plan(rings, brace_angle=45)
    ang = brace_angles(rings, lp.polylines)
    assert ang and abs(ang[len(ang) // 2] - 45) < 4, ang[len(ang) // 2]
    assert max(skin_spans(rings, lp.polylines)) <= D
    assert inside(rings, lp.polylines)
    # pitch = cavity · cot θ + bond: 5.5 · 1 + 3 = 8.5 in on a straight 10 in wall
    # (the readout uses production's area / perimeter thickness estimate)
    assert 7.5 < lp.report['truss']['effective_pitch'] < 9.0
    assert 7.5 < lp.report['pitch_min'] <= lp.report['pitch_max'] < 9.5
    # a steeper brace → a shorter pitch; the angle follows the parameter
    lp60 = plan(rings, brace_angle=60)
    a60 = brace_angles(rings, lp60.polylines)
    assert abs(a60[len(a60) // 2] - 60) < 5 and lp60.report['pitch_max'] < lp.report['pitch_min']


def test_same_geometry_parameters_and_seed_reproduce_the_lattice():
    rings = arc_rings()
    assert key(plan(rings, seed=4).polylines) == key(plan(rings, seed=4).polylines)


def test_contacts_are_bonds_with_smooth_transitions_not_sharp_vs():
    rings = line_rings()
    lp = plan(rings, bond=3.0, turn_radius=1.5)
    assert lp.report['truss']['stitches'].get('truss', 0) > 20
    # bond: web runs along each skin at the contact separation for ≈ 3 in
    on = [q for q in samples(lp.polylines, 0.25) if nearest_face(rings, q)[0] <= C + 0.05]
    assert len(on) * 0.25 > 0.6 * 3.0 * lp.report['runs'][0]['stitches']
    # no sharp reversal at ordinary landings (interior vertices turn ≤ 60°)
    pl = lp.polylines[0]
    turns = []
    for a, v, b in zip(pl, pl[1:], pl[2:]):
        u1, u2 = v - a, b - v
        if not 70 < v.x < 290:
            continue                                  # the cap V turnarounds at the wall ends
        if u1.length() > 1e-9 and u2.length() > 1e-9:
            c = (u1.x * u2.x + u1.y * u2.y) / (u1.length() * u2.length())
            turns.append(math.degrees(math.acos(max(-1.0, min(1.0, c)))))
    assert max(turns) <= 60, max(turns)
    # the zigzag of the same wall reverses sharply at every landing
    zz = WL.plan(rings, 8.5, 'zigzag', 0, True, None, contact=C, closed=True, bead=3.0)
    assert zz is not None


def test_curved_wall_angle_is_wall_relative_and_both_skins_within_the_span():
    rings = arc_rings(R=60.0)                      # strongly curved: a straight ray at a shallow angle would graze
    lp = plan(rings, brace_angle=30)
    ang = brace_angles(rings, lp.polylines)
    assert abs(ang[len(ang) // 2] - 30) < 7, ang[len(ang) // 2]
    assert max(skin_spans(rings, lp.polylines)) <= D
    assert inside(rings, lp.polylines)


def test_variable_width_keeps_the_angle_and_adapts_the_pitch():
    rings = line_rings(t0=8.0, t1=18.0)
    lp = plan(rings)
    narrow = brace_angles(rings, [[q for q in pl if q.x < 150] for pl in lp.polylines], deep=2.5)
    wide = brace_angles(rings, [[q for q in pl if q.x > 220] for pl in lp.polylines], deep=2.5)
    assert abs(narrow[len(narrow) // 2] - 45) < 8 and abs(wide[len(wide) // 2] - 45) < 8
    # contact spacing along the wall grows with the cavity
    xs = sorted(q.x for q in samples(lp.polylines) if nearest_face(rings, q)[0] <= C + 0.05 and q.y < 200)
    groups = []
    for x in xs:
        if not groups or x - groups[-1][-1] > 1.0:
            groups.append([x])
        else:
            groups[-1].append(x)
    mids = [sum(g) / len(g) for g in groups]
    gaps = [(a + b) / 2 for a, b in zip(mids, mids[1:])]
    pitch = [b - a for a, b in zip(mids, mids[1:])]
    near = [p for p, x in zip(pitch, gaps) if x < 130]
    far = [p for p, x in zip(pitch, gaps) if x > 230]
    assert sum(far) / len(far) > 1.3 * sum(near) / len(near)


def test_corner_reaches_the_existing_corner_brace_cleanly():
    lay = PF._phys(WF.polygon_wall(4, pat='truss'))
    (rings,) = layer_rings(lay)
    lp = plan(rings)
    assert sum(r['corners'] for r in lp.report['runs']) == 4      # production corner anchors
    assert inside(rings, lp.polylines)
    assert max(skin_spans(rings, lp.polylines)) <= D


def test_short_wall_degrades_gracefully():
    c = [Vec2(180 + 36 * k / 18, 200) for k in range(19)]
    rings = band(c, [5.0] * 19)
    lp = plan(rings)
    assert lp is not None and lp.polylines
    assert inside(rings, lp.polylines)
    assert lp.report['runs'][0]['stitches'] >= 1


# --- openings / lineage -------------------------------------------------------------

def _far(polys, pred):
    return sorted((round(q.x, 6), round(q.y, 6)) for pl in polys for q in pl if pred(q))


def test_an_opening_edit_is_local():
    """Moving an opening 2 in re-solves the two segments ending at its caps;
    the far wall (between corners) keeps its count and moves only by the
    corner anchors' own jitter (< 1.5 in) — no re-phase of the ring."""
    res = []
    for c in (100.0, 102.0):
        (rings,) = layer_rings(ring_layer([(c, 30.0)]))
        lp = plan(rings)
        res.append((lp.report['track']['runs'][0]['counts'], lp.polylines))
    (ca, pa), (cb, pb) = res
    assert ca[2] == cb[2]                                     # the far wall's segment count
    Sb = samples(pb)
    far = [q for q in samples(pa) if q.y > 240 and 120 < q.x < 280]
    dev = max(min(q.dist(x) for x in Sb if abs(x.x - q.x) < 10) for q in far)
    assert dev < 1.5, dev


def test_multiple_openings_do_not_rephase_unrelated_pieces():
    ops = [(100.0, 24.0), (300.0, 30.0), (430.0, 30.0), (560.0, 24.0)]
    a = layer_rings(ring_layer(ops))
    b = layer_rings(ring_layer([(102.0, 24.0)] + ops[1:]))
    pa = [pl for rings in a for pl in plan(rings).polylines]
    pb = [pl for rings in b for pl in plan(rings).polylines]
    far = lambda q: q.x > 230 and q.y > 245          # the piece between the 2nd and 3rd openings
    assert _far(pa, far) == _far(pb, far)            # (planned on its own: exactly unchanged)


def _truss_ref_lib(seed=0, openings=('rect1_top',), style='round'):
    base = RN.document('truss')
    base['infills'][0]['params'] = {'seed': seed}
    base['junction_style'], base['junction_radius'] = style, 6
    cuts = {k: dict(zip(('source_path_id', 'center_s', 'width'), RN.OPENINGS[k])) for k in openings}
    return DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base},
                          {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': cuts}}])


def _route(lib, d, transforms=None):
    lay = _deserialise_layer(lib.transformed(d, transforms)[0])
    lay.lattice_reference = lib.lattice_reference(d, transforms) or None
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    return closure_report(build_graph(rl)), compute_metrics(route_layer(rl, allow_retrace=False)), m


@pytest.mark.parametrize('style', ['round', 'miter'])
def test_reference_network_routes_closed_with_the_shared_truss(style):
    lib = _truss_ref_lib(style=style)
    for d in ('base', 'gaps'):
        c, mt, m = _route(lib, d)
        assert not c['open'], (d, c['open'])
        assert mt['print_runs'] == 1 and mt['retrace_distance'] == 0, (d, mt)
    assert lib.lattice_reference('gaps') and not lib.lattice_lineage('gaps')['IN'].get('planned_alone')
    ns = _deserialise_layer(lib.document('base')).network_summary()
    info = next(i for i in ns['infills'] if i['id'] == 'IN')['lattice']
    assert info['truss'] and info['truss']['effective_pitch'] > 0


def test_regenerate_is_lineage_wide():
    """The seed is part of the LINEAGE lattice definition: Regenerate pressed
    in a derived design moves to the owner, and every member prints the
    one regenerated scaffold."""
    lib = _truss_ref_lib(seed=0)
    s0 = lib.scaffold('base')['IN']['polylines']
    doc = lib.document('gaps')
    doc['infills'][0]['params'] = dict(doc['infills'][0]['params'], seed=3)   # Regenerate in "gaps"
    lib.set_document('gaps', doc)
    assert lib.document('base')['infills'][0]['params']['seed'] == 3
    assert lib.document('gaps')['infills'][0]['params']['seed'] == 3
    s3 = lib.scaffold('base')['IN']['polylines']
    assert s3 != s0                                           # another valid solution …
    assert lib.scaffold('gaps')['IN']['polylines'] == s3      # … shared by the derived design
    for d in ('base', 'gaps'):
        c, mt, _ = _route(lib, d)
        assert not c['open'] and mt['print_runs'] == 1


# --- Regenerate --------------------------------------------------------------------

def test_different_seeds_select_other_valid_solutions():
    rings = arc_rings(R=80.0)
    seen = {}
    for seed in range(6):
        lp = plan(rings, seed=seed)
        assert inside(rings, lp.polylines) and max(skin_spans(rings, lp.polylines)) <= D
        seen.setdefault(repr(key(lp.polylines)), seed)
    assert len(seen) >= 2


def test_a_corner_less_loop_regenerates_its_free_phase():
    from model import CirclePath
    Cp = CirclePath(200, 200, 90, id='C')
    Cp.wall = WallSpec(10)
    lay = PF._phys(PrintLayer('t', source_paths=[Cp], infills=[RegionInfill('I', 'C', 'truss', {})]))
    (rings,) = layer_rings(lay)
    keys = {repr(key(plan(rings, seed=s).polylines)) for s in range(4)}
    assert len(keys) >= 2
    for s in range(4):
        assert max(skin_spans(rings, plan(rings, seed=s).polylines)) <= D


def test_choices_are_hashed_per_feature_not_a_global_stream():
    assert WL._hchoice(7, '10,20', 'face') == WL._hchoice(7, '10,20', 'face')
    # an unrelated wall added to the layer leaves the ring's solution untouched
    a = ring_layer(seed=5)
    b = ring_layer(seed=5)
    extra = LinePath(Vec2(500, 100), Vec2(500, 300), id='X')
    extra.wall = WallSpec(10)
    b.source_paths.append(extra)
    b.infills.append(RegionInfill('IX', 'X', 'truss', {'seed': 5}))
    ra, rb = layer_rings(a), layer_rings(b)
    ring_b = next(r for r in rb if all(q.x < 400 for q in r[0]))
    assert key(plan(ra[0], seed=5).polylines) == key(plan(ring_b, seed=5).polylines)


def test_regenerate_survives_serialisation():
    doc = RN.document('truss')
    doc['infills'][0]['params'] = {'seed': 9, 'brace_angle': 50}
    inf = _deserialise_layer(doc).infills[0]
    assert inf.pattern == 'truss' and inf.params['seed'] == 9
    assert inf.to_dict()['params'] == {'seed': 9, 'brace_angle': 50}


# --- cross-Z ---------------------------------------------------------------------------

def _mo_lib(seed=0):
    doc = MO.document(())
    doc['infills'][0]['pattern'] = 'truss'
    doc['infills'][0]['params'] = {'seed': seed}
    return DesignLibrary([{'id': 'b', 'name': 'Base', 'document': doc}])


def _circle(b):
    return [[(q.x, q.y) for q in pl] for pl in b.lattice['IR']]


def _registered(lo, up, tol, box):
    S = [(a, b) for pl in lo for a, b in zip(pl, pl[1:])]

    def segd(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)
    pts = [q for pl in up for q in pl if box(q)]
    return pts and max(min(segd(q, a, b) for a, b in S) for q in pts) <= tol


def test_contact_identity_persists_through_a_gentle_transform():
    """Each transformed layer is tracked against the reference plan: counts
    kept, contact phases carried, so the circle's truss follows the circle
    (≤ ≈ 1 in per 1.2 % scale step) instead of being rediscovered per layer."""
    L = _mo_lib()
    prev = L.build('b')
    for j in range(1, 9):
        k = 1 - 0.012 * j
        b = L.build('b', transforms={'C': (k, 480 * (1 - k), 180 * (1 - k))})
        (t,) = [reg.get('track') for reg in b.network['lattice']['IR']['regions']]
        assert t['tracked_runs'] >= 1
        assert _registered(_circle(prev), _circle(b), 1.5,
                           lambda q: math.hypot(q[0] - 480, q[1] - 180) > 40 and q[0] > 420), j
        prev = b


def test_tracked_pair_insertion_is_local():
    """A count change carried through Z inserts a PAIR in one gap; every
    other contact keeps its phase."""
    q = [k / 8 for k in range(9)]
    out = WL._adapt_phases(q, 10, pick=3)
    assert len(out) == 11
    moved = [x for x in q if min(abs(x - y) for y in out) > 1e-9]
    assert len(moved) <= 6                                    # only the neighbourhood of the insertion
    assert out[0] == 0.0 and out[-1] == 1.0 and out == sorted(out)


def test_zigzag_and_wave_are_unchanged_by_the_truss_parameters():
    rings = line_rings()
    a = WL.plan(rings, 20, 'zigzag', 0, True, None, contact=C, closed=True, bead=3.0)
    b = WL.plan(rings, 20, 'zigzag', 0, True, None, contact=C, closed=True, bead=3.0,
                truss={'seed': 99, 'brace_angle': 70})
    assert key(a.polylines) == key(b.polylines)


# --- UI / API ------------------------------------------------------------------------

def test_api_lists_adaptive_truss_with_its_own_parameters():
    import app as A
    rows = A.app.test_client().get('/api/infill_patterns').get_json()
    t = next(r for r in rows if r['name'] == 'truss')
    assert [p['name'] for p in t['parameters']] == ['brace_angle', 'bond', 'max_span', 'turn_radius']
    assert {p['name']: p['default'] for p in t['parameters']} == \
        {'brace_angle': 45.0, 'bond': 3.0, 'max_span': 40.0, 'turn_radius': 1.5}
    z = next(r for r in rows if r['name'] == 'zigzag')
    assert [p['name'] for p in z['parameters']] == ['spacing']           # zigzag / wave unchanged


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_truss_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_truss_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI TRUSS SMOKE PASSED' in proc.stdout

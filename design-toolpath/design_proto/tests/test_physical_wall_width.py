"""
PHYSICAL WALL WIDTH (2026-10-16 contract): Wall Thickness is the FINISHED
DEPOSITED width — outside bead edge to outside bead edge — never the
toolpath-centreline spacing. The architectural envelope stays exactly the
Wall Thickness; every construction places its bead CENTRELINES inside it, the
outermost half a bead inside each finished face.

Measured on the generated geometry (cross-sections across the middle of a
straight wall), not assumed: deposited = outermost centreline span + W.

  * Hollow / Skin + Web: the two skins W / 2 inside the envelope faces
    (7 in wall, 3 in bead → skin centrelines 4 in apart).
  * Parallel / Interleaved / Linked / Chained Loop: already ½ bead inside
    their faces (unchanged).
  * THIN Out-and-Back: 2W − Parallel Wall Overlap (the stored
    `return_overlap`), lanes W − overlap apart.
  * Skin + Web web → skin landings keep Contact Overlap: W − O from the
    corrected skin centrelines.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from model import Vec2, PrintLayer, LinePath, RectanglePath   # noqa: E402
from material import MaterialSpec                             # noqa: E402
from canon import thick, system as sysrec                     # noqa: E402

W, O, P_OV, T = 3.0, 0.75, 0.75, 7.0
X0, X1 = 40.0, 320.0                 # the straight wall, along y = 200
STATIONS = [100.0 + 2.5 * k for k in range(64)]   # mid span, away from the caps

CONSTRUCTIONS = {
    'hollow': {'type': 'hollow'},
    'skin_web': {'type': 'skin_web'},
    'parallel3': {'type': 'parallel', 'walls': 3},
    'parallel2': {'type': 'parallel', 'walls': 2},
    'interleaved': {'type': 'interleaved'},
    'linked': {'type': 'linked'},
    'chain': {'type': 'chain'},
}
# sampled waves / loops reach their extremes only between samples
TOL = {'hollow': 1e-6, 'skin_web': 1e-6, 'parallel3': 1e-6, 'parallel2': 1e-6,
       'interleaved': 0.05, 'linked': 0.05, 'chain': 0.05}


def _mat(**kw):
    m = dict(bead_width=W, contact_overlap=O, return_overlap=P_OV, physical=True)
    m.update(kw)
    return MaterialSpec(**m)


def _layer(spec, align='center', kind='line', t=T, web=None, **mat):
    P = LinePath(Vec2(X0, 200), Vec2(X1, 200), id='W') if kind == 'line' \
        else RectanglePath(X0, 100, X1 - X0, 200, id='W')        # bottom edge y = 100, CCW
    if spec is None:
        ws = []
    elif spec['type'] == 'hollow':
        ws = [thick('W', t, align=align)]
    else:
        ws = [sysrec('W', t, spec, align=align, web=web or {})]
    L = PrintLayer('t', source_paths=[P], wall_systems=ws)
    L.material = _mat(**mat)
    if spec is None:
        L.return_paths = True
        L.prefer_closed = True
    return L


def _crossings(paths, x, keep=lambda p: True):
    ys = []
    for p in paths:
        if not keep(p):
            continue
        q = [(v.x, v.y) for v in p.sample_points()]
        if getattr(p, 'closed', False) and q:
            q = q + [q[0]]
        for a, b in zip(q, q[1:]):
            if a[0] == b[0] or (a[0] - x) * (b[0] - x) > 0:
                continue
            f = (x - a[0]) / (b[0] - a[0])
            ys.append(a[1] + f * (b[1] - a[1]))
    return sorted(ys)


def _extent(paths, y_ref, lower_only=False):
    """(lowest, highest) printed centreline offset from y_ref over the stations."""
    lo, hi = [], []
    for x in STATIONS:
        ys = _crossings(paths, x)
        if lower_only:
            ys = [y for y in ys if y < y_ref + 50]
        assert ys, f'no printed path crosses x = {x}'
        lo.append(ys[0] - y_ref)
        hi.append(ys[-1] - y_ref)
    return min(lo), max(hi)


def deposited(paths, y_ref=200.0, lower_only=False):
    lo, hi = _extent(paths, y_ref, lower_only)
    return (hi - lo) + W, lo - W / 2, hi + W / 2      # width, finished faces (offsets)


@pytest.mark.parametrize('name', list(CONSTRUCTIONS))
@pytest.mark.parametrize('align', ['center', 'left', 'right'])
def test_explicit_wall_deposits_its_wall_thickness(name, align):
    paths, _ = _layer(CONSTRUCTIONS[name], align)._build_effective()
    width, f_lo, f_hi = deposited(paths)
    assert math.isclose(width, T, abs_tol=TOL[name]), (name, align, width)
    # the finished faces are the ENVELOPE's faces (the authored alignment kept)
    want = {'center': (-T / 2, T / 2), 'left': (0.0, T), 'right': (-T, 0.0)}[align]
    assert math.isclose(f_lo, want[0], abs_tol=TOL[name] + 1e-6), (f_lo, want)
    assert math.isclose(f_hi, want[1], abs_tol=TOL[name] + 1e-6), (f_hi, want)


@pytest.mark.parametrize('name', ['hollow', 'skin_web', 'parallel3', 'interleaved'])
@pytest.mark.parametrize('align', ['center', 'inside', 'outside'])
def test_closed_wall_deposits_its_wall_thickness(name, align):
    paths, _ = _layer(CONSTRUCTIONS[name], align, kind='rect')._build_effective()
    width, f_lo, f_hi = deposited(paths, y_ref=100.0, lower_only=True)
    assert math.isclose(width, T, abs_tol=TOL[name]), (name, align, width)
    want = {'center': (-T / 2, T / 2), 'inside': (0.0, T), 'outside': (-T, 0.0)}[align]
    assert math.isclose(f_lo, want[0], abs_tol=TOL[name] + 1e-6)
    assert math.isclose(f_hi, want[1], abs_tol=TOL[name] + 1e-6)


def test_hollow_skin_centrelines_are_T_minus_W_apart():
    paths, _ = _layer(CONSTRUCTIONS['hollow'])._build_effective()
    for x in STATIONS:
        ys = _crossings(paths, x)
        assert len(ys) == 2
        assert math.isclose(ys[1] - ys[0], T - W, abs_tol=1e-6)       # 4 in
        assert math.isclose(ys[0] + ys[1], 400.0, abs_tol=1e-6)       # centred on the path


@pytest.mark.parametrize('t', [6.0, 7.0, 10.0, 12.5])
@pytest.mark.parametrize('bead', [2.5, 3.0, 3.5])
def test_hollow_width_over_thickness_and_bead(t, bead):
    paths, _ = _layer(CONSTRUCTIONS['hollow'], t=t, bead_width=bead)._build_effective()
    lo, hi = _extent(paths, 200.0)
    assert math.isclose((hi - lo) + bead, t, abs_tol=1e-6)


def test_envelope_stays_the_wall_thickness():
    """The ARCHITECTURAL envelope (Designer / Assembly) is unchanged: its
    material ring is T wide; only the printed centrelines moved inside it."""
    for align, want in (('center', [200.0 - T / 2, 200.0 + T / 2]), ('left', [200.0, 200.0 + T])):
        L = _layer(CONSTRUCTIONS['hollow'], align)
        L.envelope_only = True
        paths, _ = L._build_effective()
        assert [round(y, 6) for y in _crossings(paths, 180.0)] == want


@pytest.mark.parametrize('t', [8.0, 10.0, 12.0])
@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_skin_web_web_lands_contact_overlap_off_the_corrected_skins(pattern, t):
    """(A web keeps W − O off BOTH skins only where their centrelines are at
    least 2 (W − O) apart: T ≥ 3W − 2O = 7.5 in here. A 7 in Skin + Web wall —
    skins 4 in apart, 1 in between their beads — cannot: the lattice's
    existing clearance limit, unchanged, relative to the skins it is given.)"""
    paths, _ = _layer(CONSTRUCTIONS['skin_web'], t=t, web={'pattern': pattern})._build_effective()
    width, _, _ = deposited(paths)
    assert math.isclose(width, t, abs_tol=1e-6)
    skins = [p for p in paths if str(getattr(p, 'treatment_id', '')).startswith('W.wall')]
    web = [p for p in paths if getattr(p, 'role', None) == 'lattice']
    assert len(skins) == 2 and web
    skin_y = sorted(p.sample_points()[0].y for p in skins)
    assert math.isclose(skin_y[1] - skin_y[0], t - W, abs_tol=1e-6)
    d_min = float('inf')
    joins = [q.x for p in web for q in p.sample_points() if min(abs(q.y - y) for y in skin_y) < 1e-6]
    for p in web:
        for q in p.sample_points():
            d = min(abs(q.y - y) for y in skin_y)
            if 110 < q.x < 250 and all(abs(q.x - x) > 12.0 for x in joins):
                d_min = min(d_min, d)
    # the web's landings stop W − O from the skin centreline (never closer;
    # the route's TRANSFER — exactly on a skin — excepted: since 2026-10-18 it
    # sits on the station deepest inside its run, so it can lie mid-wall:
    # the stitches into it — a cell either side — are excepted too)
    assert math.isclose(d_min, W - O, abs_tol=0.02), d_min


@pytest.mark.parametrize('ov', [0.0, 0.75, 1.25])
def test_thin_out_and_back_is_2W_minus_parallel_wall_overlap(ov):
    paths, meta = _layer(None, return_overlap=ov)._build_effective()
    assert meta['network']['return_lanes'] == ['W']
    for x in STATIONS:
        ys = _crossings(paths, x)
        assert len(ys) == 2 and math.isclose(ys[1] - ys[0], W - ov, abs_tol=1e-6)
    width, _, _ = deposited(paths)
    assert math.isclose(width, 2 * W - ov, abs_tol=1e-6)


def test_thin_controlled_example_5_25():
    width, _, _ = deposited(_layer(None)._build_effective()[0])
    assert math.isclose(width, 5.25, abs_tol=1e-9)


def test_parallel_lanes_never_exceed_the_parallel_wall_overlap():
    """Parallel Walls distribute their N lanes across the finished envelope
    (outer lanes ½ bead inside the faces). The overlap of neighbouring lanes
    is W − spacing; it must stay within the Parallel Wall Overlap where the
    lane count allows it (a wall thick enough for its lanes)."""
    for t, n in ((7.0, 2), (10.0, 3), (12.0, 4)):
        paths, _ = _layer({'type': 'parallel', 'walls': n}, t=t)._build_effective()
        ys = _crossings(paths, 110.0)          # (away from the lane-change seam)
        assert len(ys) == n
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        assert all(math.isclose(g, (t - W) / (n - 1), abs_tol=1e-6) for g in gaps)
        assert all(W - g <= P_OV + 1e-9 for g in gaps), (t, n, gaps)

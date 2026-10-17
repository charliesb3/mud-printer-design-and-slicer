"""
SKIN + WEB SINGLE-LAYER CORRECTION (Pass A, 2026-10-16).

1. CONTINUOUS CURVES STAY CURVES. A circle has no corners at any radius,
   an open arc gets none near its ends, and a lobed wall keeps one corner
   classification under small geometric changes. The detection is shared
   (wall_lattice.corner_stations) by the lattice and wall_systems: unwrapped
   heading, same-sign turn readings, flanks clipped at run ends / junction
   kinks (never skipped). Sharp corners are unchanged.
2. TARGET WEB ANGLE. A Skin + Web web's pitch is derived from the cavity
   between the contact rails: S = w · cot(angle). A straight wall's
   stitches rise at the angle; a thicker cavity gives a longer pitch at
   the same angle. A legacy stored `spacing` no longer drives a Skin + Web
   web; unowned wall infill keeps Target Spacing.
(No cross-Z behaviour here — Pass B.)
"""
import copy
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import wall_lattice as WL                                                # noqa: E402
from model import (Vec2, PrintLayer, LinePath, CirclePath, RectanglePath,   # noqa: E402
                   ExplicitPath, RegionInfill)
from material import MaterialSpec                                         # noqa: E402
from canon import system as sysrec, layer as canon_layer                  # noqa: E402

W, O = 3.0, 0.75
CONTACT = W - O


def _mat():
    return MaterialSpec(bead_width=W, contact_overlap=O, return_overlap=0.75, physical=True)


def _skin_web(path, t, pattern='zigzag', params=None):
    L = PrintLayer('t', source_paths=[path],
                   wall_systems=[sysrec(path.id, t, {'type': 'skin_web'}, align='center',
                                        web={'pattern': pattern, 'params': dict(params or {})})])
    L.material = _mat()
    return L


def _system(path, t, spec):
    L = PrintLayer('t', source_paths=[path], wall_systems=[sysrec(path.id, t, spec, align='center')])
    L.material = _mat()
    return L


def _regions(meta):
    return [r for v in (meta['network'].get('lattice') or {}).values() for r in v['regions']]


def _corners(meta):
    return sum(run.get('corners', 0) for r in _regions(meta) for run in r.get('runs', []))


def _web(paths):
    return [p.sample_points() for p in paths if getattr(p, 'role', None) == 'lattice']


def _crossings(polys):
    """Proper crossings between non-adjacent segments of the web polylines."""
    segs = [(a, b, i, k) for i, pl in enumerate(polys) for k, (a, b) in enumerate(zip(pl, pl[1:]))
            if a.dist(b) > 1e-9]

    def orient(p, q, r):
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
    n = 0
    for x in range(len(segs)):
        a, b, i, k = segs[x]
        for y in range(x + 1, len(segs)):
            c, d, j, m = segs[y]
            if i == j and abs(k - m) <= 1:
                continue
            o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
            if o1 * o2 < -1e-9 and o3 * o4 < -1e-9:
                n += 1
    return n


def _hairpins(polys, deg=170.0, box=None):
    """Consecutive web segments that double back on themselves (box: only
    there, (x0, y0, x1, y1))."""
    n = 0
    for pl in polys:
        q = [p for k, p in enumerate(pl) if (k == 0 or p.dist(pl[k - 1]) > 1e-6) and
             (box is None or box[0] < p.x < box[2] and box[1] < p.y < box[3])]
        for a, b, c in zip(q, q[1:], q[2:]):
            u, v = b - a, c - b
            lu, lv = u.length(), v.length()
            if lu > 0.5 and lv > 0.5 and (u.x * v.x + u.y * v.y) / (lu * lv) < math.cos(math.radians(deg)):
                n += 1
    return n


# -- 1. corner detection: the shared rule on analytic centre lines ----------

def _circle_centre(R):
    L = 2 * math.pi * R
    return (lambda s: Vec2(R * math.cos(s / R), R * math.sin(s / R))), L


@pytest.mark.parametrize('R_over_t', [0.6, 0.8, 1.0, 1.2, 1.4, 1.5, 1.59, 1.7, 2.0, 2.5, 3.0, 3.82, 5.0, 8.0])
def test_A_analytic_circle_has_no_corners_at_any_radius(R_over_t):
    th = 9.0
    centre, L = _circle_centre(R_over_t * th)
    found, _ = WL.corner_stations(centre, L, True, th, 0.0, L, 0.75 * th)
    assert found == []


def test_A_unwrapped_heading_counts_the_whole_ring():
    """(the old ±180° wrap: a 6 t window round a tight ring read as a small
    turn, making the circle look 'concentrated')"""
    centre, L = _circle_centre(10.0)
    _, heading = WL.corner_stations(centre, L, True, 9.0, 0.0, L, 0.0)
    assert math.isclose(abs(heading(L) - heading(0.0)), 2 * math.pi, abs_tol=1e-6)
    assert math.isclose(abs(heading(L + 5.0) - heading(5.0)), 2 * math.pi, abs_tol=1e-6)


@pytest.mark.parametrize('R,deg', [(12, 90), (15, 180), (20, 270), (30, 180), (45, 120)])
def test_B_analytic_open_arc_has_no_corners_near_its_ends(R, deg):
    th = 9.0
    lead = 30.0                                  # straight leads: the arc ends inside the run too
    arc = math.radians(deg) * R
    L = 2 * lead + arc

    def centre(s):
        if s <= lead:
            return Vec2(R + 0.0, -(lead - s))
        if s >= lead + arc:
            a = math.radians(deg)
            e = Vec2(R * math.cos(a), R * math.sin(a))
            tdir = Vec2(-math.sin(a), math.cos(a))
            return e + tdir * (s - lead - arc)
        a = (s - lead) / R
        return Vec2(R * math.cos(a), R * math.sin(a))
    for lo_hi in ((0.0, L), (lead, lead + arc)):            # ends far / ends right at the arc
        found, _ = WL.corner_stations(centre, lead + arc + lead, False, th, lo_hi[0], lo_hi[1], 0.75 * th)
        # a long gentle arc is a curve; an arc that turns ≤ 3 t of length is a
        # rounded corner (unchanged rule) — only judge the clearly long arcs
        if arc > 4.5 * th:
            assert found == [], (R, deg, lo_hi, found)


def test_sharp_and_tightly_rounded_corners_are_still_corners():
    """Unchanged: a square's sharp corners (and small fillets) are corners."""
    th = 9.0
    side = 100.0

    def square(s, r=0.0):
        s %= 4 * side
        k, u = divmod(s, side)
        pts = [Vec2(0, 0), Vec2(side, 0), Vec2(side, side), Vec2(0, side)]
        a, b = pts[int(k)], pts[(int(k) + 1) % 4]
        return a + (b - a) * (u / side)
    found, _ = WL.corner_stations(square, 4 * side, True, th, 0.0, 4 * side, 0.75 * th)
    # (a sharp corner's turn reading plateaus: the callers keep the first
    # station and drop ties within CORNER_WIN · t, the seam wrapping)
    kept = []
    for x, _t in found:
        if not kept or x - kept[-1] >= WL.CORNER_WIN * th:
            kept.append(x)
    if len(kept) > 1 and kept[0] + 4 * side - kept[-1] < WL.CORNER_WIN * th:
        kept.pop()
    assert len(kept) == 4
    # (a 20 in rectangle fillet at T 10 stays whatever the old 3 t / 6 t test
    # made it: checked on the real build in test_G below)


# -- 1. corner detection on real Skin + Web builds --------------------------

@pytest.mark.parametrize('T', [10, 12, 16])
@pytest.mark.parametrize('R', [8, 10, 12, 14, 16, 20, 24, 30])
def test_A_skin_web_circle_has_zero_corners(T, R):
    """Including the radii that previously produced false corners
    (R ≲ 1.6 × the skin band: e.g. R 8–14 at T 12, R 10–20 at T 16)."""
    _, meta = _skin_web(CirclePath(200, 200, R, id='W'), T)._build_effective()
    assert _corners(meta) == 0


@pytest.mark.parametrize('R,deg', [(15, 180), (20, 270), (30, 180), (45, 180)])
def test_B_skin_web_open_arc_has_no_corners(R, deg):
    pts = [Vec2(200 + R * math.cos(math.radians(a)), 200 + R * math.sin(math.radians(a)))
           for a in [deg * k / 60 for k in range(61)]]
    _, meta = _skin_web(ExplicitPath(pts, id='W'), 12)._build_effective()
    assert _corners(meta) == 0


CX, CY, CR = 24.0, 96.0, 19.556623995902918          # the lobe of the z-coherence building


def _building(T, scale, pattern='zigzag', params=None):
    """A circle merged into a square's corner: a convex lobe arc between two
    concave junction kinks (layer_assembly test_wall_system_z_coherence)."""
    env = {'align': 'center', 'print_reference': False, 'thickness': T}
    doc = {'id': 'design', 'label': '',
           'source_paths': [
               {'id': 'C', 'type': 'CirclePath', 'label': 'Circle', 'closed': True, 'role': 'free',
                'visible': True, 'cx': CX, 'cy': CY, 'radius': CR * scale, 'envelope': dict(env)},
               {'id': 'R', 'type': 'RectanglePath', 'label': 'Square', 'closed': True, 'role': 'free',
                'visible': True, 'x': 18, 'y': 12, 'w': 90, 'h': 90, 'corner_radius': 5, 'envelope': dict(env)}],
           'openings': [],
           'trims': [{'id': 't1', 'source_path_id': 'R', 'start': ['C'], 'end': ['C'], 'inside': {'C': True},
                      'u_mid': 0.75},
                     {'id': 't2', 'source_path_id': 'C', 'start': ['R'], 'end': ['R'], 'inside': {'R': True},
                      'u_mid': 0.875}],
           'route_origins': [], 'region_overrides': [], 'infills': [],
           'constraints': {'start_path_id': None, 'start_t': None, 'reverse_direction': False,
                           'component_order': None},
           'corner_radius': 0, 'cap_style': 'flat', 'cap_corner_radius': 0,
           'junction_style': 'round', 'junction_radius': 7.5, 'junction_overrides': [], 'wall_relations': [],
           'material': {'bead_width': W, 'contact_overlap': O, 'return_overlap': 0.75, 'physical': True},
           'return_paths': True, 'prefer_closed': True}
    systems = [{'id': 'S1', 'name': 'Wall System 1', 'type': 'skin_web', 'params': {}, 'members': ['C', 'R'],
                'web': {'pattern': pattern, 'params': dict(params or {}), 'variation_index': 0}}]
    return canon_layer(doc, systems)


@pytest.mark.parametrize('T', [12, 16])
def test_C_lobed_wall_keeps_its_corner_classification(T):
    """Previously (same fixture): T 12 went 5 → 4 → 3 → 3 → 4 → 3 corners
    and T 16 grew a sliding corner on the lobe arc, as the circle shrank in
    2.5 % steps. Now: the square's three corners + the two junction kinks,
    every step — and no corner on the arc itself."""
    counts = []
    for k in range(7):
        sc = 1.0 - 0.025 * k
        _, meta = _building(T, sc)._build_effective()
        counts.append(_corners(meta))
        for r in _regions(meta):
            for m in r['corner_motifs']['motifs']:
                for q in (m['outer'], m['inner']):
                    d = math.hypot(q[0] - CX, q[1] - CY)
                    # the arc proper: outside the square, away from both kinks
                    on_arc = q[0] < 18 - 1 and q[1] > 102 + 1 and abs(d - CR * sc) < T
                    assert not on_arc, (T, sc, m)
    assert len(set(counts)) == 1, counts


def test_C_concave_kink_motif_does_not_hairpin():
    """A junction kink that a tight arc turns straight back (nearly parallel
    legs): the corner motif's outer wrap fell back on itself. The wrap is
    only used when its curve runs forward; else the straight chord."""
    for sc in (0.85, 0.825):
        paths, _ = _building(16, sc)._build_effective()
        # (round the left kink; the seam corner's transfer hook at the top
        # right is older and unrelated)
        assert _hairpins(_web(paths), box=(0, 55, 45, 105)) == 0


# -- 2. TARGET WEB ANGLE ----------------------------------------------------

def _stitch_angle(paths, x0=120.0, x1=320.0):
    """Median angle (to the wall line) of the web's straight stitches in the
    middle of a wall along y = 200."""
    out = []
    for pl in _web(paths):
        for a, b in zip(pl, pl[1:]):
            if x0 < a.x < x1 and x0 < b.x < x1 and a.dist(b) > 1.0:
                v = abs(math.degrees(math.atan2(b.y - a.y, b.x - a.x))) % 180
                out.append(min(v, 180 - v))
    out.sort()
    return out[len(out) // 2]


def _line(T, **params):
    return _skin_web(LinePath(Vec2(40, 200), Vec2(400, 200), id='W'), T, params=params)


@pytest.mark.parametrize('T', [9, 10, 12, 16, 20])
@pytest.mark.parametrize('angle', [None, 30.0, 45.0])
def test_D_straight_wall_web_rises_at_the_target_angle(T, angle):
    params = {} if angle is None else {'web_angle': angle}
    paths, meta = _line(T, **params)._build_effective()
    want = WL.WEB_ANGLE_DEFAULT if angle is None else angle
    wa = meta['network']['infills'][0]['lattice']['web_angle'][0]
    assert wa['target'] == want and not wa['limited']
    assert math.isclose(wa['cavity'], T - W - 2 * CONTACT, abs_tol=1e-6)     # between the contact rails
    assert abs(_stitch_angle(paths) - want) < 1.0, (T, angle, _stitch_angle(paths))


def test_D_default_angle_is_twenty_degrees():
    """Default = the angle of the long-standing 20 in pitch in a 12 in wall
    measured skin to skin before the physical-width change (cavity 7.5 in,
    20.6°) — the 'ordinary' Skin + Web look."""
    assert WL.WEB_ANGLE_DEFAULT == 20.0
    import infill as IF
    assert IF.web_parameters_for('zigzag')[0]['name'] == 'web_angle'
    assert IF.web_parameters_for('zigzag')[0]['default'] == WL.WEB_ANGLE_DEFAULT
    assert IF.web_parameters_for('truss') == IF.TRUSS_PARAMETERS
    assert IF.parameters_for('zigzag')[0]['name'] == 'spacing'      # (legacy wall infill unchanged)


def test_E_thicker_cavity_longer_pitch_same_angle():
    rows = []
    for T in (10, 12, 14, 16, 20):
        paths, meta = _line(T)._build_effective()
        lat = meta['network']['infills'][0]['lattice']
        rows.append((T, lat['pitch_min'], lat['web_angle'][0]['cavity'], _stitch_angle(paths)))
    pitches = [p for _, p, _, _ in rows]
    assert pitches == sorted(pitches) and pitches[0] < pitches[-1] / 4      # 6.9 … 34 in
    for T, p, w, a in rows:
        # integer stitches per run: the actual pitch is within one stitch of S
        assert abs(p - w / math.tan(math.radians(WL.WEB_ANGLE_DEFAULT))) < 0.1 * p + 0.5, (T, p, w)
        assert abs(a - WL.WEB_ANGLE_DEFAULT) < 1.0, (T, a)


def test_E_gradual_cavity_change_gradual_pitch():
    prev = None
    for k in range(9):
        T = 12.0 + 0.25 * k
        lat = _line(T)._build_effective()[1]['network']['infills'][0]['lattice']
        S = lat['web_angle'][0]['pitch']
        if prev is not None:
            assert 0 < S - prev < 1.0                  # 0.25 in thicker → ≈ 0.69 in longer
        prev = S


def test_web_pitch_formula_and_floor():
    S, w, lim = WL.web_pitch(9.0, CONTACT, W, 20.0)
    assert math.isclose(w, 4.5) and math.isclose(S, 4.5 / math.tan(math.radians(20.0))) and not lim
    # a cavity barely wider than nothing: landings on one skin stay a bead apart
    S, w, lim = WL.web_pitch(5.0, CONTACT, W, 20.0)
    assert math.isclose(w, 0.5) and S == 0.5 * W and lim
    # no cavity at all (rails meet): the floor, never a crash
    S, w, lim = WL.web_pitch(4.0, CONTACT, W, 45.0)
    assert w == 0.0 and S == 0.5 * W and lim


def test_thin_skin_web_still_builds_with_a_web():
    """No arbitrary minimum thickness: 8 in (cavity 0.5 in) still gets a web,
    angle-limited by the bead floor and reported as such."""
    paths, meta = _line(8)._build_effective()
    wa = meta['network']['infills'][0]['lattice']['web_angle'][0]
    assert _web(paths) and wa['limited'] and wa['pitch'] == 0.5 * W


def test_web_landings_keep_contact_overlap():
    """Web → skin landings still stop W − O off the skin centrelines."""
    for T in (10, 12, 16):
        paths, _ = _line(T, web_angle=35.0)._build_effective()
        skins = [p for p in paths if str(getattr(p, 'treatment_id', '')).startswith('W.wall')]
        skin_y = sorted(p.sample_points()[0].y for p in skins)
        d_min = min(min(abs(q.y - y) for y in skin_y)
                    for pl in _web(paths) for q in pl if 120 < q.x < 320)
        assert math.isclose(d_min, CONTACT, abs_tol=0.02), (T, d_min)


@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
@pytest.mark.parametrize('R,T', [(12, 12), (16, 12), (16, 10), (20, 16)])
def test_F_tight_circle_is_a_continuous_curve_solution(R, T, pattern):
    """No corner anchors, one even pitch all round, no self-crossing web."""
    paths, meta = _skin_web(CirclePath(200, 200, R, id='W'), T, pattern)._build_effective()
    assert _corners(meta) == 0
    lat = meta['network']['infills'][0]['lattice']
    assert lat['pitch_max'] - lat['pitch_min'] < 0.02 * lat['pitch_max'] + 1e-6
    web = _web(paths)
    assert web and _crossings(web) == 0 and _hairpins(web) == 0


# -- saved-project compatibility --------------------------------------------

def test_legacy_spacing_on_a_skin_web_web_is_ignored():
    """Saved projects keep loading: an old Skin + Web web stored `spacing`
    (every project did: 20); it no longer drives the web — the default
    Target Web Angle does — and the stored value is left untouched."""
    a = _line(12, spacing=20)._build_effective()
    b = _line(12, spacing=45)._build_effective()
    c = _line(12)._build_effective()
    sig = lambda r: [[(round(q.x, 6), round(q.y, 6)) for q in pl] for pl in _web(r[0])]
    assert sig(a) == sig(b) == sig(c)
    L = _line(12, spacing=45)
    L._build_effective()
    assert L.wall_systems[0].web['params'] == {'spacing': 45}


def test_unowned_wall_infill_keeps_target_spacing():
    """Legacy (unowned) wall infill is not Skin + Web: Target Spacing as before."""
    from canon import thick
    L = PrintLayer('t', source_paths=[LinePath(Vec2(40, 200), Vec2(400, 200), id='W')],
                   wall_systems=[thick('W', 12)],
                   infills=[RegionInfill('I', 'W', 'zigzag', {'spacing': 30.0})])
    L.material = _mat()
    _, meta = L._build_effective()
    lat = meta['network']['infills'][0]['lattice']
    assert lat['web_angle'] is None and abs(lat['pitch_min'] - 30.0) < 3.0


def test_project_web_angle_round_trips_through_the_app():
    """A Wall System's web {web_angle} reaches the build (composed layer)."""
    for ang in (20.0, 40.0):
        _, meta = _building(12, 1.0, params={'web_angle': ang})._build_effective()
        lat = meta['network']['infills'][0]['lattice']
        assert lat['web_angle'][0]['target'] == ang


# -- G. the other Wall Systems: only the false-corner cases change ----------

@pytest.mark.parametrize('spec', [{'type': 'interleaved'}, {'type': 'linked'}])
def test_G_generated_systems_keep_real_corners(spec):
    """Interleaved / Linked use the shared detection: a sharp rectangle keeps
    its 4 corners, a straight wall has none — unchanged."""
    _, meta = _system(RectanglePath(40, 100, 280, 200, id='W'), 12, spec)._build_effective()
    assert meta['network']['wall_systems'][0]['corners'] == 4
    _, meta = _system(LinePath(Vec2(40, 200), Vec2(320, 200), id='W'), 12, spec)._build_effective()
    assert meta['network']['wall_systems'][0]['corners'] == 0


@pytest.mark.parametrize('spec', [{'type': 'interleaved'}, {'type': 'linked'}])
@pytest.mark.parametrize('R', [12, 16, 40])
def test_G_generated_systems_no_false_corners_on_circles(spec, R):
    """(R 12 / 16 at T 12 had false corner zones — the shared bug; R 40 never did)"""
    _, meta = _system(CirclePath(200, 200, R, id='W'), 12, spec)._build_effective()
    assert meta['network']['wall_systems'][0]['corners'] == 0


# -- 3. curves: the CONCAVE skin owns the Target Web Angle ------------------
# A brace leaves the concave (inside) contact rail at the angle to that
# rail's LOCAL tangent and runs straight to the convex rail; the convex
# landing and its angle follow. Straight walls are unchanged; gentle curves
# converge to them.

def _reviewed_rule():
    """The reviewed Pass A station / stitch rules (no concave rule)."""
    class _Off:
        def __enter__(self):
            self.prev = WL._Geo.__dict__.get('web')
            WL._Geo.web = property(lambda s_: None, lambda s_, v: None)
            return self

        def __exit__(self, *a):
            del WL._Geo.web
            if self.prev is not None:
                WL._Geo.web = self.prev
    return _Off()


def _ring(R, T, pattern='zigzag', angle=None):
    return _skin_web(CirclePath(200, 200, R, id='W'), T, pattern,
                     None if angle is None else {'web_angle': angle})


def _ring_landings(paths, R, T):
    """Per stitch of a ring web: (concave departure °, convex arrival °,
    concave landing angle, convex landing angle) — rail landings only
    (the exact-face transfer landings are skipped)."""
    ri, ro = R - (T - W) / 2 + CONTACT, R + (T - W) / 2 - CONTACT
    out = []
    for pl in _web(paths):
        pol = [(math.hypot(q.x - 200, q.y - 200), math.atan2(q.y - 200, q.x - 200), q) for q in pl]
        for (r0, a0, p0), (r1, a1, p1) in zip(pol, pol[1:]):
            on = lambda r_, rr: abs(r_ - rr) < 0.05
            if not ((on(r0, ri) and on(r1, ro)) or (on(r0, ro) and on(r1, ri))):
                continue
            (rc, ac, pc), (rv, av, pv) = ((r0, a0, p0), (r1, a1, p1)) if on(r0, ri) else ((r1, a1, p1), (r0, a0, p0))
            v = pv - pc
            ang = lambda p_, d_: math.degrees(math.acos(min(1.0, abs(
                (d_.x * -(p_.y - 200) + d_.y * (p_.x - 200)) / (d_.length() * math.hypot(p_.x - 200, p_.y - 200))))))
            out.append((ang(pc, v), ang(pv, v), ac, av))
    return out


@pytest.mark.parametrize('T', [10, 12, 16])
@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_concave_rule_leaves_straight_walls_unchanged(T, pattern):
    """Byte-for-byte the reviewed straight-wall web (and a sharp rectangle:
    its sides and corner motifs)."""
    for mk in (lambda: _line(T) if pattern == 'zigzag' else
               _skin_web(LinePath(Vec2(40, 200), Vec2(400, 200), id='W'), T, 'wave'),
               lambda: _skin_web(RectanglePath(40, 100, 280, 200, id='W'), T, pattern)):
        new = [[(q.x, q.y) for q in pl] for pl in _web(mk()._build_effective()[0])]
        with _reviewed_rule():
            old = [[(q.x, q.y) for q in pl] for pl in _web(mk()._build_effective()[0])]
        assert new == old


def test_gentle_curve_converges_to_the_straight_wall():
    """The derived centre-line pitch tends to the straight-wall pitch S as
    the radius grows (the convex rail's bow over one brace vanishes)."""
    S = WL.web_pitch(12.0 - W, CONTACT, W, WL.WEB_ANGLE_DEFAULT)[0]
    errs = []
    for R in (200, 600, 2000):
        meta = _ring(R, 12)._build_effective()[1]
        # (the ring's loop run; a huge ring's skeleton may add a zero-length fragment)
        run = max((r_ for r in _regions(meta) for r_ in r['runs']), key=lambda r_: r_['length'])
        errs.append(abs(run['pitch'] - S) / S)
    assert errs == sorted(errs, reverse=True), errs
    assert errs[-1] < 0.015, errs


@pytest.mark.parametrize('R', [10, 12, 16, 24, 40])
@pytest.mark.parametrize('angle', [20.0, 30.0])
def test_concave_departure_keeps_the_target_angle_on_tighter_circles(R, angle):
    paths, meta = _ring(R, 12, angle=angle)._build_effective()
    st = _ring_landings(paths, R, 12)
    dep = sorted(d for d, _, _, _ in st)
    assert len(dep) >= 6
    # (a loop takes an EVEN whole number of stitches: with only 8–10 round a
    # very tight ring that rounding alone moves the angle a few degrees)
    tol = 4.0 if R <= 12 else 2.5
    assert abs(dep[len(dep) // 2] - angle) < tol, (R, angle, dep)
    assert sum(abs(d - angle) < tol + 1.0 for d in dep) >= 0.8 * len(dep), (R, angle, dep)


def test_convex_landing_adapts_instead_of_equal_spacing():
    """The convex arrival steepens and the convex landings spread out as the
    circle tightens; the concave departure does not change."""
    rows = []
    for R in (40, 24, 16, 12):
        st = _ring_landings(_ring(R, 12)._build_effective()[0], R, 12)
        dep = sorted(d for d, _, _, _ in st)[len(st) // 2]
        arr = sorted(a for _, a, _, _ in st)[len(st) // 2]
        rows.append((R, dep, arr))
        assert arr > dep + 5.0, (R, dep, arr)          # NOT forced to the target on the convex side
    arrs = [a for _, _, a in rows]
    assert arrs == sorted(arrs), rows                  # tighter → steeper convex arrival
    assert max(d for _, d, _ in rows) - min(d for _, d, _ in rows) < 3.0, rows
    # landing spacing per skin (arc length): the convex skin's is longer
    R, T = 16, 12
    ri, ro = R - (T - W) / 2 + CONTACT, R + (T - W) / 2 - CONTACT
    st = _ring_landings(_ring(R, T)._build_effective()[0], R, T)
    gap = lambda angs, rr: sorted(((b - a) % (2 * math.pi)) * rr for a, b in zip(angs, angs[1:] + angs[:1]))
    ci = sorted({round(c, 6) for _, _, c, _ in st})
    co = sorted({round(v, 6) for _, _, _, v in st})
    gi, go = gap(ci, ri), gap(co, ro)
    assert go[len(go) // 2] > 1.15 * gi[len(gi) // 2], (gi, go)


@pytest.mark.parametrize('R', [10, 12, 16, 24])
@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_tight_circle_web_is_ordered_continuous_and_uncrossed(R, pattern):
    paths, meta = _ring(R, 12, pattern)._build_effective()
    web = _web(paths)
    assert len(web) == 1 and web[0][0].dist(web[0][-1]) < 1e-6         # one closed pass
    assert _crossings(web) == 0 and _hairpins(web) == 0
    assert _corners(meta) == 0
    if pattern == 'wave':
        return                     # (wave samples run along the rails: the landing order is the zigzag's)
    # landings progress round the ring evenly (no seam / phase reset)
    ri, ro = R - (12 - W) / 2 + CONTACT, R + (12 - W) / 2 - CONTACT
    ang = [math.atan2(q.y - 200, q.x - 200) for q in web[0]
           if min(abs(math.hypot(q.x - 200, q.y - 200) - rr) for rr in (ri, ro, ri - CONTACT, ro + CONTACT)) < 0.05]
    steps = [(b - a) % (2 * math.pi) for a, b in zip(ang, ang[1:])]
    steps = [x if x < math.pi else x - 2 * math.pi for x in steps]
    sgn = 1 if sum(steps) > 0 else -1
    steps = [sgn * x for x in steps]
    assert min(steps) > 0 and max(steps) < 1.15 * min(steps), [round(math.degrees(x), 1) for x in steps]


def test_s_curve_changes_concave_side_continuously():
    """Where the curvature changes sign the concave skin changes side: the
    landings progress without a reset or a corner."""
    pts = []
    for k in range(121):
        x = 40 + 3.0 * k
        pts.append(Vec2(x, 200 + 45 * math.sin((x - 40) / 360 * 2 * math.pi)))
    paths, meta = _skin_web(ExplicitPath(pts, id='W'), 12)._build_effective()
    web = _web(paths)
    assert web and _corners(meta) == 0 and _hairpins(web) == 0
    for pl in web:
        # a lone wall is ONE closed out-and-back: away from the caps, x runs
        # one way and then the other — a single reversal, at the far cap
        xs = [q.x for q in pl if 80 < q.x < 360]
        d = [1 if b > a else -1 for a, b in zip(xs, xs[1:]) if abs(b - a) > 1e-6]
        flips = sum(1 for a, b in zip(d, d[1:]) if a != b)
        assert flips <= 2, flips

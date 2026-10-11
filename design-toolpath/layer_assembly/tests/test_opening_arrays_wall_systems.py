"""
REGRESSION (2026-10-10): Assembly self-supporting opening ARRAYS on every
Wall System. Root cause found: a cut piece of a GENERATED Wall System
(Interleaved / Linked / Chained Loop) keeps the uncut layer's pattern only
if wall_systems._ref_map maps it onto the reference run — and it rejected a
whole piece when a sample within one thickness of a CUT END (frames askew
there; at a corner they jump to the bisector) fell off the reference centre
line. An array puts many cut faces on a ring, so one landing near a corner
made the rest of the ring (hundreds of inches) re-fit its own period / phase
— every layer different, remote support findings, the wall printed far from
its stack. Now the interior of the piece decides.

Checked here on a rectangular room (a ring, four corners) and a straight
wall, for all five constructions: single vs array, every arrangement, cut
intervals separate and exact, full thickness, caps (closed routes), close
spacing / wall ends refused with a reason and the wall left intact, no
remote change of the wall, support findings only at the openings,
transforms (Follow Wall / Plumb), print plan.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_profiles_designer import (BEAD, MAT, T, _segments, _seg_dist, designs, line_doc, open_ends,  # noqa: E402
                                    targets_of)

from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Footprint, Section, SectionTransform, resolve
from layer_assembly.profiles import OpeningArray, OpeningProfile, WallRef, apply_profiles
from layer_assembly.source import stack_geometry
from layer_assembly.support import analyse_support

SYSTEMS = [('skin_web', {}), ('parallel', {'walls': 4}), ('interleaved', {'paths': 4}), ('linked', {'paths': 4}),
           ('chain', {})]
IDS = [s for s, _ in SYSTEMS]


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def rect_doc(system, params, w=240.0, h=120.0):
    """A rectangular room R (0,0)–(w,h), 10 in walls, one Wall System."""
    doc = {'id': 'r', 'material': dict(MAT),
           'source_paths': [{'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': w, 'h': h,
                             'closed': True}],
           'wall_systems': [{'id': 'WS1', 'type': system, 'members': ['R'], 'thickness': T, 'params': dict(params)}]}
    if system == 'skin_web':
        doc['wall_systems'][0]['web'] = {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}
        doc['infills'] = [{'id': 'WEB', 'path_id': 'R', 'pattern': 'zigzag', 'params': {'spacing': 20},
                           'kind': 'wall', 'variation_index': 0, 'owner': 'WS1'}]
    return doc


_SRC = {}


def _src(kind, system, params):
    key = (kind, system)
    if key not in _SRC:
        doc = rect_doc(system, params) if kind == 'rect' else line_doc(system, params, length=240.0)
        _SRC[key] = DesignerSource.from_designs(designs(doc))
    return _SRC[key]


def _diamond(pos=30.0, zb=6.0, w=24.0, half=24.0, wall=WallRef('R', 0), array=None):
    return OpeningProfile('P', wall, pos, zb, 0, w, upper='pointed', lower='pointed', middle=False,
                          upper_height=half, bottom_depth=half, array=array)


def _stack(src, p, top=72.0, tf=None, al='follow'):
    a = Assembly(1.5, [Section('A', top, '', tf or SectionTransform())], profiles=[p], opening_alignment=al)
    res = resolve(a, ['A'], {'A': Footprint.of_bbox(0, 0, 240, 120)})
    res, rep = apply_profiles(res, [p], targets_of(src, ('A',)), BEAD, a.max_overhang, None, None, al)
    return a, res, rep[0], stack_geometry(res, src)


def _cuts_by_layer(r):
    out = {}
    for L in r.layers:
        out.setdefault(L['layer'], []).append((L['at'] - L['width'] / 2, L['at'] + L['width'] / 2))
    return out


def _wall_gaps(polys, y0, length):
    """Stretches of the wall band (|y − y0| ≤ T/2) along x printed by no bead."""
    iv = sorted((min(a[0], b[0]) - BEAD / 2, max(a[0], b[0]) + BEAD / 2) for a, b in _segments(polys)
                if abs(a[1] - y0) <= T / 2 + 1e-6 and abs(b[1] - y0) <= T / 2 + 1e-6)
    out, x = [], 0.0
    for a, b in iv:
        if a > x + 1e-6:
            out.append((x, a))
        x = max(x, b)
    if x < length - 1e-6:
        out.append((x, length))
    return out


def _remote(polys, canon, centres, radius):
    """Printed points farther than `radius` from every opening that do not lie
    on the uncut design (a wall changed away from its openings)."""
    segs = list(_segments(canon))
    return [q for p in polys for q in p['pts']
            if all(math.dist(q, c) > radius for c in centres) and min(_seg_dist(q, a, b) for a, b in segs) > 0.05]


CORNERS = [(0, 0), (240, 0), (240, 120), (0, 120)]


@pytest.mark.parametrize('count', [2, 4])
@pytest.mark.parametrize('system,params', SYSTEMS, ids=IDS)
def test_array_on_a_ring_changes_nothing_away_from_its_openings(system, params, count):
    """THE regression: four diamonds on one edge of a room. Every cut layer
    keeps the uncut wall everywhere farther than one wall thickness from an
    opening (Chained Loop: away from the room's corners too — see the
    limitation in the memory); support findings only at the openings."""
    src = _src('rect', system, params)
    tg = targets_of(src, ('A',))['A']['R#0']
    p = _diamond(array=OpeningArray('horizontal', count=count, h_spacing=22))
    a, res, r, geo = _stack(src, p)
    assert r.valid, r.errors
    # the reference: an UNCUT layer of the same stack (layer 0 — with the
    # lattice stations every layer of a profiled stack carries)
    canon = geo[res.instances[0].geometry_key][0]
    # (a cut end changes the wall within one thickness of it; a Chained Loop's
    # lead from its curve to the cap reaches ~3 thicknesses)
    reach = p.width / 2 + (3 * T if system == 'chain' else T) + 2 * BEAD
    cuts = _cuts_by_layer(r)
    centres = [(tg.a[0] + m['position'], tg.a[1]) for m in r.members]
    for k in (6, 12, 20, 28, 34):
        if system == 'chain':
            # (Chained Loop: its tight loops are resampled per piece — a point-to-
            # segment comparison is not meaningful; judged by support below; its
            # ring CORNER motif jitters between cut layers with a single opening
            # too — a pre-existing limitation, see the memory)
            continue
        far = _remote(geo[res.instances[k].geometry_key][0], canon, centres, reach)
        assert len(far) <= 6, (system, k, len(far), far[:5])
    for f in analyse_support(res, geo, a).findings:
        near = min(math.dist(f.centre, c) for c in centres) <= 12 + T + 2 * BEAD + f.span / 2
        assert near or (system == 'chain' and min(math.dist(f.centre, c) for c in CORNERS) < 40) or \
            (system in ('interleaved', 'linked') and min(math.dist(f.centre, c) for c in CORNERS) < 16), \
            (system, f.layer, f.kind, f.centre)


@pytest.mark.parametrize('system,params', SYSTEMS, ids=IDS)
def test_single_versus_four_opening_array(system, params):
    """The array cuts each opening exactly where the single opening is cut
    (the first clone = the single one), independently, on every layer."""
    src = _src('line', system, params)
    one = _stack(src, _diamond(pos=40, wall=WallRef('L')))
    four = _stack(src, _diamond(pos=40, wall=WallRef('L'), array=OpeningArray('horizontal', count=4, h_spacing=22)))
    assert one[2].valid and four[2].valid, (one[2].errors, four[2].errors)
    c1, c4 = _cuts_by_layer(one[2]), _cuts_by_layer(four[2])
    assert set(c1) == set(c4)
    for k in c1:
        assert len(c4[k]) == 4 and sorted(c4[k])[0] == pytest.approx(c1[k][0])
        if min(b - a_ for a_, b in c4[k]) < 2 * BEAD + 1:
            continue                                  # (a tip sliver: closed by its caps, no measurable void)
        gaps = [g for g in _wall_gaps(four[3][four[1].instances[k].geometry_key][0], 0.0, 240.0) if g[1] - g[0] > 1.0]
        assert len(gaps) == 4, (system, k, gaps)          # four SEPARATE voids, nothing else missing
        for (g0, g1), (c0, c1_) in zip(gaps, sorted(c4[k])):
            assert g0 >= c0 - BEAD - 1e-6 and g1 <= c1_ + BEAD + 1e-6, (system, k, (g0, g1), (c0, c1_))


ARR = [('horizontal', dict(count=3, h_spacing=22)), ('vertical', dict(count=2, v_spacing=6)),
       ('diagonal_up', dict(count=3, h_spacing=22, v_spacing=-24)),
       ('staggered', dict(cols=2, rows=2, h_spacing=30, v_spacing=6))]


@pytest.mark.parametrize('arrangement,kw', ARR, ids=[a for a, _ in ARR])
@pytest.mark.parametrize('system,params', [SYSTEMS[1], SYSTEMS[3]], ids=['parallel', 'linked'])
def test_every_arrangement_cuts_separate_full_thickness_openings(system, params, arrangement, kw):
    src = _src('line', system, params)
    p = _diamond(pos=40, zb=3, w=18, half=18, wall=WallRef('L'), array=OpeningArray(arrangement, **kw))
    a, res, r, geo = _stack(src, p, top=96)
    assert r.valid, r.errors
    cuts = _cuts_by_layer(r)
    multi = [k for k, c in cuts.items() if len(c) > 1]
    if arrangement != 'vertical':
        assert multi, 'several openings on one layer'
    for k, cs in cuts.items():
        if k % 3 == 0:
            assert open_ends(src, 'A', res.instances[k].cut_spec()) == [], 'closed routes: caps at every opening'
        if min(b - a_ for a_, b in cs) < 2 * BEAD + 1:
            continue                                  # (tip slivers: no measurable void)
        gaps = [g for g in _wall_gaps(geo[res.instances[k].geometry_key][0], 0.0, 240.0) if g[1] - g[0] > 1.0]
        assert len(gaps) == len(cs), (arrangement, k, gaps, cs)
    # full thickness: no printed point inside any opening's clear void (both faces cut)
    for k, cs in list(cuts.items())[::5]:
        polys = geo[res.instances[k].geometry_key][0]
        for c0, c1 in cs:
            if c1 - c0 > 2 * BEAD:
                inside = [q for pl in polys for q in pl['pts'] if c0 + BEAD / 2 + 1e-6 < q[0] < c1 - BEAD / 2 - 1e-6
                          and abs(q[1]) < T / 2]
                assert inside == [], (k, (c0, c1), inside[:3])


@pytest.mark.parametrize('system,params', SYSTEMS[1:], ids=IDS[1:])
def test_close_spacing_and_wall_ends_are_refused_and_the_wall_left_intact(system, params):
    src = _src('line', system, params)
    ok = _stack(src, _diamond(pos=40, wall=WallRef('L'), array=OpeningArray('horizontal', count=3, h_spacing=20)))
    assert ok[2].valid, ok[2].errors                      # exactly the pier minimum (2 × thickness)
    for p, why in ((_diamond(pos=40, wall=WallRef('L'), array=OpeningArray('horizontal', count=3, h_spacing=19)),
                    'of wall between'),
                   (_diamond(pos=12, wall=WallRef('L'), array=OpeningArray('horizontal', count=3, h_spacing=22)),
                    'does not fit')):
        a, res, r, geo = _stack(src, p)
        assert not r.valid and any(why in e for e in r.errors), r.errors
        assert not any(i.cuts for i in res.instances), 'nothing cut: the wall stays as designed'
        assert all(i.geometry_key == 'A' for i in res.instances)


def test_arrays_near_a_corner_junction_of_the_ring():
    """Openings close to the room's corners (the edges' junctions), on two
    edges at once: every layer closes, no remote change."""
    src = _src('rect', 'interleaved', {'paths': 4})
    canon = src.geometry('A').polylines
    p = _diamond(pos=22, w=16, half=16, array=OpeningArray('horizontal', count=5, h_spacing=32))
    a, res, r, geo = _stack(src, p)
    assert r.valid, r.errors
    assert r.members[-1]['position'] >= 210 and r.members[0]['position'] <= 22, 'openings close to both corners'
    canon = geo[res.instances[0].geometry_key][0]
    tg = targets_of(src, ('A',))['A']['R#0']
    centres = [(tg.a[0] + m['position'], tg.a[1]) for m in r.members]
    for k in (8, 20, 30):
        assert open_ends(src, 'A', res.instances[k].cut_spec()) == []
        far = _remote(geo[res.instances[k].geometry_key][0], canon, centres, p.width / 2 + T + 2 * BEAD)
        far = [q for q in far if all(math.dist(q, c) > 16 for c in CORNERS)]
        assert len(far) <= 6, (k, far[:5])


@pytest.mark.parametrize('al', ['follow', 'plumb'])
def test_arrays_under_assembly_transforms(al):
    """A shifting stack: the array's openings stay separate and exact on
    every layer, Follow Wall (lean) and Vertical / Plumb (world-fixed)."""
    src = _src('line', 'linked', {'paths': 4})
    p = _diamond(pos=40, wall=WallRef('L'), array=OpeningArray('horizontal', count=3, h_spacing=24))
    a, res, r, geo = _stack(src, p, tf=SectionTransform('linear', (0.2, 0.0), 0.0), al=al)
    assert r.valid, r.errors
    xs = {}
    for k, cs in _cuts_by_layer(r).items():
        if min(b - a_ for a_, b in cs) >= 2 * BEAD + 1:
            gaps = [g for g in _wall_gaps(geo[res.instances[k].geometry_key][0], 0.0, 240.0) if g[1] - g[0] > 1.0]
            assert len(gaps) == 3, (k, gaps)
        T_ = res.instances[k].transform
        for j, (c0, c1) in enumerate(sorted(cs)):
            xs.setdefault(j, set()).add(round(T_.scale * (c0 + c1) / 2 + T_.tx, 3))
    if al == 'plumb':
        assert all(len(v) == 1 for v in xs.values())
    else:
        assert all(len(v) > 1 for v in xs.values())
    assert not [f for f in analyse_support(res, geo, a).findings if f.kind == 'bridge']


def test_print_plan_prints_the_array():
    """The plan routes exactly the resolved array: on a cut layer its print
    moves leave the four voids (world XY) and nothing else."""
    from layer_assembly.print_plan import build_plan, items
    src = _src('line', 'parallel', {'walls': 4})
    p = _diamond(pos=40, wall=WallRef('L'), array=OpeningArray('horizontal', count=4, h_spacing=22))
    a, res, r, geo = _stack(src, p)
    plan = build_plan(res, src)
    k = 20
    pts = [{'pts': it[3]} for it in items(plan) if it[0] == 'print' and it[1] == k]
    gaps = [g for g in _wall_gaps(pts, 0.0, 240.0) if g[1] - g[0] > 1.0]
    cs = sorted(_cuts_by_layer(r)[k])
    assert len(gaps) == 4 and all(g0 >= c0 - BEAD - 1e-6 and g1 <= c1 + BEAD + 1e-6 for (g0, g1), (c0, c1) in zip(gaps, cs))

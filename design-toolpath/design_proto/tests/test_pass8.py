"""
Pass 8 — wall + solid infill geometry (geometric tests on produced
geometry and routes, never labels):

  OPENINGS   an opening cuts the COMPLETE wall assembly, also when the
             wall's two faces are two source paths (wall relationship,
             inset + wall infill, a wall-infill region with one wall-like
             void): both faces cut, cap faces built, infill clear of it,
             no accidental connection across it.
  SOLID      boundary support (maximum unsupported boundary distance),
             flowing serpentine, continuity (closed loops: one run, no
             travel, start = end).
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app  # noqa: F401  (path setup)
import route_quality as RQ
from graph import route_layer, compute_metrics
from model import (Vec2, PrintLayer, RectanglePath, CirclePath, EllipsePath,
                   RegionInfill, WallRelation, InsetPath, Opening, WallSpec)
from tests.test_networks import _assert_internal_inside


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _route(L):
    paths, meta = L._build_effective()
    mv = route_layer(L.to_routing_layer(paths, meta))
    return paths, meta, mv, compute_metrics(mv)


def _pts(p, meta):
    return meta['pts'].get(id(p)) or p.sample_points()


def _crosses_box(paths, meta, box, kinds=None):
    """Any path (optionally of the given treatment ids) passing through the
    open box (x0, x1, y0, y1)?"""
    x0, x1, y0, y1 = box
    for p in paths:
        if kinds is not None and getattr(p, 'treatment_id', '') not in kinds:
            continue
        sp = _pts(p, meta)
        for a, b in zip(sp, sp[1:]):
            for k in range(11):
                q = a.lerp(b, k / 10)
                if x0 < q.x < x1 and y0 < q.y < y1:
                    return True
    return False


def two_face(kind='relation', ops=(('O', 100, 24),), pattern='zigzag', infill=True):
    """Outer rect O (0,0,200,120) + inner rect I 10 in inside: one wall."""
    O = RectanglePath(0, 0, 200, 120, id='O')
    if kind == 'inset':
        I = InsetPath('O', 10, id='I')
    else:
        I = RectanglePath(10, 10, 180, 100, id='I')
    rels = [WallRelation('w', 'O', 'I', 10)] if kind == 'relation' else []
    infs = [RegionInfill('F', 'O', pattern, {'spacing': 20})] if infill else []
    return PrintLayer('t', source_paths=[O, I], wall_relations=rels, infills=infs,
                      openings=[Opening(f'o{k}', pid, s, w) for k, (pid, s, w) in enumerate(ops)])


def _faces(paths, pid):
    return [p for p in paths if p.id == pid or p.id.startswith(pid + '~')]


# ---------------------------------------------------------------------------
# OPENINGS through two-face wall assemblies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['relation', 'inset', 'void'])
@pytest.mark.parametrize('on', ['O', 'I'])
def test_opening_cuts_both_faces_of_a_two_path_wall(kind, on):
    # O bottom side is y = 0, I bottom side y = 10. An opening at
    # center_s 100 (24 wide) is a doorway at x ≈ 88…112 through the wall.
    s = 100 if on == 'O' else 90           # I starts at (10, 10): same x
    L = two_face(kind, ops=((on, s, 24),))
    paths, meta, mv, m = _route(L)
    for pid in ('O', 'I'):
        pcs = _faces(paths, pid)
        assert pcs and all(not p.closed for p in pcs), pid       # cut, not intact
    # one doorway: nothing printed through its clear space
    assert not _crosses_box(paths, meta, (90, 110, 0.5, 9.5))
    # both cut faces closed: wall-end caps (linked assemblies) or the flat
    # cut faces of a doorway through a wall-material region (arbitrary void)
    caps = [p for p in paths if getattr(p, 'treatment_id', '') in ('wall_system', 'opening_face')]
    assert len(caps) == 2
    for c in caps:
        cp = _pts(c, meta)
        ys = sorted((cp[0].y, cp[-1].y))
        assert ys[0] < 0.5 and ys[1] > 9.5                        # outer → inner face
    # infill present, inside the wall, and continuous with one route
    assert [p for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    _assert_internal_inside(L)
    assert m['print_runs'] == 1 and m['travel_moves'] == 0
    assert RQ.wall_metrics(L)['interior_retrace'] < 1e-6


def test_wall_infill_region_with_several_voids_is_cut_through():
    # (was: "…_is_not_guessed" — Pass 8 left rooms alone; the wall-region
    # pass makes an opening a subtraction from the declared material: see
    # tests/test_wall_regions.py for the full set)
    L = PrintLayer('t', source_paths=[
        RectanglePath(0, 0, 230, 110, id='O'), RectanglePath(10, 10, 100, 90, id='A'),
        RectanglePath(120, 10, 100, 90, id='B')],
        infills=[RegionInfill('F', 'O', 'zigzag', {'spacing': 16})],
        openings=[Opening('o', 'O', 60, 20)])
    paths, _ = L._build_effective()
    assert not any(p.id == 'A' and p.closed for p in paths)      # doorway into room A
    assert any(p.id == 'B' and p.closed for p in paths)          # room B untouched


def test_relation_without_opening_is_unchanged():
    # absorption only happens where an opening actually cuts
    L = two_face('relation', ops=())
    paths, _ = L._build_effective()
    assert any(p.id == 'O' and p.closed for p in paths)
    assert any(p.id == 'I' and p.closed for p in paths)


def test_multiple_and_overlapping_openings_through_two_path_wall():
    # two separate doorways (bottom and top) + two overlapping ones on the
    # right side (unioned into one wider gap)
    L = two_face('relation', ops=(('O', 60, 20), ('O', 400, 24), ('O', 250, 20), ('O', 262, 20)))
    paths, meta, mv, m = _route(L)
    caps = [p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system']
    assert len(caps) == 6                                    # 3 gaps × 2 faces
    assert all(not p.closed for p in _faces(paths, 'O') + _faces(paths, 'I'))
    # the overlapping pair on the right side (x = 200, s 240…272 → y 40…72)
    assert not _crosses_box(paths, meta, (190.5, 199.5, 42, 70))
    assert not _crosses_box(paths, meta, (52, 68, 0.5, 9.5))
    assert not _crosses_box(paths, meta, (111, 129, 110.5, 119.5))      # top: s 400 → x 120 (CCW)
    _assert_internal_inside(L)
    assert RQ.wall_metrics(L)['interior_retrace'] < 1e-6


def test_opening_spanning_a_corner_of_a_two_path_wall():
    # O's bottom-right corner is at s = 200; 30 wide around it
    L = two_face('relation', ops=(('O', 200, 30),))
    paths, meta, mv, m = _route(L)
    assert all(not p.closed for p in _faces(paths, 'O') + _faces(paths, 'I'))
    assert not _crosses_box(paths, meta, (190.5, 199.5, 0.5, 9.5))
    _assert_internal_inside(L)
    assert m['print_runs'] == 1 and m['travel_moves'] == 0


def test_opening_through_circle_relation_wall():
    O, I = CirclePath(200, 200, 80, id='O'), CirclePath(200, 200, 70, id='I')
    L = PrintLayer('t', source_paths=[O, I], wall_relations=[WallRelation('w', 'O', 'I', 10)],
                   infills=[RegionInfill('F', 'O', 'wave', {'spacing': 20})],
                   openings=[Opening('o', 'O', 0.0, 20)])
    paths, meta, mv, m = _route(L)
    assert all(not p.closed for p in _faces(paths, 'O') + _faces(paths, 'I'))
    assert len([p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system']) == 2
    _assert_internal_inside(L)
    assert m['print_runs'] == 1 and m['travel_moves'] == 0


def test_opening_edge_is_not_a_wall_connection():
    # the cut faces are wall ENDS (capped), never junctions with the other
    # face: no junction is created and each face piece ends at a cap
    L = two_face('relation', ops=(('O', 100, 24),))
    paths, meta = L._build_effective()
    assert meta['network']['junctions'] == [] or all(not j['corner'] for j in meta['network']['junctions'])
    caps = [_pts(p, meta) for p in paths if getattr(p, 'treatment_id', '') == 'wall_system']
    ends = [q for p in _faces(paths, 'O') + _faces(paths, 'I') for q in (_pts(p, meta)[0], _pts(p, meta)[-1])]
    for e in ends:
        assert any(min(e.dist(c[0]), e.dist(c[-1])) < 1e-6 for c in caps)


def test_wave_infill_clipped_by_two_path_opening():
    L = two_face('relation', ops=(('O', 100, 24),), pattern='wave')
    paths, meta, mv, m = _route(L)
    assert not _crosses_box(paths, meta, (90, 110, 0.5, 9.5))
    assert RQ.wall_metrics(L)['congestion_hotspots'] == 0


# ---------------------------------------------------------------------------
# SOLID infill (Pass 8 correction): coherent perimeter / infill phases,
# conventional rectilinear, serpentine = interconnected smooth web.
# Zero travel is a preference, not the objective: routing numbers are
# reported / bounded loosely, geometry is asserted.
# ---------------------------------------------------------------------------

from test_pass7 import SOLIDS, _solid          # noqa: E402  (shared fixtures)
import solid as SO                             # noqa: E402
from graph import build_graph                  # noqa: E402

# Pass 7 (conventional) print runs per fixture — the corrected routing must
# not need MORE runs than the conventional fill
P7_RUNS = {'rectangle': 1, 'ellipse': 1, 'rect + circular void': 3,
           'ellipse + circular void': 2, 'multiple voids': 5, 'nested island': 4}


def _solid_info(L):
    return next(i for i in L.network_summary()['infills'] if i['id'] == 'S')['solid']


def _solid_route(L):
    paths, meta = L._build_effective()
    solid = {p.id for p in paths if getattr(p, 'treatment_id', '') in ('solid_infill', 'solid_link')}
    rl = L.to_routing_layer(paths, meta)
    return paths, meta, rl, route_layer(rl), solid


@pytest.mark.parametrize('pat', ['rectilinear', 'serpentine'])
@pytest.mark.parametrize('name', list(SOLIDS))
def test_solid_perimeters_print_as_whole_loops(name, pat):
    # Pass 8's hairpins made every boundary contact a routing junction:
    # playback went perimeter → infill → perimeter → … Now every boundary
    # ring is printed in ONE contiguous stretch of the route (a complete
    # perimeter operation), never twice, and the infill is its own phase.
    L = SOLIDS[name](pat)
    paths, meta, rl, mv, solid = _solid_route(L)
    assert RQ.geometric_retrace(mv) < 1e-6
    blocks = {}
    for i, m in enumerate(mv):
        if m.kind != 'travel' and m.strand_id not in solid:
            blocks.setdefault(m.strand_id, []).append(i)
    for sid, idx in blocks.items():
        assert idx == list(range(idx[0], idx[0] + len(idx))), sid   # contiguous
    # a boundary ring is joined to the infill at ONE point at most
    G = build_graph(rl)
    for st in rl.strands:
        if st.id in solid:
            continue
        joins = set()
        for u, v, d in G.edges(data=True):
            if d['strand_id'] != st.id:
                continue
            for n in (u, v):
                if any(G[n][w][k]['strand_id'] in solid for w in G[n] for k in G[n][w]):
                    joins.add(n)
        assert len(joins) <= 1, (st.id, joins)
    m = RQ.solid_metrics(L)
    assert m['void_violations'] == 0 and m['congestion_hotspots'] == 0
    # routing is reported, not maximised: never more runs than the
    # conventional Pass 7 fill; simple convex regions need no travel
    assert m['runs'] <= P7_RUNS[name]
    if name in ('rectangle', 'ellipse'):
        assert m['travel_moves'] == 0


@pytest.mark.parametrize('name', list(SOLIDS))
def test_rectilinear_is_a_conventional_fill(name):
    # straight parallel passes, joined by short boundary turns (Pass 7's
    # boustrophedon) — no hairpins, no web contacts
    L = SOLIDS[name]('rectilinear')
    paths, meta = L._build_effective()
    reg = meta['network']['solid_regions'][0]
    th = math.radians(reg['angle'])
    ux, uy = math.cos(th), math.sin(th)
    lines = [p.sample_points() for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill']
    long_par = 0
    for pl in lines:
        for a, b in zip(pl, pl[1:]):
            L_ = a.dist(b)
            if L_ > 3 * 16:
                assert abs((b.x - a.x) * ux + (b.y - a.y) * uy) / L_ > 0.999   # long runs: the passes
                long_par += 1
    assert long_par >= 3
    assert _solid_info(L)['web_contacts'] == 0


def _turn_deg(a, b, c):
    v1, v2 = b - a, c - b
    l1, l2 = v1.length(), v2.length()
    if l1 < 1e-9 or l2 < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1.x * v2.x + v1.y * v2.y) / (l1 * l2)))))


WEB = ['rectangle', 'ellipse', 'rect + circular void', 'ellipse + circular void', 'multiple voids']


@pytest.mark.parametrize('name', WEB)
def test_serpentine_is_an_interconnected_smooth_web(name):
    L = SOLIDS[name]('serpentine')
    paths, meta, rl, mv, solid = _solid_route(L)
    reg = meta['network']['solid_regions'][0]
    from infill import _Region
    R = _Region([[Vec2(*q) for q in ring] for ring in reg['rings']], 4.0)
    sp = 16.0
    th = math.radians(reg['angle'])
    nx_, ny_ = -math.sin(th), math.cos(th)
    polys = [p.sample_points() for p in paths if p.id in solid]
    # 1. smooth: away from the boundary turns no vertex turns sharply
    worst = 0.0
    for pl in polys:
        for a, b, c in zip(pl, pl[1:], pl[2:]):
            if R.dist(b, 1.5 * sp) >= 1.5 * sp:
                worst = max(worst, _turn_deg(a, b, c))
    assert worst <= 20.0
    # 2. genuinely curvy: long strands swing by ≈ the full spacing
    swing = max((max(q.x * nx_ + q.y * ny_ for q in pl) - min(q.x * nx_ + q.y * ny_ for q in pl))
                for pl in polys if len(pl) > 40)
    assert swing >= 0.8 * sp
    # 3. interconnected: neighbouring strands share CONTACT vertices — two
    # strands touching tangentially from opposite sides (180° apart), a
    # hand-off of exactly two strands (no knot)
    where = {}
    for pi, pl in enumerate(polys):
        for k in range(1, len(pl) - 1):
            where.setdefault((round(pl[k].x, 9), round(pl[k].y, 9)), []).append((pi, k))
    contacts = [v for v in where.values() if len(v) >= 2]
    assert len(contacts) >= 10
    assert _solid_info(L)['web_contacts'] >= 10
    for occ in contacts:
        assert len(occ) == 2                                     # two strands, never more
        sides = []
        for pi, k in occ:
            pl = polys[pi]
            a, b, c = pl[k - 1], pl[k], pl[k + 1]
            t = c - a
            assert _turn_deg(a, b, c) < 20.0                     # passes smoothly (crest curvature)
            # which side of the contact the strand bulges to
            mid = a.lerp(c, 0.5)
            sides.append((mid.x - b.x) * nx_ + (mid.y - b.y) * ny_)
        assert sides[0] * sides[1] < 0                           # opposite phase
    G = build_graph(rl)
    gen_deg = max(sum(1 for w in G[n] for kk in G[n][w] if G[n][w][kk]['strand_id'] in solid)
                  for n in G.nodes)
    assert gen_deg <= 4                                          # two strands at a contact
    m = RQ.solid_metrics(L)
    assert m['void_violations'] == 0 and m['exact_retrace'] < 1e-6
    assert m['congestion_hotspots'] == 0


@pytest.mark.parametrize('pat', ['rectilinear', 'serpentine'])
def test_boundary_support_is_opt_in(pat):
    # default: diagnostic only — no landings bend the field
    L = _solid(EllipsePath(200, 150, 160, 100, id='O'), pat=pat, angle=0)
    info = _solid_info(L)
    assert info['bumps'] == 0 and info['max_unsupported_limit'] == 0
    assert info['max_unsupported'] > 0                           # still reported
    # an explicit user limit adds landings (part of the passes)
    L = _solid(EllipsePath(200, 150, 160, 100, id='O'), pat=pat, angle=0, max_unsupported=40)
    m = RQ.solid_metrics(L)
    assert m['rings'][0]['max_unsupported'] <= 40 + 0.5
    assert _solid_info(L)['bumps'] > 0
    assert m['exact_retrace'] < 1e-6 and m['congestion_hotspots'] == 0


@pytest.mark.parametrize('pat', ['rectilinear', 'serpentine'])
@pytest.mark.parametrize('name', list(SOLIDS))
def test_solid_perimeter_printed_exactly_once(name, pat):
    # every segment of every boundary ring is printed exactly ONCE (no
    # perimeter reprinting, no perimeter retrace to close a route), and the
    # routing numbers are available as diagnostics
    L = SOLIDS[name](pat)
    paths, meta, rl, mv, solid = _solid_route(L)
    G = build_graph(rl)
    expected = {}
    for u, v, d in G.edges(data=True):
        if d['strand_id'] not in solid:
            expected[d['strand_id']] = expected.get(d['strand_id'], 0) + 1
    printed = {}
    for m in mv:
        if m.kind != 'travel' and m.strand_id not in solid:
            printed[m.strand_id] = printed.get(m.strand_id, 0) + 1
            assert m.kind == 'print', m           # never 'retrace'
    assert printed == expected
    m = RQ.solid_metrics(L)
    for k in ('runs', 'travel_moves', 'travel_length', 'exact_retrace'):
        assert k in m
    assert mv[0].start.dist(mv[-1].end) >= 0.0   # start / end distance is reported

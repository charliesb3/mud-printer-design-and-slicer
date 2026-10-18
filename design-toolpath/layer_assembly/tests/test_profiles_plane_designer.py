"""
PLANAR TOP CUT through the REAL Designer: one plane cuts a selection of
walls (circles, rectangles, lines; several Wall Systems); each layer is the
ordinary Designer resolution of the design with the selected sources cut by
that layer's half-plane (Openings on closed sources, trimmed open sources).
Checked: the resolved top terminations of every selected wall lie EXACTLY on
the one plane; unselected walls are untouched; away from the cut line every
layer is the uncut stack (cross-Z phase / lanes kept); no headers.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve
from layer_assembly.profiles import (PlaneProfile, OpeningProfile, WallRef, WallTarget, apply_profiles,
                                     plane_from_high_low)
from layer_assembly.source import stack_geometry, bbox_of
from layer_assembly.support import analyse_support

BEAD, T = 3.0, 10.0
MAT = {'bead_width': BEAD, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _system(sid, kind, members):
    params = {'paths': 4} if kind in ('interleaved', 'linked') else {}
    ws = {'id': sid, 'name': sid, 'type': kind, 'members': members, 'thickness': T, 'params': params}
    return ws


def building(kind='hollow', cap='flat', extra=()):
    """A circle (WS1) and a rectangle (WS2) of `kind`, and an UNSELECTED line
    (its own hollow wall record WU — a BUILD document, as the Wall Systems
    page's generation receives it) — disconnected."""
    doc = {'id': 'b', 'material': dict(MAT), 'cap_style': cap, 'source_paths': [
        {'id': 'C', 'type': 'CirclePath', 'label': 'Circle', 'cx': 0, 'cy': 0, 'radius': 60, 'closed': True},
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'x': 100, 'y': -40, 'w': 100, 'h': 80, 'closed': True},
        {'id': 'U', 'type': 'LinePath', 'label': 'Unselected', 'start': [-110, -60], 'end': [-110, 60]}] + list(extra),
        'wall_systems': [_system('WS1', kind, ['C']), _system('WS2', kind, ['R']), _system('WU', 'hollow', ['U'])]}
    if kind == 'skin_web':
        for ws, host in ((doc['wall_systems'][0], 'C'), (doc['wall_systems'][1], 'R')):
            ws['web'] = {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}
        doc['infills'] = [{'id': 'WEBC', 'path_id': 'C', 'pattern': 'zigzag', 'params': {'spacing': 20}, 'kind': 'wall',
                           'variation_index': 0, 'owner': 'WS1'},
                          {'id': 'WEBR', 'path_id': 'R', 'pattern': 'zigzag', 'params': {'spacing': 20}, 'kind': 'wall',
                           'variation_index': 0, 'owner': 'WS2'}]
    return doc


def stackp(src, a, ids=('A',), fps=None):
    res = resolve(a, list(ids), fps)
    tg = {d: {t.key: t for t in map(WallTarget.from_dict, src.walls(d))} for d in ids}
    res, rep = apply_profiles(res, a.profiles, tg, BEAD, a.max_overhang, {d: src.sources(d) for d in ids})
    from layer_assembly.construction_chain import run as run_chain
    res = run_chain(src, res)                            # (the construction-state chain, as the print resolve)
    return res, rep, stack_geometry(res, src)


def _segments(polys):
    for p in polys:
        pts = p['pts'] + ([p['pts'][0]] if p.get('closed') else [])
        yield from zip(pts, pts[1:])


def _seg_dist(q, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)


def _changed(A, B, tol=0.05, cell=2.0):
    def off(P, Q):
        grid = {}
        for a, b in _segments(Q):
            for gx in range(int(math.floor(min(a[0], b[0]) / cell)), int(math.floor(max(a[0], b[0]) / cell)) + 1):
                for gy in range(int(math.floor(min(a[1], b[1]) / cell)), int(math.floor(max(a[1], b[1]) / cell)) + 1):
                    grid.setdefault((gx, gy), []).append((a, b))
        out = []
        for p in P:
            for q in p['pts']:
                gx, gy = int(math.floor(q[0] / cell)), int(math.floor(q[1] / cell))
                near = [sg for dx in (-1, 0, 1) for dy in (-1, 0, 1) for sg in grid.get((gx + dx, gy + dy), ())]
                if not near or min(_seg_dist(q, a, b) for a, b in near) > tol:
                    out.append(q)
        return out
    return off(A, B) + off(B, A)


def _changed_one_way(A, B, tol=0.05, cell=2.0):
    """Vertices of A not on B's geometry."""
    grid = {}
    for a, b in _segments(B):
        for gx in range(int(math.floor(min(a[0], b[0]) / cell)), int(math.floor(max(a[0], b[0]) / cell)) + 1):
            for gy in range(int(math.floor(min(a[1], b[1]) / cell)), int(math.floor(max(a[1], b[1]) / cell)) + 1):
                grid.setdefault((gx, gy), []).append((a, b))
    out = []
    for p in A:
        for q in p['pts']:
            gx, gy = int(math.floor(q[0] / cell)), int(math.floor(q[1] / cell))
            near = [sg for dx in (-1, 0, 1) for dy in (-1, 0, 1) for sg in grid.get((gx + dx, gy + dy), ())]
            if not near or min(_seg_dist(q, a, b) for a, b in near) > tol:
                out.append(q)
    return out


def terminations(src, inst, pid='P', corner=False):
    """World points where the resolved selected walls END at the cut (on
    their source lines, as the Designer reports them; its geometry checks are
    separate). corner=True: only the ends a CORNER MINIMUM moved (below the
    plane by design); False: every other end."""
    built = src.lib.build(inst.design_id, cuts=inst.cut_spec())
    out = []
    for r in built.semantic.get('profile') or []:
        if r.get('op') != 'plane' or pid not in (r.get('profile') or '').split('+'):
            continue
        cm = set(r.get('corner_min') or [])
        for sid, ends in (r.get('ends') or {}).items():
            if (sid in cm) == corner:
                out += [tuple(e) for e in ends]
    T_ = inst.transform
    return [T_.apply(x, y) for x, y in out]


def _plane_line_dist(p, inst, q):
    """Signed plan distance of a design point q from the layer's cut line,
    positive on the KEPT side (removed material lies at negative values)."""
    (px, py), (nx, ny) = p.line_at(0.5 * (inst.z_bottom + inst.z_top))
    w = inst.transform.apply(*q)
    return (w[0] - px) * nx + (w[1] - py) * ny


FAMILIES = ['hollow', 'skin_web', 'interleaved', 'linked', 'chain']


@pytest.mark.parametrize('kind', FAMILIES)
def test_one_plane_cuts_two_wall_systems_exactly(kind):
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building(kind)}])
    canon = src.geometry('A').polylines
    # uphill toward −x: the rectangle is cut first, the circle later; the line is NOT selected
    p = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.3, anchor=(205.0, 0.0), z_anchor=18.0)
    a = Assembly(1.5, [Section('A', 120)], profiles=[p])
    res, rep, geo = stackp(src, a)
    assert rep[0].valid and len(rep[0].layers) > 30
    unsel = [q for q in canon for _ in [0] if all(x < -100 for x, _ in q['pts'])]
    # the stack's own uncut layer (a single-pass lattice ring prints its doubled
    # STATION reference on every layer of the stack, as a Door Gap's Base does)
    canon = geo[res.instances[0].geometry_key][0]
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        z = L['z']
        polys = geo[inst.geometry_key][0]
        # EXACT COPLANARITY: every resolved termination of every selected wall on the plane
        ends = terminations(src, inst)
        assert all(abs(p.z_at(*w) - z) < 1e-6 for w in ends), [(w, p.z_at(*w) - z) for w in ends][:4]
        if not L['removed_all']:
            # away from the cut line every layer is the uncut stack (phase / lanes / stations kept):
            # exactly for Hollow / Skin + Web; generated constructions within 1.3 in (corner mitres of a
            # cut ring), the Chained Loop within 4.5 in at the ring's corners (its frame there)
            # (the chain: the same tightly looping curve sampled at another density — crest vertices ≤ 1 in)
            tol = {'hollow': 0.05, 'skin_web': 0.05, 'interleaved': 2.0, 'linked': 2.0, 'chain': 1.0}[kind]
            far = [q for q in _changed(polys, canon, tol) if _plane_line_dist(p, inst, q) > 1.7 * T]
            if kind == 'chain':
                # KNOWN: a Chained Loop RING cut open closes as the out-and-back pair — a mirrored
                # RETURN lap is added on cut layers (the chain's closure rule). The uncut lap itself
                # is kept: every canonical vertex on the kept side lies on the cut layer's chain.
                lost = [q for q in _changed_one_way(canon, polys, tol) if _plane_line_dist(p, inst, q) > 1.7 * T]
                assert len(lost) <= max(80, 0.03 * sum(len(pl['pts']) for pl in canon)), (L['layer'], len(lost))
            elif kind in ('interleaved', 'linked'):
                # (a corner MITRE vertex of the cut ring may sit a few inches off the uncut one)
                assert len(far) <= 3, (L['layer'], far[:3])
            else:
                assert far == [], (L['layer'], far[:3])
            # nothing of the RECTANGLE survives beyond the cut line (½ wall + a bead: its cut faces
            # are square to the wall; on the circle a radial face reaches further — see limitations)
            (px, py), (nx, ny) = p.line_at(z)
            beyond = [q for pl in polys for q in pl['pts'] if q[0] > 65 and (q[0] - px) * nx + (q[1] - py) * ny < -(T / 2 + BEAD)]
            assert beyond == [], (L['layer'], beyond[:3])
        # the unselected wall prints unchanged on every layer
        assert all(not _changed(unsel, [pl for pl in polys if all(x < -100 for x, _ in pl['pts'])]) for _ in [0])
    fs = analyse_support(res, geo, a).findings
    first = rep[0].layers[0]['layer']
    # a HEADER only where the first cut layers slice a sliver off the rectangle's corner (its cap
    # over the corner cavity, nothing below yet — real, reported); nowhere else
    def at_termination(f):
        return abs(_plane_line_dist(p, res.instances[f.layer], f.local_centre)) <= 2.0 * T
    # (KNOWN, reported: the Chained Loop's end motif crossing a corner as its termination
    # passes it may also bridge — always AT that layer's termination)
    # (KNOWN, reported: where the termination sweeps past a corner, the corner minimum's caps
    # sit over the corner cavity below; a chain's end motif crossing a corner — always AT that
    # layer's termination, never elsewhere)
    assert all((f.layer <= first + 1 and f.centre[0] > 180) or at_termination(f)
               for f in fs if f.status == 'needs_header'), [(f.layer, f.centre) for f in fs if f.status == 'needs_header']
    if kind in ('hollow', 'skin_web'):
        # (the first cut layer clips a sliver off the rectangle's corners: the only findings allowed are there)
        assert all(f.centre[0] > 180 for f in fs), [(f.layer, f.centre) for f in fs]
    elif kind == 'chain':
        # KNOWN (reported, never hidden): a Chained Loop RING cut open prints the out-and-back
        # pair, so its first cut layers carry a return lap over the single lap below —
        # INSUFFICIENT SUPPORT along that piece (a ring needs a doubled chain reference, as the
        # single-pass lattice ring has — not built)
        assert all(f.kind == 'overlap' for f in fs if f.status != 'needs_header')
    else:
        # generated constructions: the END MOTIF retreating 5 in per layer (slope 0.3 at 1.5 in
        # layers) may be reported at the termination — never far from that layer's cut line
        def near_cut(f):
            inst = res.instances[f.layer]
            return abs(_plane_line_dist(p, inst, f.local_centre)) <= 3.0 * T or _plane_line_dist(p, inst, f.local_centre) < 0
        assert all(near_cut(f) for f in fs), [(f.layer, f.centre) for f in fs if not near_cut(f)]


def test_straight_single_wall_trimmed_along_the_plane():
    doc = {'id': 'l', 'material': dict(MAT), 'source_paths': [
        {'id': 'L', 'type': 'LinePath', 'label': 'Wall', 'start': [0, 0], 'end': [120, 0],
         'envelope': {'thickness': T, 'align': 'auto', 'print_reference': False}}]}
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': doc}])
    p = plane_from_high_low('P', 180.0, (-120.0, 0.0, (0.0, 0.0)), z_high=72.0, z_low=12.0, sources=('L',))
    a = Assembly(1.5, [Section('A', 84)], profiles=[p])
    res, rep, geo = stackp(src, a)
    assert rep[0].valid
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        polys = geo[inst.geometry_key][0]
        if L['removed_all'] or polys == []:     # (less than a bead left: the wall is absent)
            assert polys == []
            continue
        ends = terminations(src, inst)
        assert len(ends) == 1 and abs(p.z_at(*ends[0]) - L['z']) < 1e-6
        assert max(x for pl in polys for x, _ in pl['pts']) <= ends[0][0] + 1e-6
    assert analyse_support(res, geo, a).findings == []


def test_plane_across_corners_and_a_junction():
    attached = {'id': 'B', 'type': 'LinePath', 'label': 'Buttress', 'start': [200, 0], 'end': [260, 0]}
    doc = building('hollow', extra=[attached])
    doc['wall_systems'][1]['members'] = ['R', 'B']
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': doc}])
    p = PlaneProfile('P', systems=('WS2',), direction=225.0, slope=0.35, anchor=(250.0, 30.0), z_anchor=10.0)
    a = Assembly(1.5, [Section('A', 96)], profiles=[p])
    res, rep, geo = stackp(src, a)
    assert rep[0].valid
    opened = []
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        assert all(abs(p.z_at(*w) - L['z']) < 1e-6 for w in terminations(src, inst))     # coplanar
        polys = geo[inst.geometry_key][0]
        if not L['removed_all']:
            (px, py), (nx, ny) = p.line_at(L['z'])
            assert not [q for pl in polys for q in pl['pts'] if q[0] > 95 and
                        (q[0] - px) * nx + (q[1] - py) * ny < -(T / 2 + BEAD) * math.sqrt(2)]
        if src.lib._open_ends(*_layer_of(src, inst)):
            opened.append(L['layer'])
    # KNOWN (reported): where the plane slices within a wall thickness of the corner and the
    # buttress junction, the Designer cannot close that layer's route (degenerate corner ends)
    assert len(opened) <= 3, opened


def _layer_of(src, inst):
    from app import _deserialise_layer
    b = src.lib.build(inst.design_id, cuts=inst.cut_spec())
    lay = _deserialise_layer(b.document)
    paths, meta = lay._build_effective()
    return lay, paths, meta


def test_flip_slope_level_plane_and_a_steep_cut():
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    base = PlaneProfile('P', systems=('WS1',), direction=0.0, slope=0.4, anchor=(-60.0, 0.0), z_anchor=20.0)
    for p in (base, base.flipped(), PlaneProfile('P', systems=('WS1',), slope=0.0, z_anchor=30.0),
              PlaneProfile('P', systems=('WS1',), direction=0.0, slope=6.0, anchor=(-60.0, 0.0), z_anchor=20.0)):
        a = Assembly(1.5, [Section('A', 96)], profiles=[p])
        res, rep, geo = stackp(src, a)
        assert rep[0].valid, rep[0].errors
        for L in rep[0].layers:
            inst = res.instances[L['layer']]
            if p.slope == 0:
                assert L['removed_all'] and L['z'] > 30
                continue
            assert all(abs(p.z_at(*w) - L['z']) < 1e-6 for w in terminations(src, inst))
        assert not [f for f in analyse_support(res, geo, a).findings if f.status == 'needs_header']
    # FLIP: the circle's top is low on the other side
    assert base.z_at(-60, 0) == base.flipped().z_at(-60, 0) and base.z_at(60, 0) > base.flipped().z_at(60, 0)


@pytest.mark.parametrize('kind', ['skin_web', 'interleaved'])
def test_flat_and_rounded_terminations_stay_local(kind):
    for cap in ('flat', 'full_round'):
        src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building(kind, cap)}])
        p = PlaneProfile('P', systems=('WS1',), direction=0.0, slope=0.3, anchor=(-60.0, 0.0), z_anchor=20.0)
        a = Assembly(1.5, [Section('A', 96)], profiles=[p])
        res, rep, geo = stackp(src, a)
        canon = geo[res.instances[0].geometry_key][0]           # the stack's own uncut layer
        for L in rep[0].layers:
            if L['removed_all']:
                continue
            inst = res.instances[L['layer']]
            far = [q for q in _changed(geo[inst.geometry_key][0], canon, 0.05 if kind == 'skin_web' else 1.3)
                   if _plane_line_dist(p, inst, q) > 1.7 * T]
            assert far == [], (cap, L['layer'], far[:3])


def test_coexists_with_a_section_transform_and_another_profile():
    """WORLD plane: a section shift moves the layers, the terminations stay
    on the plane in world coordinates; an opening on the (unselected) line
    is applied in the same layers."""
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    fps = {'A': Footprint.of_bbox(*bbox_of(src.geometry('A').polylines))}
    p = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.3, anchor=(205.0, 0.0), z_anchor=18.0)
    o = OpeningProfile('O', WallRef('U'), 60, 12, 48, 24)
    tf = SectionTransform('linear', (0.25, 0.0), 0.0)
    a = Assembly(1.5, [Section('A', 96, '', tf)], profiles=[p, o])
    res, rep, geo = stackp(src, a, fps=fps)
    assert all(r.valid for r in rep)
    both = [i for i in res.instances if any(c[0] == 'plane' for c in i.cuts) and any(c[0] == 'opening' for c in i.cuts)]
    assert both
    for inst in both[::4]:
        z = 0.5 * (inst.z_bottom + inst.z_top)
        assert inst.transform.tx != 0
        assert all(abs(p.z_at(*w) - z) < 1e-6 for w in terminations(src, inst))
    first = rep[0].layers[0]['layer']
    hdr = [f for f in analyse_support(res, geo, a).findings if f.status == 'needs_header']
    assert all(f.layer <= first + 1 and f.local_centre[0] > 180 for f in hdr), [(f.layer, f.centre) for f in hdr]


def test_unselected_assembly_unchanged_and_endpoint_reports_the_plane():
    from app import app
    c = app.test_client()
    ds = [{'id': 'A', 'name': 'A', 'document': building('hollow')}]
    p = PlaneProfile('P', systems=('WS1',), direction=0.0, slope=0.3, anchor=(-60.0, 0.0), z_anchor=20.0).to_dict()
    body = {'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 72}], 'profiles': [p]},
            'design_ids': ['A'], 'designs': ds}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    assert 'error' not in out and out['profiles'][0]['valid'] and out['profiles'][0]['kind'] == 'plane'
    srcs = {r['source']: r for r in out['profile_sources']['A']}
    assert srcs['C']['system'] == 'WS1' and srcs['C']['closed'] and srcs['U']['system'] == 'WU'   # (its own hollow record)
    cutv = [v for v in out['variants'].values() if any(x['op'] == 'plane' for x in v.get('cuts') or [])]
    assert cutv and all(any(x['op'] == 'plane' and x.get('applied') for x in v['profile']) for v in cutv)
    none = c.post('/api/assembly/resolve', json=dict(body, assembly=dict(body['assembly'], profiles=[]))).get_json()
    assert 'variants' not in none


# ---- 2026-10-08: plane POSITION independent of slope; several planes compose ----------------

def _plane_through(direction, slope, point, z):
    """The plane of that direction / slope meeting Z `z` at plan `point`."""
    return dict(direction=direction, slope=slope, anchor=point, z_anchor=z)


def test_translated_plane_cuts_only_one_corner_of_a_flat_building():
    """A flat 80 in rectangle; an inclined plane positioned so it meets the
    top only near one corner: that corner is removed, the rest of the building
    keeps its 80 in top (every layer far from the corner is the uncut layer)."""
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    # uphill toward the building centre (225° = toward −x −y), meeting z 80 at 20 in in from the corner (200, 40)
    p = PlaneProfile('P', systems=('WS2',), **_plane_through(225.0, 1.0, (200 - 14.14, 40 - 14.14), 80.0))
    a = Assembly(1.5, [Section('A', 80)], profiles=[p])
    res, rep, geo = stackp(src, a)
    assert rep[0].valid and rep[0].layers
    canon = geo[res.instances[0].geometry_key][0]
    top = res.instances[-1]
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        polys = geo[inst.geometry_key][0]
        assert all(abs(p.z_at(*w) - L['z']) < 1e-6 for w in terminations(src, inst))
        far = [q for q in _changed(polys, canon) if _plane_line_dist(p, inst, q) > 1.7 * T]
        assert far == [], (L['layer'], far[:3])
    # the top layer still has its far corners and the circle: only the one corner is gone
    tp = geo[top.geometry_key][0]
    pts = [q for pl in tp for q in pl['pts']]
    assert any(abs(x - 100) < 6 and abs(y + 40) < 6 for x, y in pts)                 # opposite corner kept
    assert not any(x > 195 and y > 35 for x, y in pts)                                # the cut corner gone
    assert len(rep[0].layers) < 0.5 * len(res.instances)                              # only the upper layers


def test_planar_cut_on_one_wall_replaces_the_wall_taper_exactly():
    """The old Wall Taper's cut line as ONE plane over that wall (the UI's
    "Convert"): every layer inside the taper's Z range resolves identically."""
    from layer_assembly.profiles import taper
    doc = {'id': 'l', 'material': dict(MAT), 'source_paths': [
        {'id': 'L', 'type': 'LinePath', 'label': 'Wall', 'start': [140, 330], 'end': [260, 330]}],
        'wall_systems': [{'id': 'WS', 'type': 'skin_web', 'members': ['L'], 'thickness': T, 'params': {},
                          'web': {'pattern': 'wave', 'params': {'spacing': 20}, 'variation_index': 0}}],
        'infills': [{'id': 'WEB', 'path_id': 'L', 'pattern': 'wave', 'params': {'spacing': 20}, 'kind': 'wall',
                     'variation_index': 0, 'owner': 'WS'}]}
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': doc}])
    t = taper('T', WallRef('L'), 'start', 120, 6, 40, 66)
    pl = PlaneProfile('T', sources=('L',), direction=180.0, slope=60 / 80, anchor=(260.0, 330.0), z_anchor=6.0)
    out = {}
    for prof in (t, pl):
        a = Assembly(1.5, [Section('A', 72)], profiles=[prof])
        res, rep, geo = stackp(src, a)
        assert rep[0].valid
        out[prof.kind] = {L['layer']: geo[res.instances[L['layer']].geometry_key][0]
                          for L in rep[0].layers if 6 < L['z'] < 66}
        assert not analyse_support(res, geo, a).findings
    assert out['extent'] and set(out['extent']) == set(out['plane'])
    for k in out['extent']:
        assert not _changed(out['extent'][k], out['plane'][k]), k


def _corner_planes():
    A = PlaneProfile('A', systems=('WS2',), **_plane_through(225.0, 1.0, (186.0, 26.0), 60.0))     # corner (200, 40)
    B = PlaneProfile('B', systems=('WS2',), **_plane_through(45.0, 0.8, (114.0, -26.0), 50.0))     # corner (100, −40)
    return A, B


# KNOWN (class A, Z4; WALL_ARCHITECTURE.md Z.6): on the Skin + Web building the
# layers cut by both planes RE-PLAN their web (the clipped-reference local join
# does not apply there: `lattice` report 'planned', not 'inherited'), so ~130
# web vertices change farther than 1.7 T from both cut lines — a nonlocal
# re-phase; invariant 4 (territory consumed locally). Strict xfail until the
# identity-anchored state (Z4) makes the cut a local event.
@pytest.mark.parametrize('kind', ['hollow',
                                  pytest.param('skin_web', marks=pytest.mark.xfail(
                                      strict=True, reason='KNOWN (class A, Z4): both-plane layers re-plan the web — '
                                                          'WALL_ARCHITECTURE.md Z.6')),
                                  'interleaved'])
def test_two_planes_on_the_same_walls_compose_subtractively(kind):
    """Two corners sliced by two different planes: material survives only
    where BOTH keep it; every termination lies on one of the planes; away
    from both cut lines every layer is the uncut stack (pattern coherent)."""
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building(kind)}])
    A, B = _corner_planes()
    a = Assembly(1.5, [Section('A', 80)], profiles=[A, B])
    res, rep, geo = stackp(src, a)
    assert all(r.valid for r in rep)
    canon = geo[res.instances[0].geometry_key][0]
    tol = 0.05 if kind != 'interleaved' else 2.0
    both = 0
    for inst in res.instances:
        ops = [json.loads(c[1]) for c in inst.cuts if c[0] == 'plane']
        if not ops:
            continue
        both += len(ops) == 2
        z = 0.5 * (inst.z_bottom + inst.z_top)
        polys = geo[inst.geometry_key][0]
        for w in terminations(src, inst, 'A') + terminations(src, inst, 'B'):
            assert min(abs(A.z_at(*w) - z), abs(B.z_at(*w) - z)) < 1e-6
        far = [q for q in _changed(polys, canon, tol)
               if all(_plane_line_dist(P_, inst, q) > 1.7 * T for P_ in (A, B) if P_.line_at(z) not in ('all', 'none'))]
        assert len(far) <= (3 if kind == 'interleaved' else 0), (inst.index, far[:3])
        for P_ in (A, B):                                    # nothing survives far beyond either line
            ln = P_.line_at(z)
            if ln in ('all', 'none'):
                continue
            (px, py), (nx, ny) = ln
            assert not [q for pl in polys for q in pl['pts'] if q[0] > 65 and
                        (q[0] - px) * nx + (q[1] - py) * ny < -(T / 2 + BEAD) * math.sqrt(2)]
    assert both > 3                                            # layers cut by BOTH planes
    assert not [f for f in analyse_support(res, geo, a).findings if f.status == 'needs_header' and kind != 'hollow']


def test_overlapping_selections_and_editing_or_deleting_one_plane():
    """A gentle whole-building plane (both systems) + a steep local one (the
    rectangle only): the circle sees only the first; deleting the local plane
    gives back exactly the whole-building result; editing it (moving it)
    never changes the other plane's cuts."""
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    G = PlaneProfile('G', systems=('WS1', 'WS2'), direction=180.0, slope=0.15, anchor=(205.0, 0.0), z_anchor=50.0)
    S = PlaneProfile('S', systems=('WS2',), **_plane_through(225.0, 1.5, (186.0, 26.0), 70.0))
    res2, rep2, geo2 = stackp(src, Assembly(1.5, [Section('A', 96)], profiles=[G, S]))
    res1, rep1, geo1 = stackp(src, Assembly(1.5, [Section('A', 96)], profiles=[G]))
    assert all(r.valid for r in rep2)
    gops = lambda res: [[c[1] for c in i.cuts if c[0] == 'plane' and json.loads(c[1])['profile'] == 'G'] for i in res.instances]
    assert gops(res2) == gops(res1)                                    # G's cuts do not depend on S
    S2 = PlaneProfile('S', systems=('WS2',), **_plane_through(225.0, 1.5, (176.0, 16.0), 70.0))
    res3, _, _ = stackp(src, Assembly(1.5, [Section('A', 96)], profiles=[G, S2]))
    assert gops(res3) == gops(res1)                                    # … nor on where S is moved
    for i1, i2 in zip(res1.instances, res2.instances):
        p1, p2 = geo1[i1.geometry_key][0], geo2[i2.geometry_key][0]
        circle = lambda P_: [pl for pl in P_ if all(x < 70 for x, _ in pl['pts'])]
        assert not _changed(circle(p1), circle(p2)), i1.index           # the circle sees only G
        if not any(c[0] == 'plane' and json.loads(c[1])['profile'] == 'S' for c in i2.cuts):
            assert i1.geometry_key == i2.geometry_key or not _changed(p1, p2)   # S absent: G alone

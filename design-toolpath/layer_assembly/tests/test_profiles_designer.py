"""
ASSEMBLY PROFILES through the REAL Designer: the per-layer cut spec becomes
ordinary Designer geometry (design_proto/profile_cuts.py) BEFORE the layer is
resolved — openings cut the whole wall assembly and are capped, tapered walls
are shortened, Wall Systems / lattice regenerate, routes close, support finds
nothing for a valid profile. Also: isolation (no profiles → identical),
coexistence with transforms, section boundaries / derived designs, the web
endpoints, worker processes.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Section, SectionTransform, TransformCenter, Footprint, resolve


# (written for the 1.5 in default Maximum Overhang — since 2026-10-11 the
# default is 1 in: these cases state the 1.5 in they were designed with)
_Assembly = Assembly


def Assembly(*a, **k):          # noqa: F811
    if len(a) < 9:
        k.setdefault('max_overhang', 1.5)
    return _Assembly(*a, **k)


Assembly.from_dict = _Assembly.from_dict
from layer_assembly.profiles import OpeningProfile, WallRef, WallTarget, taper, apply_profiles
from layer_assembly import opening_shape as OS
from layer_assembly.source import stack_geometry, bbox_of
from layer_assembly.support import analyse_support

BEAD, T = 3.0, 10.0
MAT = {'bead_width': BEAD, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
# constructions whose routes close at free wall ends (even lanes / skins)
CLOSING = [('plain', {}), ('hollow', {}), ('skin_web', {}), ('parallel', {'walls': 4}),
           ('interleaved', {'paths': 4}), ('linked', {'paths': 4}), ('chain', {})]


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def line_doc(system='plain', params=None, length=120.0, extra=()):
    """A free-standing straight wall L (0,0)→(length,0), 10 in, in a Wall
    System of the given construction (plain = the path's own wall)."""
    paths = [{'id': 'L', 'type': 'LinePath', 'label': 'Wall', 'start': [0, 0], 'end': [length, 0],
              **({'envelope': {'thickness': T, 'align': 'auto', 'print_reference': False}}
                 if system == 'plain' else {})}] + [dict(e) for e in extra]
    doc = {'id': 'l', 'source_paths': paths, 'material': dict(MAT)}
    if system != 'plain':
        doc['wall_systems'] = [{'id': 'WS1', 'type': system, 'members': [p['id'] for p in paths],
                                'thickness': T, 'params': dict(params or {})}]
        if system == 'skin_web':
            doc['wall_systems'][0]['web'] = {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}
            doc['infills'] = [{'id': 'WEB', 'path_id': 'L', 'pattern': 'zigzag', 'params': {'spacing': 20},
                               'kind': 'wall', 'variation_index': 0, 'owner': 'WS1'}]
    return doc


def designs(doc, derived=None):
    out = [{'id': 'A', 'name': 'Base', 'document': doc}]
    if derived is not None:
        out.append({'id': 'B', 'name': 'Variant', 'parent': 'A', 'patch': derived})
    return out


def targets_of(src, ids):
    return {d: {t.key: t for t in map(WallTarget.from_dict, src.walls(d))} for d in ids}


def stack(src, assembly, ids=('A',), fps=None, groups=None):
    """The resolved stack as the print resolve makes it: profiles, then the
    construction-state chain (stage Z4), then every layer's geometry."""
    from layer_assembly.construction_chain import run as run_chain
    res = resolve(assembly, list(ids), fps, groups)
    res, rep = apply_profiles(res, assembly.profiles, targets_of(src, ids), BEAD, assembly.max_overhang)
    res = run_chain(src, res)
    geo = stack_geometry(res, src)
    return res, rep, geo


def open_ends(src, design, cuts):
    from app import _deserialise_layer
    b = src.lib.build(design, cuts=cuts)
    lay = _deserialise_layer(b.document)
    paths, meta = lay._build_effective()
    return src.lib._open_ends(lay, paths, meta)


def opening_cut(at=60.0, width=24.0, source='L', edge=None):
    return [{'op': 'opening', 'source': source, 'edge': edge, 'at': at, 'width': width, 'profile': 'P'}]


def _segments(polys):
    for p in polys:
        pts = p['pts'] + ([p['pts'][0]] if p.get('closed') else [])
        for a, b in zip(pts, pts[1:]):
            yield a, b


def _inside(polys, x0, x1, y0, y1, n=40):
    """Printed centreline points strictly inside the box (sampled)."""
    hits = []
    for a, b in _segments(polys):
        for k in range(n + 1):
            x, y = a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
            if x0 < x < x1 and y0 <= y <= y1:
                hits.append((x, y))
    return hits


# ---- self-supporting openings ---------------------------------------------------------------

@pytest.mark.parametrize('system,params', CLOSING, ids=[c[0] for c in CLOSING])
def test_opening_penetrates_the_whole_wall_and_every_piece_closes(system, params):
    src = DesignerSource.from_designs(designs(line_doc(system, params)))
    g = src.geometry('A', None, opening_cut(60, 24))
    # the clear opening (bead edges ≥ 24 in apart; the plain / skin caps print ON the cut face)
    assert not _inside(g.polylines, 48 + 1e-6, 72 - 1e-6, -T / 2, T / 2), 'material inside the opening'
    for side in (lambda x: x < 60, lambda x: x > 60):                 # both pieces span the wall
        ys = [y for p in g.polylines for x, y in p['pts'] if side(x)]
        assert max(ys) >= T / 2 - BEAD / 2 - 1e-6 and min(ys) <= -T / 2 + BEAD / 2 + 1e-6
    assert open_ends(src, 'A', opening_cut(60, 24)) == open_ends(src, 'A', None) == []
    rep = g.diagnostics['profile']
    assert rep == [{'profile': 'P', 'op': 'opening', 'source': 'L', 'applied': True, 'center_s': 60.0}]


@pytest.mark.parametrize('shape,height', [('diamond', 48), ('lens', 57)])   # (a Lens = the curved diamond)
@pytest.mark.parametrize('system,params', [('plain', {}), ('hollow', {}), ('skin_web', {}),
                                           ('parallel', {'walls': 4}), ('parallel', {'walls': 3})],
                         ids=['plain', 'hollow', 'skin_web', 'parallel4', 'parallel3'])
def test_valid_self_supporting_opening_has_no_support_findings(system, params, shape, height):
    src = DesignerSource.from_designs(designs(line_doc(system, params)))
    a = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 60, 6, height, 24, shape)])
    res, rep, geo = stack(src, a)
    assert rep[0].valid and len(rep[0].layers) > 20
    widths = [L['width'] for L in rep[0].layers]
    printed = []
    for L in rep[0].layers:                                            # the printed gap follows the profile
        polys = geo[res.instances[L['layer']].geometry_key][0]
        xs = [x for p in polys for x, _ in p['pts']]
        printed.append(min(x for x in xs if x > 60) - max(x for x in xs if x < 60))
    off = printed[0] - widths[0]                                       # (cap convention: constant)
    assert all(abs(p - w - off) < 1e-6 for p, w in zip(printed, widths))
    findings = analyse_support(res, geo, a).findings
    assert findings == [], [(f.layer, f.kind) for f in findings]


def test_generated_constructions_never_request_a_header():
    """Interleaved / Linked / Chained Loop keep the uncut pattern at a cut
    (see test_generated_wall_pattern_keeps_its_cross_z_phase_at_cuts); the
    new end motif at the narrowest first / closing layers may still be
    reported as INSUFFICIENT LAYER SUPPORT (KNOWN, never hidden), but a
    self-supporting opening never produces a HEADER NEEDED."""
    for system, params in (('interleaved', {'paths': 4}), ('linked', {'paths': 4}), ('chain', {})):
        src = DesignerSource.from_designs(designs(line_doc(system, params)))
        a = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 60, 6, 48, 24)])
        res, rep, geo = stack(src, a)
        assert rep[0].valid
        assert not [f for f in analyse_support(res, geo, a).findings if f.kind == 'bridge'], system


def _web_doc(pattern, length=240.0):
    doc = line_doc('skin_web', length=length)
    doc['wall_systems'][0]['web'] = {'pattern': pattern, 'params': {'spacing': 20}, 'variation_index': 0}
    doc['infills'][0]['pattern'] = pattern
    return doc


def _seg_dist(q, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)


def _changed(A, B):
    """Vertices of A that do not lie on B's geometry (and vice versa)."""
    def off(P, Q):
        segs = list(_segments(Q))
        return [q for p in P for q in p['pts'] if min(_seg_dist(q, a, b) for a, b in segs) > 1e-6]
    return off(A, B) + off(B, A)


@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
@pytest.mark.parametrize('shape', ['diamond', 'lens'])
def test_opening_is_local_on_a_skin_web_wall(pattern, shape):
    """MANUAL-REVIEW REGRESSION (2026-10-08): a valid opening in a Skin + Web
    wall used to re-plan the whole web on every cut layer (stitches moved up
    to half a pitch along the WHOLE wall → remote support findings). Now each
    cut layer prints the uncut layer's web clipped at the cut faces and
    joined there: every layer has the void, nothing changes farther than one
    wall thickness from a cut face, and support finds nothing."""
    src = DesignerSource.from_designs(designs(_web_doc(pattern)))
    a = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 120, 12, 48,
                                                                   24 if shape == 'diamond' else 18, shape)])
    res, rep, geo = stack(src, a)
    # the uncut reference is the stack's own first layer (stage Z4: the web under an opening prints two
    # passes from the bottom, carried by the construction state — not the stateless single-pass plan)
    canon = geo[res.instances[0].geometry_key][0]
    assert rep[0].valid and len(rep[0].layers) > 25
    for L in rep[0].layers:
        polys = geo[res.instances[L['layer']].geometry_key][0]
        w = L['width']
        # the void: no printed material within the clear width (caps sit on the cut faces)
        assert not _inside(polys, 120 - w / 2 + BEAD / 2 + 1e-6, 120 + w / 2 - BEAD / 2 - 1e-6, -T / 2, T / 2)
        assert [x for p in polys for x, _ in p['pts'] if x < 120] and [x for p in polys for x, _ in p['pts'] if x > 120]
        # (within a twelfth of a bead: the end margin and motif insets are measured in
        # the region's area / perimeter thickness estimate, which the cut lowers by a
        # few % — every station along the wall shifts ≤ 0.2 in; stage Z4 residual)
        # the band of the cut: a thickness plus the cut's own construction — the
        # transition cell (≤ 1.5 cells), its lane cell and the station next to it where
        # the split-off run's pass transfer lands (Z.9): about four cells, 3 T here
        far = [q for q in _changed_tol(polys, canon, tol=0.25) if abs(q[0] - 120) > w / 2 + 3 * T + 1e-6]
        assert far == [], f'layer {L["layer"]}: geometry changed {max(abs(q[0] - 120) - w / 2 for q in far):.1f} in from the cut'
    for L in rep[0].layers[::4]:
        assert open_ends(src, 'A', res.instances[L['layer']].cut_spec()) == []
    findings = analyse_support(res, geo, a).findings
    assert findings == [], [(f.layer, f.kind) for f in findings]


def test_opening_in_a_single_pass_lattice_ring_is_local():
    """A room whose wall lattice is ONE circulating pass (demo Base alone,
    wave): an opening cuts that strand once per cut face — it cannot close
    locally. As for a Door Gap derived design, every layer of the stack then
    prints a doubled run (the station reference), and each cut layer is that
    layer clipped + joined: no findings, closed, local."""
    from layer_assembly.demo import demo_designs
    src = DesignerSource.from_designs(demo_designs()[:1])
    a = Assembly(1.5, [Section('A', 96)], profiles=[OpeningProfile('P', WallRef('W', 0), 100, 12, 60, 30, 'diamond')])
    res, rep, geo = stack(src, a)
    assert rep[0].valid
    uncut = [i for i in res.instances if not any(c[0] in ('opening', 'transition') for c in i.cuts)]
    assert uncut and len({i.geometry_key for i in uncut}) == 1          # one reference for every uncut layer
    ref = geo[uncut[0].geometry_key][0]
    for L in rep[0].layers:
        polys = geo[res.instances[L['layer']].geometry_key][0]
        far = [q for q in _changed(polys, ref)
               if abs(q[0] - 200) > L['width'] / 2 + T + BEAD + 1e-6]
        assert far == [], f'layer {L["layer"]}: changed away from the opening'
    for L in rep[0].layers[::6]:
        assert open_ends(src, 'A', res.instances[L['layer']].cut_spec()) == []
    findings = analyse_support(res, geo, a).findings
    assert findings == [], [(f.layer, f.kind) for f in findings]


def test_valid_profile_adds_no_remote_findings_and_real_headers_still_work():
    """The demo room (wave lattice; a Door Gap section whose top needs a
    real header): a self-supporting opening on another wall adds no finding
    anywhere, and the door's HEADER NEEDED is still reported, untagged."""
    from layer_assembly.demo import demo_designs, demo_assembly
    src = DesignerSource.from_designs(demo_designs())
    secs = demo_assembly().sections
    base = Assembly(1.5, secs)
    r0, _, g0 = stack(src, base, ids=('A', 'B', 'C'))
    f0 = analyse_support(r0, g0, base).findings
    assert any(f.kind == 'bridge' for f in f0)                          # the door lintel
    for prof in (OpeningProfile('P', WallRef('W', 1), 60, 6, 48, 18, 'lens'),
                 OpeningProfile('P', WallRef('W', 2), 100, 60, 36, 18, 'diamond')):
        a = Assembly(1.5, secs, profiles=[prof])
        res, rep, geo = stack(src, a, ids=('A', 'B', 'C'))
        assert rep[0].valid and any(i.cuts for i in res.instances)
        f1 = analyse_support(res, geo, a).findings
        key = lambda f: (f.layer, f.kind, round(f.centre[0], 3), round(f.centre[1], 3))
        assert sorted(map(key, f1)) == sorted(map(key, f0)), prof.wall


def _changed_tol(A, B, tol=0.05, cell=2.0):
    """Vertices of A not on B's geometry (and vice versa), within tol (the
    same curve sampled at other stations differs by ~0.02 in). Gridded."""
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


GEN = [('interleaved', {'paths': 4}), ('linked', {'paths': 4}), ('chain', {})]


@pytest.mark.parametrize('system,params', GEN, ids=[g[0] for g in GEN])
@pytest.mark.parametrize('kind', ['opening', 'taper'])
def test_generated_wall_pattern_keeps_its_cross_z_phase_at_cuts(system, params, kind):
    """REVIEW 3 (2026-10-08): a profile CUTS the uncut pattern, it never
    re-fits it. Every cut layer of Interleaved / Linked / Chained Loop is the
    uncut layer's geometry (same period, phase, lanes, loop stations) except
    near the cut, where only the new end motif is solved — so the layers
    stack as they did without the profile, and support finds nothing away
    from the cut."""
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=240.0)))
    canon = src.geometry('A').polylines
    prof = (OpeningProfile('P', WallRef('L'), 120, 12, 36, 18, 'diamond') if kind == 'opening'
            else taper('T', WallRef('L'), 'auto', 240, 0, 186, 72))
    a = Assembly(1.5, [Section('A', 72)], profiles=[prof])
    res, rep, geo = stack(src, a)
    assert rep[0].valid
    reach = (2.0 if system == 'chain' else 1.3) * T          # the local end motif (chain: lead + a loop crest)
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        built = src.lib.build('A', cuts=inst.cut_spec())
        assert all(w.get('phase_ref_runs') for w in built.network['wall_systems'])     # the reference was used
        polys = geo[inst.geometry_key][0]
        if kind == 'opening':
            far = [q for q in _changed_tol(polys, canon) if abs(q[0] - 120) > L['width'] / 2 + reach]
        else:
            far = [q for q in _changed_tol(polys, canon) if q[0] < L['retained'] - reach]
        assert far == [], f'layer {L["layer"]}: the pattern changed away from the cut ({far[:3]})'
    findings = analyse_support(res, geo, a).findings
    assert not [f for f in findings if f.kind == 'bridge']
    cut_x = lambda f: 120 if kind == 'opening' else next(L['retained'] for L in rep[0].layers if L['layer'] >= f.layer - 1)
    assert all(abs(f.centre[0] - cut_x(f)) <= 2.5 * T for f in findings), \
        [(f.layer, f.centre) for f in findings]                 # nothing remote
    if kind == 'taper' and system != 'chain':
        assert findings == []


@pytest.mark.parametrize('system,params', [('interleaved', {'paths': 4}), ('hollow', {})], ids=['interleaved', 'hollow'])
def test_taper_either_diagonal_on_either_wall_direction(system, params):
    """Top-left → bottom-right and top-right → bottom-left, on a wall drawn
    either way, with Keep = Auto: each keeps the supported side — the same
    retreating physical result mirrored, the same support outcome."""
    out = {}
    for rev in (False, True):
        doc = line_doc(system, params, length=120)
        if rev:
            doc['source_paths'][0].update(start=[120, 0], end=[0, 0])
        src = DesignerSource.from_designs(designs(doc))
        to_s = (lambda x: 120 - x) if rev else (lambda x: x)
        for diag, (xl, xu) in (('TL_BR', (120, 40)), ('TR_BL', (0, 80))):
            p = taper('T', WallRef('L'), 'auto', to_s(xl), 0, to_s(xu), 72)
            a = Assembly(1.5, [Section('A', 72)], profiles=[p])
            res, rep, geo = stack(src, a)
            assert rep[0].valid, (rev, diag, rep[0].errors)
            ext = []
            for L in rep[0].layers:
                xs = [x for q in geo[res.instances[L['layer']].geometry_key][0] for x, _ in q['pts']]
                ext.append((round(min(xs), 1), round(max(xs), 1)))
            out[(rev, diag)] = (ext, len(analyse_support(res, geo, a).findings))
    for diag in ('TL_BR', 'TR_BL'):
        assert out[(False, diag)][0] == out[(True, diag)][0]          # wall direction does not matter
    mirror = lambda ext: [(round(120 - b, 1), round(120 - a, 1)) for a, b in ext]
    assert out[(False, 'TL_BR')][0] == mirror(out[(False, 'TR_BL')][0])  # the two diagonals mirror
    assert len({v[1] for v in out.values()}) == 1                         # … with the same support outcome


def _printed_gap(polys, c, bead=BEAD):
    """(left, right) CLEAR edges of the printed opening around x = c — the
    bead EDGES (centre line ± ½ bead) of the innermost material beside it
    (segments within the wall band projected on x); (c, c) when the layer is
    closed there. (The overhang rule is about bead edges: how far a bead
    hangs past the bead below.)"""
    iv = sorted((min(a[0], b[0]), max(a[0], b[0])) for a, b in _segments(polys)
                if abs(a[1]) <= T / 2 + 1e-6 and abs(b[1]) <= T / 2 + 1e-6)
    left, right = -math.inf, math.inf
    for x0, x1 in iv:
        if x0 <= c + 1e-6 and x1 >= c - 1e-6:
            return (c, c)
        if x1 < c:
            left = max(left, x1)
        if x0 > c:
            right = min(right, x0)
    left, right = left + 0.5 * bead, right - 0.5 * bead
    return (c, c) if right - left <= 1e-6 else (left, right)


FAMILIES = [('hollow', {}), ('skin_web', {}), ('interleaved', {'paths': 4}), ('linked', {'paths': 4}), ('chain', {})]


@pytest.mark.parametrize('system,params', FAMILIES, ids=[f[0] for f in FAMILIES])
@pytest.mark.parametrize('lh,zb,height,width', [(1.5, 12, 36, 18), (1.25, 10.3, 37.1, 15.4), (1.75, 7, 30, 30)],
                         ids=['aligned', 'tip_between_layers', 'too_steep_constrained'])
def test_printed_diamond_closure_obeys_maximum_overhang(system, params, lh, zb, height, width):
    """REVIEW 4 HARD RULE, on the PRINTED geometry of every family: through
    the complete opening — closed, smallest, wider …, widest, … narrower,
    smallest, closed — no surviving side advances inward by more than
    Maximum overhang between consecutive layers, the final closure included
    (the printed opening edge = the innermost bead centre line beside the
    hole; a closed layer's edge is the opening's centre)."""
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=160.0)))
    # (too steep as typed: the UI's constraints keep the width and raise the
    # UPPER height — 2026-10-10 — and the PRINTED result of that shape is checked)
    s, _ = OS.constrain({'shape': 'diamond', 'upper': 'pointed', 'lower': 'pointed', 'width': width, 'height': height,
                         'z_bottom': zb, 'array': None}, 'width', lh, 1.5, 3.0)
    a = Assembly(lh, [Section('A', 72)], profiles=[OpeningProfile(
        'P', WallRef('L'), 80, zb, s['height'], width, upper=s['upper'], lower=s['lower'], middle=s['middle'],
        middle_height=s['middle_height'], upper_height=s['upper_height'], bottom_depth=s['bottom_depth'])])
    res, rep, geo = stack(src, a)
    assert rep[0].valid, rep[0].errors
    h = a.max_overhang
    cut = [L['layer'] for L in rep[0].layers]
    lo_k, hi_k = cut[0] - 1, cut[-1] + 1
    edges = []
    for k in range(lo_k, hi_k + 1):
        polys = geo[res.instances[k].geometry_key][0]
        edges.append(_printed_gap(polys, 80.0))
    assert edges[0] == (80.0, 80.0) and edges[-1] == (80.0, 80.0)        # closed below and above
    for (l0, r0), (l1, r1) in zip(edges, edges[1:]):
        assert l1 - l0 <= h + 1e-6 and r0 - r1 <= h + 1e-6, (edges, h)   # no side advances inward > h
    widths = [r - l for l, r in edges]
    peak = max(range(len(widths)), key=lambda i: widths[i])
    printed = [i for i, w in enumerate(widths) if w > 0]
    assert printed and widths[printed[0]] <= 2 * h + 1e-6 and widths[printed[-1]] <= 2 * h + 1e-6
    # (a true discrete point at both ends — on plain / skin walls the caps print ON the cut
    # face, so the narrowest cut layers print closed and the point starts a layer later)
    assert all(widths[i] <= widths[i + 1] + 1e-6 for i in range(peak)) and \
        all(widths[i] >= widths[i + 1] - 1e-6 for i in range(peak, len(widths) - 1))
    findings = analyse_support(res, geo, a).findings
    assert not [f for f in findings if f.status == 'needs_header'], 'a valid profile is headerless'


@pytest.mark.parametrize('system,params', [('skin_web', {}), ('interleaved', {'paths': 4}), ('linked', {'paths': 4}),
                                           ('chain', {})], ids=['skin_web', 'interleaved', 'linked', 'chain'])
def test_flat_and_rounded_cut_caps_change_only_the_local_termination(system, params):
    """REVIEW 4: Flat ↔ Rounded at a taper's moving end changes only the end
    motif: with either cap style every cut layer is that style's uncut stack
    except at the moving end, and support finds nothing. Rounded used to re-plan the Skin + Web web (the local join only
    paired cut strands along a STRAIGHT end wall) and to give generated walls
    another end motif at the untouched far end whenever the cut piece was
    oriented the other way (the closing lane permutation and original-end
    stations followed the piece, not the reference)."""
    out = {}
    for cap in ('flat', 'full_round'):
        doc = line_doc(system, params, length=240.0)
        doc['cap_style'] = cap
        src = DesignerSource.from_designs(designs(doc))
        canon = src.geometry('A').polylines
        a = Assembly(1.5, [Section('A', 72)], profiles=[taper('T', WallRef('L'), 'auto', 240, 0, 186, 72)])
        res, rep, geo = stack(src, a)
        assert rep[0].valid
        for L in rep[0].layers:
            # every cut layer = ITS OWN cap style's uncut stack, except at the moving end
            polys = geo[res.instances[L['layer']].geometry_key][0]
            ch = [q for q in _changed_tol(polys, canon) if q[0] <= L['retained'] + 1e-6]
            assert all(q[0] >= L['retained'] - 1.6 * T for q in ch), (cap, L['layer'], min(q[0] for q in ch))
        fs = analyse_support(res, geo, a).findings
        out[cap] = fs
        assert len(fs) <= (1 if system == 'chain' else 0), (cap, [(f.layer, f.centre) for f in fs])


@pytest.mark.parametrize('system,params', [
    pytest.param('skin_web', {}, marks=pytest.mark.xfail(
        strict=True, reason='class C (stage Z4, under review): the opening pre-deformation ramps are retired for '
                           'zigzag / wave webs — the construction state makes the cut local without them')),
    ('interleaved', {'paths': 4}), ('linked', {'paths': 4}), ('chain', {})],
    ids=['skin_web', 'interleaved', 'linked', 'chain'])
def test_opening_cross_z_transition_is_local_in_z_and_in_plan(system, params):
    """REVIEW 4: the layers just below / above an opening print the uncut
    pattern pre-deformed toward the cut layer's end motif (transition ops,
    α stepping so no layer moves more than h); the change is local in plan,
    the layers beyond the transition are EXACTLY canonical, and the stack is
    headerless (Skin + Web / Interleaved / Linked: no findings at all)."""
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=240.0)))
    canon = src.geometry('A').polylines
    # (off the wall's middle: there the mirrored web passes already cross — nothing to pre-deform)
    a = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 100, 12, 36, 18, 'diamond')])
    res, rep, geo = stack(src, a)
    cut = [L['layer'] for L in rep[0].layers]
    trans = [i.index for i in res.instances if any(c[0] == 'transition' for c in i.cuts)]
    n = math.ceil((T - BEAD) / a.max_overhang - 1e-9) - 1              # (thickness − bead) / h
    assert sorted(trans) == list(range(cut[0] - n, cut[0])) + list(range(cut[-1] + 1, cut[-1] + n + 1))
    alphas = {i.index: next(c[6] for c in i.cuts if c[0] == 'transition') for i in res.instances if i.index in trans}
    assert [alphas[k] for k in range(cut[0] - n, cut[0])] == pytest.approx([(j + 1) / (n + 1) for j in range(n)], abs=1e-4)
    for i in res.instances:
        polys = geo[i.geometry_key][0]
        if i.index in trans:
            ch = _changed_tol(polys, canon)
            assert ch and all(abs(q[0] - 100) <= 1.6 * T for q in ch)       # pre-deformed, locally
        elif i.index not in cut:
            assert not _changed_tol(polys, canon), i.index                  # beyond the transition: canonical
    fs = analyse_support(res, geo, a).findings
    assert not [f for f in fs if f.status == 'needs_header']
    if system != 'chain':
        assert fs == [], [(f.layer, f.centre) for f in fs]


def test_invalid_profile_leaves_the_wall_solid():
    """An opening that does not fit the wall (jambs) is NOT generated: every
    layer prints the canonical design. An over-steep one is NOT generated
    either (2026-10-10: reported with the printable height / width — the UI
    constrains it while editing; it is never silently extended)."""
    src = DesignerSource.from_designs(designs(line_doc('hollow')))
    a = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 12, 6, 24, 20)])
    res, rep, geo = stack(src, a)
    assert not rep[0].valid and 'does not fit' in rep[0].errors[0] and set(geo) == {'A'}
    assert analyse_support(res, geo, a).findings == []
    steep = Assembly(1.5, [Section('A', 72)], profiles=[OpeningProfile('P', WallRef('L'), 60, 6, 24, 36)])
    res, rep, geo = stack(src, steep)
    assert not rep[0].valid and 'not self-supporting' in rep[0].errors[0] and set(geo) == {'A'}
    assert rep[0].hint['field'] == 'upper_height' and rep[0].hint['min_height'] > 12 and rep[0].hint['max_width'] < 36
    assert not [f for f in analyse_support(res, geo, steep).findings if f.status == 'needs_header']


def test_opening_on_a_rectangle_edge_with_corner_radius():
    rect = {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': 160, 'h': 120,
            'closed': True, 'corner_radius': 12}
    doc = {'id': 'l', 'source_paths': [rect], 'material': dict(MAT),
           'wall_systems': [{'id': 'WS1', 'type': 'hollow', 'members': ['R'], 'thickness': T, 'params': {}}]}
    src = DesignerSource.from_designs(designs(doc))
    edges = {t['key'] if 'key' in t else f"{t['source']}#{t['edge']}": t for t in src.walls('A')}
    assert len(edges) == 4 and not any(t['taper'] for t in edges.values())
    e = next(t for t in src.walls('A') if t['edge'] == 2)              # the top edge, (160,120)→(0,120)
    g = src.geometry('A', None, opening_cut(40, 20, 'R', 2))           # 40 in from that edge's start
    assert not _inside(g.polylines, 110 + 1e-6, 130 - 1e-6, 120 - T / 2, 120 + T / 2)
    assert open_ends(src, 'A', opening_cut(40, 20, 'R', 2)) == []
    assert e['length'] == 160


@pytest.mark.parametrize('system,params', [('plain', {}), ('skin_web', {}), ('parallel', {'walls': 4})],
                         ids=['plain', 'skin_web', 'parallel4'])
def test_opening_layers_are_real_separated_capped_pieces(system, params):
    """REVIEW 2 (2026-10-08): the resolved layer — not an overlay — has the
    perforation: the wall is split into two pieces, each closed by its own
    caps exactly on the cut faces, and the Designer request the UI makes
    (resolve → variants → /geometry with their cuts) returns that geometry."""
    src = DesignerSource.from_designs(designs(line_doc(system, params)))
    canon = src.geometry('A').polylines
    g = src.geometry('A', None, opening_cut(60, 24)).polylines
    assert g != canon
    if system == 'parallel':                                          # N lanes → one closed loop per piece
        assert [p['closed'] for p in canon] == [True] and [p['closed'] for p in g] == [True, True]
        assert max(x for x, _ in g[0]['pts']) <= 48 + 1e-6 and min(x for x, _ in g[1]['pts']) >= 72 - 1e-6
    else:
        ids = {p['id'] for p in g}
        assert {'L.wall+~0', 'L.wall+~1', 'L.wall-~0', 'L.wall-~1'} <= ids     # both faces split in two
        caps = {p['id']: p['pts'] for p in g if p['id'] in ('L~0_ce', 'L~1_cs')}
        assert set(caps) == {'L~0_ce', 'L~1_cs'}                              # each piece capped at the cut
        assert all(abs(x - 48) < 1e-6 for x, _ in caps['L~0_ce']) and all(abs(x - 72) < 1e-6 for x, _ in caps['L~1_cs'])
    # the UI path: resolve, then fetch every variant WITH its cuts
    from app import app
    c = app.test_client()
    ds = designs(line_doc(system, params))
    body = {'assembly': {'layer_height': 1.5, 'max_overhang': 1.5, 'sections': [{'design_id': 'A', 'height': 72}],
                         'profiles': [OpeningProfile('P', WallRef('L'), 60, 6, 48, 24).to_dict()]},
            'design_ids': ['A'], 'designs': ds}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    need = {k: {'design_id': v['design_id'], 'transforms': v['transforms'], 'cuts': v.get('cuts') or []}
            for k, v in out['variants'].items()}
    geo = c.post('/api/assembly/geometry', json={'designs': ds, 'variants': need}).get_json()['designs']
    widest = max(out['profiles'][0]['layers'], key=lambda L: L['width'])
    k = out['instances'][widest['layer']]['geometry_key']
    polys = geo[k]['polylines']
    assert not _inside(polys, 60 - widest['width'] / 2 + BEAD / 2 + 1e-6, 60 + widest['width'] / 2 - BEAD / 2 - 1e-6, -T / 2, T / 2)
    assert polys != canon


@pytest.mark.parametrize('system', ['plain', 'hollow'])
def test_reversed_source_path_and_mirrored_profile_print_the_same(system):
    """A wall drawn the other way with the MIRRORED cut / the opening at
    L − s resolves to the same physical layers."""
    fwd = DesignerSource.from_designs(designs(line_doc(system)))
    rev_doc = line_doc(system)
    rev_doc['source_paths'][0].update(start=[120, 0], end=[0, 0])
    rev = DesignerSource.from_designs(designs(rev_doc))
    def same(A, B, tol=1e-3):           # every vertex of each lies on the other's geometry
        for P, Q in ((A, B), (B, A)):
            segs = list(_segments(Q))
            if any(min(_seg_dist(q, a, b) for a, b in segs) > tol for p in P for q in p['pts']):
                return False
        return bool(A) == bool(B)
    tp = taper('T', WallRef('L'), 'start', 120, 6, 50, 66)
    for profs_f, profs_r in (([tp], [tp.mirrored(120.0)]),
                             ([OpeningProfile('P', WallRef('L'), 45, 6, 48, 18, 'lens')],
                              [OpeningProfile('P', WallRef('L'), 75, 6, 48, 18, 'lens')])):
        a_f = Assembly(1.5, [Section('A', 72)], profiles=profs_f)
        a_r = Assembly(1.5, [Section('A', 72)], profiles=profs_r)
        rf, pf, gf = stack(fwd, a_f)
        rr, pr, gr = stack(rev, a_r)
        assert pf[0].valid and pr[0].valid
        for k in range(len(rf.instances)):
            A, B = gf[rf.instances[k].geometry_key][0], gr[rr.instances[k].geometry_key][0]
            assert (not A and not B) or same(A, B), k


# ---- tapers ---------------------------------------------------------------------------------------

def _max_x(polys):
    return max((x for p in polys for x, _ in p['pts']), default=None)


@pytest.mark.parametrize('upper', [0, 20])
@pytest.mark.parametrize('system,params', [('plain', {}), ('skin_web', {}), ('parallel', {'walls': 4}),
                                           ('interleaved', {'paths': 4}), ('chain', {})],
                         ids=['plain', 'skin_web', 'parallel4', 'interleaved4', 'chain'])
def test_linear_taper(system, params, upper):
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=60)))
    a = Assembly(1.5, [Section('A', 84)], profiles=[taper('T', WallRef('L'), 'start', 60, 0, upper, 72)])
    res, rep, geo = stack(src, a)
    assert rep[0].valid
    gen = system not in ('plain', 'skin_web')
    for L in rep[0].layers:
        polys = geo[res.instances[L['layer']].geometry_key][0]
        if L['retained'] == 0:
            assert polys == [], 'the wall is absent where nothing is retained'
            continue
        assert _max_x(polys) <= L['retained'] + 1e-6                     # nothing past the retained end
        assert _max_x(polys) >= L['retained'] - BEAD - 1e-6
        assert not gen or L['retained'] >= 2 * T                         # no stub of a generated wall
    if upper:
        assert all(L['retained'] == 20 for L in rep[0].layers[48:])      # holds above the upper Z
    else:
        assert rep[0].layers[-1]['retained'] == 0
    fs = analyse_support(res, geo, a).findings
    if system in ('interleaved', 'chain'):
        # the pattern is the uncut one (phase reference); KNOWN: the generated END
        # MOTIF retreating with the cut may be reported at the moving end (overlap)
        ends = {L['layer']: L['retained'] for L in rep[0].layers}
        assert all(f.kind == 'overlap' and abs(f.centre[0] - ends.get(f.layer, 0)) <= 2 * T for f in fs), \
            [(f.layer, f.centre) for f in fs]
    else:                         # a retreating end: no overhang; the Skin + Web web is clipped + joined
        assert fs == [], [(f.layer, f.kind) for f in fs]


def test_tapered_layers_are_genuinely_shortened_source_geometry():
    src = DesignerSource.from_designs(designs(line_doc('hollow', length=120)))
    for keep, line in (('start', (120, 0, 30, 72)), ('end', (0, 0, 90, 72))):
        a = Assembly(1.5, [Section('A', 72)], profiles=[taper('T', WallRef('L'), keep, *line)])
        res, rep, geo = stack(src, a)
        assert rep[0].valid
        for L in rep[0].layers[::7]:
            built = src.lib.build('A', cuts=res.instances[L['layer']].cut_spec())
            (line_,) = [d for d in built.document['source_paths'] if d['id'] == 'L']
            xs = sorted((line_['start'][0], line_['end'][0]))
            kept = (0, L['cut']) if keep == 'start' else (L['cut'], 120)
            assert xs == pytest.approx(list(kept), abs=1e-3)               # the SOURCE wall is shorter
            polys = geo[res.instances[L['layer']].geometry_key][0]
            cap = [p for p in polys if p['id'] == ('L_ce' if keep == 'start' else 'L_cs')]
            assert cap and all(abs(x - L['cut']) < 1e-3 for x, _ in cap[0]['pts'])   # capped at the cut
            xs_all = [x for p in polys for x, _ in p['pts']]
            assert min(xs_all) >= kept[0] - 1e-6 and max(xs_all) <= kept[1] + 1e-6


@pytest.mark.xfail(strict=True, reason='KNOWN (class A, Z4): the room\'s web re-phases everywhere when the attached line '
                                       'tapers away — Pass A derives ONE pitch per region from the median cavity, and the '
                                       'region\'s run set changed (14.18 → 14.43 in); invariants 4 / 6 (local consumption, '
                                       'junctions are local events) — WALL_ARCHITECTURE.md Z.6')
def test_taper_to_zero_removes_only_that_wall():
    """A line attached to a room tapers away: at 0 the layer prints the
    room's walls exactly as the room alone (its web keeps its anchor:
    profile_cuts re-anchors it) and the room's web stays the web of the
    layers below (clipped, joined only at the former junction — local)."""
    rect = {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': 120, 'h': 120, 'closed': True}
    doc = line_doc('skin_web', length=200, extra=[rect])
    doc['source_paths'][0].update(start=[120, 60], end=[240, 60])       # out of the room's right wall
    doc['infills'][0]['path_id'] = 'L'                                   # the web anchored on the line
    alone = json.loads(json.dumps(doc))
    alone['source_paths'] = [rect]
    alone['wall_systems'][0]['members'] = ['R']
    alone['infills'][0]['path_id'] = 'R'
    src = DesignerSource.from_designs(designs(doc))
    ref = DesignerSource.from_designs(designs(alone)).geometry('A')
    g = src.geometry('A', None, [{'op': 'extent', 'source': 'L', 'edge': None, 'end': 'end',
                                  'retained': 0, 'profile': 'T'}])
    assert g.diagnostics['profile'][0]['removed']
    norm = lambda polys: sorted(json.dumps([[round(x, 6), round(y, 6)] for x, y in p['pts']]) for p in polys)
    walls = lambda geo: [p for p in geo.polylines if not str(p['id']).startswith('WEB')]
    assert walls(g) and norm(walls(g)) == norm(walls(ref))
    far = lambda geo: sorted((round(x, 6), round(y, 6)) for p in geo.polylines if str(p['id']).startswith('WEB')
                             for x, y in p["pts"] if x < 121 and math.hypot(x - 120, y - 60) > 20)
    assert far(g) and far(g) == far(src.geometry('A'))
    assert open_ends(src, 'A', [{'op': 'extent', 'source': 'L', 'edge': None, 'end': 'end',
                                 'retained': 0, 'profile': 'T'}]) == []
    shorter = src.geometry('A', None, [{'op': 'extent', 'source': 'L', 'edge': None, 'end': 'end',
                                        'retained': 60, 'profile': 'T'}])
    assert 175 < _max_x(shorter.polylines) <= 180 + 1e-6                 # still T-joined, shortened
    assert open_ends(src, 'A', [{'op': 'extent', 'source': 'L', 'edge': None, 'end': 'end',
                                 'retained': 60, 'profile': 'T'}]) == []


@pytest.mark.parametrize('system', ['skin_web', 'hollow', 'plain'])
def test_short_local_buttress_taper(system):
    """A free-standing buttress wall attached to a room: only its OUTER
    30 in are cut, by a diagonal between two elevation points (keep the
    attached start). The room and the rest of the buttress stay the layers
    below; routes close; support finds nothing."""
    rect = {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': 120, 'h': 120, 'closed': True}
    doc = line_doc(system, length=80, extra=[rect] if system != 'plain'
                   else [dict(rect, envelope={'thickness': T, 'align': 'auto', 'print_reference': False})])
    doc['source_paths'][0].update(start=[120, 60], end=[200, 60])
    src = DesignerSource.from_designs(designs(doc))
    (w,) = [t for t in src.walls('A') if t['source'] == 'L']
    assert w['taper'] and w['attached'] == ['start']                    # the default retained side
    canon = src.geometry('A').polylines
    a = Assembly(1.5, [Section('A', 72)], profiles=[taper('T', WallRef('L'), 'start', 80, 12, 50, 60)])
    res, rep, geo = stack(src, a)
    assert rep[0].valid
    for L in rep[0].layers:
        assert 50 - 1e-6 <= L['retained'] <= 80 and L['cut'] == L['retained']
        polys = geo[res.instances[L['layer']].geometry_key][0]
        assert _max_x(polys) <= 120 + L['retained'] + 1e-6
        far = [q for q in _changed(polys, canon) if q[0] < 120 + L['retained'] - T - 1e-6]
        assert far == [], f'layer {L["layer"]}: changed away from the cut'
        if L['layer'] % 6 == 0:
            assert open_ends(src, 'A', res.instances[L['layer']].cut_spec()) == []
    assert not res.instances[7].cuts                                     # below the lower point: unaffected
    assert all(L['retained'] == 50 for L in rep[0].layers if L['z'] > 60)
    findings = analyse_support(res, geo, a).findings
    assert findings == [], [(f.layer, f.kind) for f in findings]


def test_taper_coexists_with_group_and_section_transforms():
    """Cuts first (design coordinates), then the semantic transform of a
    transform group, then the placement: the shortened wall is moved like
    any wall; section transforms change placements only."""
    src = DesignerSource.from_designs(designs(line_doc('hollow', length=120)))
    fps = {'A': Footprint.of_bbox(*bbox_of(src.geometry('A').polylines))}
    prof = taper('T', WallRef('L'), 'start', 120, 0, 60, 72)
    tf = SectionTransform('linear', (0.125, 0.0), 0.0625)
    r0 = resolve(Assembly(1.5, [Section('A', 72, '', tf)]), ['A'], fps)
    a1 = Assembly(1.5, [Section('A', 72, '', tf)], profiles=[prof])
    r1, rep, geo = stack(src, a1, fps=fps)
    assert [i.transform for i in r0.instances] == [i.transform for i in r1.instances]
    assert rep[0].valid and analyse_support(r1, geo, a1).findings == []
    cuts = r1.instances[30].cut_spec()
    doc_t, report = src.lib.transformed('A', {'L': (0.5, 10.0, 0.0)}, cuts)
    line = next(d for d in doc_t['source_paths'] if d['id'] == 'L')
    kept = cuts[0]['retained']
    assert line['end'][0] - line['start'][0] == pytest.approx(0.5 * kept) and report['profile'][0]['applied']


def test_taper_across_a_section_boundary_into_a_derived_design():
    doc = line_doc('parallel', {'walls': 4}, length=120)
    door = {'openings': {'door': {'source_path_id': 'L', 'center_s': 20, 'width': 16}}}
    src = DesignerSource.from_designs(designs(doc, door))
    a = Assembly(1.5, [Section('A', 36), Section('B', 36)],
                 profiles=[taper('T', WallRef('L'), 'end', 20, 24, 80, 60)])
    res, rep, geo = stack(src, a, ids=('A', 'B'))
    assert rep[0].valid
    r = {L['layer']: L for L in rep[0].layers}
    assert min(r) == 16 and not res.instances[15].cuts                  # below its lower Z: unaffected
    assert r[23]['design'] == 'A' and r[24]['design'] == 'B'
    for k in (20, 30, 47):
        polys = geo[res.instances[k].geometry_key][0]
        xs = [x for p in polys for x, _ in p['pts']]
        assert min(xs) >= 120 - r[k]['retained'] - 1e-6                  # the START retreats
    # the derived design's own door (centre 20 in from the start) is gone once the start passes it
    assert any(c.get('op') == 'extent' for c in res.instances[30].cut_spec())
    built = src.lib.build('B', cuts=res.instances[30].cut_spec())
    assert built.document['openings'] == []                             # its stretch of wall is gone
    built = src.lib.build('B', cuts=res.instances[24].cut_spec())        # retained 100: door re-measured
    assert [round(o['center_s'], 6) for o in built.document['openings']] == [round(20 - (120 - r[24]['retained']), 6)] \
        or built.document['openings'] == []
    assert analyse_support(res, geo, a).findings == []


# ---- isolation / coexistence -----------------------------------------------------------------------

def test_designer_output_without_cuts_is_unchanged():
    src = DesignerSource.from_designs(designs(line_doc('skin_web')))
    g0 = src.geometry('A')
    assert src.geometry('A', None, None) is g0 and src.geometry('A', {}, []) is g0
    lib = src.lib
    assert lib.build('A').printable == lib.build('A', cuts=None).printable == lib.build('A', cuts=[]).printable
    fresh = DesignerSource.from_designs(designs(line_doc('skin_web'))).geometry('A')
    assert fresh.polylines == g0.polylines


def test_assembly_without_profiles_resolves_identically():
    src = DesignerSource.from_designs(designs(line_doc('parallel', {'walls': 4})))
    fps = {'A': Footprint.of_bbox(*bbox_of(src.geometry('A').polylines))}
    a = Assembly(1.5, [Section('A', 36, '', SectionTransform('linear', (0.25, 0), 0.25))])
    res0 = resolve(a, ['A'], fps)
    res1, rep, geo = stack(src, a, fps=fps)
    # (the stack's layers carry a construction state and, scaled, are their own
    # variants — invariant 17; the Assembly resolve itself is the same)
    strip = lambda k: k.split('|st:')[0]
    assert rep == [] and [i.transform for i in res1.instances] == [i.transform for i in res0.instances]
    assert [strip(i.geometry_key) for i in res1.instances] == [i.geometry_key for i in res0.instances]


def test_profiles_coexist_with_section_and_group_transforms():
    doc = line_doc('hollow', length=120)
    src = DesignerSource.from_designs(designs(doc))
    fps = {'A': Footprint.of_bbox(*bbox_of(src.geometry('A').polylines))}
    tf = SectionTransform('linear', (0.125, 0.0), 0.0625)
    prof = OpeningProfile('P', WallRef('L'), 60, 6, 48, 24)
    a0 = Assembly(1.5, [Section('A', 72, '', tf)])
    a1 = Assembly(1.5, [Section('A', 72, '', tf)], profiles=[prof])
    r0 = resolve(a0, ['A'], fps)
    r1, rep, geo = stack(src, a1, fps=fps)
    assert [i.transform for i in r0.instances] == [i.transform for i in r1.instances]   # placement untouched
    assert rep[0].valid and analyse_support(r1, geo, a1).findings == []
    # a transform group scaling the wall: cuts are applied FIRST, then the semantic transform
    # (the opening's position scales with its host, its width stays physical — like any opening)
    cuts = r1.instances[24].cut_spec()
    doc_t, report = src.lib.transformed('A', {'L': (0.5, 10.0, 0.0)}, cuts)
    op = next(o for o in doc_t['openings'] if o['id'] == 'asm-P')
    assert op['center_s'] == pytest.approx(30.0) and op['width'] == pytest.approx(cuts[0]['width'])
    assert report['profile'][0]['applied']
    from layer_assembly.groups import build_groups
    g = src.geometry('A')
    grp = build_groups({'A': (g.polylines, g.bead_width)}, {'A': None}, ['A'],
                       [TransformCenter('c1', 60, 0, ('L',))], g.sources)
    a2 = Assembly(1.5, [Section('A', 72, '', SectionTransform('linear', (0, 0), 0.0625))],
                  center_mode='multiple', centers=[TransformCenter('c1', 60, 0, ('L',))], profiles=[prof])
    fps2 = {'A': Footprint(60, 0, 120)}
    r2, rep2, geo2 = stack(src, a2, fps=fps2, groups=grp)
    both = [i for i in r2.instances if i.semantic and i.cuts]
    assert both and all('|cut:' in i.geometry_key and i.geometry_key in geo2 for i in both)


def test_lattice_regenerates_from_profiled_geometry():
    src = DesignerSource.from_designs(designs(line_doc('skin_web')))
    base = src.lib.build('A')
    cut = src.lib.build('A', cuts=opening_cut(60, 24))
    assert base.lattice and cut.lattice
    pts = [(p.x, p.y) for polys in cut.lattice.values() for pl in polys for p in pl]
    assert not [q for q in pts if 48 + 1e-6 < q[0] < 72 - 1e-6], 'web lattice inside the opening'
    assert any(q[0] < 48 for q in pts) and any(q[0] > 72 for q in pts)      # web on both pieces
    short = src.lib.build('A', cuts=[{'op': 'extent', 'source': 'L', 'edge': None, 'end': 'end',
                                      'retained': 70, 'profile': 'T'}])
    xs = [p.x for polys in short.lattice.values() for pl in polys for p in pl]
    assert xs and max(xs) <= 70 + 1e-6


def test_walls_report_and_minimum_length():
    src = DesignerSource.from_designs(designs(line_doc('parallel', {'walls': 4})))
    (w,) = src.walls('A')
    assert w['taper'] and w['thickness'] == T and w['min_length'] == 2 * T
    assert DesignerSource.from_designs(designs(line_doc('hollow'))).walls('A')[0]['min_length'] == 0


# ---- endpoints / workers --------------------------------------------------------------------------

def test_resolve_endpoint_reports_profiles_and_cut_variants():
    from app import app
    c = app.test_client()
    ds = designs(line_doc('parallel', {'walls': 4}))
    prof = [OpeningProfile('P', WallRef('L'), 60, 6, 48, 18, 'lens').to_dict(),
            taper('T', WallRef('L'), 'start', 120, 60, 90, 72).to_dict()]
    body = {'assembly': {'layer_height': 1.5, 'max_overhang': 1.5, 'sections': [{'design_id': 'A', 'height': 72}], 'profiles': prof},
            'design_ids': ['A'], 'designs': ds}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    assert 'error' not in out, out
    assert [r['id'] for r in out['profiles']] == ['P', 'T'] and all(r['valid'] for r in out['profiles'])
    assert out['profile_targets']['A'][0]['source'] == 'L'
    cutv = {k: v for k, v in out['variants'].items() if any(c['op'] != 'station' for c in v.get('cuts') or [])}
    assert cutv and all(v['profile'] and all(x['applied'] for x in v['profile']) for v in cutv.values())
    assert not [f for f in out['support']['findings'] if f['status'] == 'needs_header']
    k, v = next(iter(cutv.items()))
    g = c.post('/api/assembly/geometry', json={'designs': ds, 'variants': {k: {'design_id': 'A', 'transforms': {},
                                                                              'cuts': v['cuts']}}}).get_json()
    assert g['designs'][k]['polylines']
    none = c.post('/api/assembly/resolve', json=dict(body, assembly=dict(body['assembly'], profiles=[]))).get_json()
    assert none['profiles'] == [] and 'variants' not in none


def test_cut_variants_build_in_worker_processes(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '2')
    doc = line_doc('hollow')
    a = Assembly(1.5, [Section('A', 36)], profiles=[OpeningProfile('P', WallRef('L'), 60, 3, 30, 18)])
    src = DesignerSource.from_designs(designs(doc))
    res, rep, geo = stack(src, a)
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')
    ref = DesignerSource.from_designs(designs(doc))
    _, _, geo0 = stack(ref, a)
    assert set(geo) == set(geo0) and all(geo[k][0] == geo0[k][0] for k in geo)

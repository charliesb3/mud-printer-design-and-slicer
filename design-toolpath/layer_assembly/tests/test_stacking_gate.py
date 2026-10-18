"""
SIMPLE SKIN + WEB STACKING GATE — Construction through Z, milestone 1
(WALL_ARCHITECTURE.md §Z.0 and its 2026-10-18 clarifications).

A straight open Skin + Web wall (a two-pass lone loop, cap V at each end) and
a plain rectangular Skin + Web wall (a single-pass corner-motif loop), zigzag,
under Assembly transforms, resolved exactly as Generate resolves them. Every
check reads the DEPOSITED geometry (world coordinates), never the state:

  - every interior landing has its predecessor below at the same position
    after the host's legitimate rigid motion (none under a scale; the
    translation itself under a pure translation) and perpendicular
    accommodation to the moved face: no longitudinal displacement;
  - landings are born / consumed only in the transition band at a moving end
    or corner; both ends consume independently;
  - at an N → N−1 event the survivors are unchanged;
  - no cell exceeds 1.5 nominal pitches; no lattice segment is a long
    corrective stitch;
  - the bead-level support checker finds nothing;
  - the route keeps the topology of the first layer (no extra travel);
  - the printed wall thickness is constant.
"""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..', '..', 'design_proto'), os.path.join(HERE, '..', '..'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('LAYER_ASSEMBLY_WORKERS', '0')

from test_profiles_designer import line_doc, BEAD, T          # noqa: E402  (10 in Skin + Web, 3 in bead)

pytestmark = pytest.mark.integration

TOL = 1e-3            # in: "the same position"


def _doc(kind):
    doc = line_doc('skin_web', {}, length=240.0)
    if kind == 'rect':
        doc['source_paths'] = [{'id': 'L', 'type': 'RectanglePath', 'label': 'Room', 'closed': True,
                                'x': 0, 'y': 0, 'w': 160, 'h': 100}]
    return doc


def _asm(layers, scale_in=0.0, shift=(0.0, 0.0), centers=None):
    a = {'layer_height': 1.5, 'max_overhang': 1.5,
         'sections': [{'design_id': 'A', 'height': 1.5 * layers,
                       'transform': {'profile': 'linear', 'shift': list(shift), 'scale_in': scale_in}}],
         'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0},
         'center_mode': 'footprint', 'center_counter': 0, 'centers': [], 'objects': [], 'overrides': [],
         'profiles': []}
    if centers:
        a.update(center_mode='multiple', center_counter=len(centers), centers=centers)
    return a


def build(kind, asm):
    """[{'inst', 'lattice': [world polylines], 'skins': [...], 'moves'}], support report."""
    from layer_assembly import web
    from layer_assembly.source import layer_geometry
    designs = [{'id': 'A', 'name': 'A', 'parent': None, 'document': _doc(kind)}]
    st = web._resolve_stack({'assembly': asm, 'designs': designs, 'design_ids': ['A']})
    src, res = st['src'], st['res']
    layers, geo = [], dict(st['geo'])
    for inst in res.instances:
        g = layer_geometry(src, inst)
        if inst.is_variant and inst.geometry_key not in geo:
            geo[inst.geometry_key] = (g.polylines, g.bead_width)
        tr = inst.transform
        attrs = (g.diagnostics.get('routing') or {}).get('strands') or []
        lat, skins = [], []
        for j, pl in enumerate(g.polylines):
            pts = [tr.apply(x, y) for x, y in pl['pts']]
            (lat if (attrs[j] if j < len(attrs) else {}).get('role') == 'lattice' else skins).append(pts)
        moves = src.route(g)[0]
        layers.append({'inst': inst, 'lattice': lat, 'skins': skins,
                       'travel': sum(1 for m in moves if getattr(m, 'kind', '') == 'travel')})
    report = web._support_report(st, geo)
    return layers, report


# ---- landing extraction (world coordinates) ----------------------------------------

def _frames(kind, L):
    """[(axis 'x' | 'y', centre line coordinate, lo, hi)] of the straight wall
    stretches of a layer, from its skins (the faces' centre lines)."""
    xs = [q[0] for pl in L['skins'] for q in pl]
    ys = [q[1] for pl in L['skins'] for q in pl]
    if kind == 'line':
        return [('x', 0.5 * (min(ys) + max(ys)), min(xs) + T / 2, max(xs) - T / 2)]
    rings = sorted(L['skins'], key=lambda pl: max(q[0] for q in pl) - min(q[0] for q in pl))
    o, i = rings[-1], rings[0]                                   # the outer and the inner skin ring

    def bb(pl):
        return min(q[0] for q in pl), max(q[0] for q in pl), min(q[1] for q in pl), max(q[1] for q in pl)
    x0, x1, y0, y1 = [0.5 * (u + v) for u, v in zip(bb(o), bb(i))]  # the centre-line box
    return [('x', y0, x0, x1), ('x', y1, x0, x1), ('y', x0, y0, y1), ('y', x1, y0, y1)]


def _rail(kind, L0):
    """The landings' distance from a wall's centre line (measured once)."""
    ax, c, lo, hi = _frames(kind, L0)[0]
    from collections import Counter
    ds = Counter(round(abs(q[1] - c), 3) for pl in L0['lattice'] for q in pl
                 if lo + 20 < q[0] < hi - 20 and abs(q[1] - c) < T / 2)
    return ds.most_common(1)[0][0]                      # (the contact rail; a transfer landing sits on the face)


def landings(kind, L, rail):
    """{frame index: sorted [(along-wall position, face ±1)]} of a layer's
    deposited landings on its straight stretches (vertices on a contact rail,
    the end caps and corner motifs excluded by position)."""
    out = {}
    for k, (ax, c, lo, hi) in enumerate(_frames(kind, L)):
        got = set()
        for pl in L['lattice']:
            for q in pl:
                u, v = (q[0], q[1] - c) if ax == 'x' else (q[1], q[0] - c)
                if (abs(abs(v) - rail) < 0.02 or abs(abs(v) - (T - BEAD) / 2) < 0.02) \
                        and lo - 1e-6 <= u <= hi + 1e-6:
                    got.add((round(u, 4), 1 if v > 0 else -1))
        out[k] = sorted(got)
    return out


# ---- the checks ----------------------------------------------------------------------

def check_stack(kind, layers, report, band, rigid=None):
    """Assert the gate on every adjacent pair. rigid(i) → (dx, dy) of the
    host's legitimate rigid motion between layers i − 1 and i (None: no rigid
    part — a scale)."""
    rail = _rail(kind, layers[0])
    lands = [landings(kind, L, rail) for L in layers]
    p0 = sorted(y[0] - x[0] for x, y in zip(lands[0][0], lands[0][0][1:]) if y[0] - x[0] > 0.1)
    p0 = p0[len(p0) // 2]
    seg0 = max(math.dist(a, b) for pl in layers[0]['lattice'] for a, b in zip(pl, pl[1:]))
    deaths = births = 0
    for i in range(1, len(layers)):
        frames = _frames(kind, layers[i])
        for k, (ax, c, lo, hi) in enumerate(frames):
            a, b = lands[i - 1][k], lands[i][k]
            shift = 0.0
            if rigid is not None:
                dx, dy = rigid(i)
                shift = dx if ax == 'x' else dy
            a = [(u + shift, f) for u, f in a]

            def interior(u):
                return lo + band < u < hi - band
            gone = [q for q in a if interior(q[0]) and not any(abs(q[0] - r[0]) < TOL and q[1] == r[1] for r in b)]
            new = [q for q in b if interior(q[0]) and not any(abs(q[0] - r[0]) < TOL and q[1] == r[1] for r in a)]
            assert not gone and not new, (kind, 'layer', i, 'frame', k, 'interior moved / consumed', gone[:4], new[:4])
            deaths += sum(1 for q in a if not any(abs(q[0] - r[0]) < TOL and q[1] == r[1] for r in b))
            births += sum(1 for q in b if not any(abs(q[0] - r[0]) < TOL and q[1] == r[1] for r in a))
            us = sorted({u for u, _ in b})
            cells = [y - x for x, y in zip(us, us[1:])]
            if kind == 'rect':
                cells = cells[1:-1]               # (the two cells across a corner motif: checked as segments below)
            assert not cells or max(cells) <= 1.5 * p0 + 0.05, (kind, 'layer', i, 'long cell', round(max(cells), 2), round(p0, 2))
        segmax = max(math.dist(q0, q1) for pl in layers[i]['lattice'] for q0, q1 in zip(pl, pl[1:]))
        assert segmax <= 1.5 * seg0, (kind, 'layer', i, 'long corrective stitch', round(segmax, 2), round(seg0, 2))
        assert layers[i]['travel'] == layers[0]['travel'], (kind, 'layer', i, 'route topology changed')
    assert not report.findings, [(f.layer, f.kind, round(f.span, 2)) for f in report.findings][:6]
    return {'p0': p0, 'deaths': deaths, 'births': births, 'lands': lands}


def thickness(kind, L):
    """The printed wall width: skin centre lines one bead in from the faces."""
    if kind == 'line':
        ys = [q[1] for pl in L['skins'] for q in pl]
        return round(max(ys) - min(ys) + BEAD, 3)
    boxes = sorted((max(q[0] for q in pl) - min(q[0] for q in pl)) for pl in L['skins'])
    return round((boxes[-1] - boxes[0]) / 2 + BEAD, 3)          # (the outer and the inner skin ring)


BAND = 2.5 * 7.0      # in: the transition band at a wall end / corner (two and a half nominal pitches)


def test_straight_wall_both_ends_consume_independently():
    layers, rep = build('line', _asm(12, scale_in=3.0))
    out = check_stack('line', layers, rep, BAND)
    firsts = [L[0][0][0] for L in out['lands']]
    lasts = [L[0][-1][0] for L in out['lands']]
    assert firsts[-1] > firsts[0] + 6 and lasts[-1] < lasts[0] - 6, 'both ends consumed'
    assert out['deaths'] and not out['births']
    assert len({thickness('line', L) for L in layers}) == 1 and abs(thickness('line', layers[0]) - T) < 0.01


def test_straight_wall_one_moving_end_consumes_locally():
    """The wall is its own transform group scaled about its LEFT end: the left
    end stands, the right end moves inward and consumes; the landings next to
    the standing end never change."""
    asm = _asm(12, scale_in=2.0, centers=[{'id': 'c1', 'x': 0.0, 'y': 0.0, 'sources': ['L']}])
    layers, rep = build('line', asm)
    out = check_stack('line', layers, rep, BAND)
    firsts = [L[0][:3] for L in out['lands']]
    assert all(f == firsts[0] for f in firsts), 'nothing changed at the standing end'
    assert out['lands'][-1][0][-1][0] < out['lands'][0][0][-1][0] - 6


def test_n_to_n_minus_one_never_redistributes_the_survivors():
    layers, rep = build('line', _asm(24, scale_in=1.0))
    out = check_stack('line', layers, rep, BAND)
    events = 0
    for a, b in zip(out['lands'], out['lands'][1:]):
        a, b = a[0], b[0]
        if len(b) < len(a):
            events += 1
            survivors = [q for q in a if q in b]
            assert len(a) - len(survivors) <= 4, 'one station (both faces) per end at most'
            assert all(q in a for q in b), 'every remaining landing is one of the landings below, unmoved'
    assert events, 'a repeat must have been consumed'


def test_expansion_creates_construction_only_in_newly_exposed_territory():
    layers, rep = build('line', _asm(16, scale_in=-1.0))     # (each end 1 in a layer outward: within overhang)
    out = check_stack('line', layers, rep, BAND)
    assert out['births'] and not out['deaths']
    assert len(out['lands'][-1][0]) > len(out['lands'][0][0])


def test_pure_translation_carries_the_construction_with_the_host():
    layers, rep = build('line', _asm(10, shift=(1.0, 0.0)))
    tx = [L['inst'].transform.to_dict()['tx'] for L in layers]
    assert tx[-1] - tx[0] > 5, tx

    def rigid(i):
        return (tx[i] - tx[i - 1], 0.0)
    out = check_stack('line', layers, rep, BAND, rigid=rigid)
    assert not out['births'] and not out['deaths'], 'a rigid motion consumes nothing'
    # … and it did NOT stay behind in world coordinates
    assert out['lands'][-1][0][5][0] - out['lands'][0][0][5][0] == pytest.approx(tx[-1] - tx[0], abs=TOL)


def test_rectangle_corners_consume_locally_without_reorganising_the_sides():
    layers, rep = build('rect', _asm(10, scale_in=1.0))      # (each side ≤ 1 in a layer: within overhang)
    out = check_stack('rect', layers, rep, BAND)
    assert out['deaths'], 'the corners consumed construction'
    assert len({thickness('rect', L) for L in layers}) == 1

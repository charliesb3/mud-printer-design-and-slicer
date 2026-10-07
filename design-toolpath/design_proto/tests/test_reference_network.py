"""
REFERENCE NETWORK REGRESSION (stabilization, 2026-10-06): the designer's
real topology — Circle 1 ∩ Rect 1 ∩ Rect 2, Line 1 joining Rect 2 to
Circle 1, one Network Wall (10 in), a wall lattice, a derived "gaps" design
with openings (tests/reference_network.py). Simpler fixtures kept passing
while this one fragmented; these tests pin:

  * ONE closed route per connected piece, shared lineage lattice, for no /
    one / two openings, an opening near a junction, on the curved wall,
    zigzag and wave, mitred and rounded junctions;
  * an opening reaching INTO another wall's material (the junction is
    rebuilt around it) closes by planning that member alone — reported;
  * Wall Thickness 0 = a SINGLE-BEAD path (never deleted, never an epsilon
    wall), and no wall lattice where there is no cavity;
  * derived junctions (intersecting forms) vs explicit connectors (Line 1),
    and the junction SEPARATION sweep under semantic transforms.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer
from layer_design import DesignLibrary
from graph import route_layer, compute_metrics, build_graph, closure_report
import reference_network as RN
import semantic_transform as ST
import source_attribution as SA

# named openings beyond the fixture's: (source, center_s, width)
EXTRA = {
    'near_r2': ('R1', 456.0, 24.0),          # Rect 1's bottom wall, 12 in clear of Rect 2's wall
    'into_r2': ('R1', 581.4, 24.0),          # Rect 1's left wall ACROSS Rect 2's top wall (overlaps it)
}


def opening(k):
    return RN.OPENINGS.get(k) or EXTRA[k]


def lib_of(pattern='zigzag', style='miter', openings=('rect1_top',), line_thickness=None, base=None):
    base = base or RN.document(pattern, line_thickness)
    base['junction_style'], base['junction_radius'] = style, 6
    cuts = {k: dict(zip(('source_path_id', 'center_s', 'width'), opening(k))) for k in openings}
    return DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base},
                          {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': cuts}}])


def route(lib, d, transforms=None):
    lay = _deserialise_layer(lib.transformed(d, transforms)[0])
    lay.lattice_reference = lib.lattice_reference(d, transforms) or None
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    return closure_report(build_graph(rl)), compute_metrics(route_layer(rl, allow_retrace=False)), m


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


CASES = [  # (pattern, style, openings) — A no openings … G wave
    ('zigzag', 'miter', ()),
    ('wave', 'round', ()),
    ('zigzag', 'miter', ('rect1_top',)),
    ('zigzag', 'round', ('rect1_top',)),
    ('wave', 'miter', ('rect1_top',)),
    ('wave', 'round', ('rect1_top',)),
    ('zigzag', 'round', ('rect1_top', 'circle_far')),
    ('wave', 'miter', ('rect1_top', 'circle_far')),
    ('zigzag', 'miter', ('near_r2',)),
    ('wave', 'round', ('near_r2',)),
    ('zigzag', 'round', ('circle_far',)),
    ('wave', 'miter', ('circle_far',)),
]


@pytest.mark.parametrize('pattern,style,openings', CASES)
def test_one_closed_route_with_the_shared_lattice(pattern, style, openings):
    """Every connected piece is ONE closed route (no open ends, one print
    run, no retrace) — Base and the derived design, both printing the ONE
    shared scaffold (not planned alone)."""
    lib = lib_of(pattern, style, openings or ('rect1_top',))
    for d in (('base',) if not openings else ('base', 'gaps')):
        c, mt, _ = route(lib, d)
        assert not c['open'], (d, c['open'])
        assert mt['print_runs'] == 1 and mt['retrace_distance'] == 0, (d, mt)
        assert lib.lattice_reference(d)                       # the shared lattice, not a fallback
        assert not lib.lattice_lineage(d)['IN'].get('planned_alone')


def test_round_junction_arcs_are_not_jamb_lines():
    """Only END walls (caps, doorway faces) are jamb lines of the scaffold —
    rounded-junction arcs are 'cap' beads too but junction geometry (they
    used to add 1 in pseudo-jambs at every rebuilt junction)."""
    sc = lib_of('wave', 'round', ('rect1_top',)).scaffold('base')['IN']['stations']
    assert len(sc) == 2 and all(abs(math.dist(a, b) - 10.0) < 1e-6 for a, b in sc)


def test_an_opening_into_another_wall_closes_alone_and_is_reported():
    """The doorway cuts Rect 1's wall where Rect 2's wall crosses it: the
    member's junction is rebuilt, so the shared scaffold cannot close there.
    A closed route outranks registration: the member is planned alone —
    one closed route — and the lineage diagnostics say so."""
    lib = lib_of('wave', 'round', ('into_r2',))
    c, mt, _ = route(lib, 'gaps')
    assert not c['open'] and mt['print_runs'] == 1
    lin = lib.lattice_lineage('gaps')['IN']
    assert lin['planned_alone'] and 'planned on its own' in lin['jambs']['unresolved'][-1]['why']
    c, mt, _ = route(lib, 'base')
    assert not c['open'] and lib.lattice_reference('base')    # Base keeps the shared scaffold


# -- Wall Thickness 0 = single bead; no lattice without a cavity ------------------

def _line_band_lattice(b, half):
    (a, e) = RN.LINE
    L = math.dist(a, e)
    lat = [q for s in b.printable if s['id'].startswith('IN') for q in s['pts']]
    inside = []
    for q in lat:
        t = ((q[0] - a[0]) * (e[0] - a[0]) + (q[1] - a[1]) * (e[1] - a[1])) / (L * L)
        if 15 / L < t < 1 - 15 / L and _segd(q, a, e) < half:
            inside.append(q)
    return inside


@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_wall_thickness_zero_is_a_single_bead_path_without_lattice(pattern):
    """Line 1 inherits the network's 10 in wall; overriding it to 0 makes it
    a SINGLE-BEAD path: physical rules print its return lanes (W − R apart),
    the geometry is not deleted, no fake epsilon wall, and the network's
    lattice does not run into it. One closed route."""
    lib = DesignLibrary([{'id': 'b', 'name': 'B', 'document': RN.document(pattern, line_thickness=0)}])
    b = lib.build('b')
    assert b.network['return_lanes'] == ['L1']
    lanes = [s for s in b.printable if s['id'].startswith('L1.wall')]
    assert len(lanes) == 2                                     # printed — not deleted
    mid = [(RN.LINE[0][0] + RN.LINE[1][0]) / 2, (RN.LINE[0][1] + RN.LINE[1][1]) / 2]
    d = [min(_segd(mid, u, v) for u, v in zip(s['pts'], s['pts'][1:])) for s in lanes]
    assert all(abs(x - (3.0 - 0.75) / 2) < 1e-6 for x in d)    # W − R apart, centred
    assert not _line_band_lattice(b, 3.0)                       # no lattice in the single bead
    c, mt, _ = route(lib, 'b')
    assert not c['open'] and mt['print_runs'] == 1


def test_inherited_and_thin_walls():
    """Inherited: a 10 in wall with lattice inside. Overridden to 3 in (one
    bead width): a thick wall whose faces are 3 in apart — no cavity, so no
    lattice ('no_cavity' in the report) — still one closed route."""
    lib = DesignLibrary([{'id': 'b', 'name': 'B', 'document': RN.document('zigzag')}])
    b = lib.build('b')
    assert _line_band_lattice(b, 5.0)
    lib3 = DesignLibrary([{'id': 'b', 'name': 'B', 'document': RN.document('zigzag', line_thickness=3)}])
    b3 = lib3.build('b')
    assert not b3.network['return_lanes'] and not _line_band_lattice(b3, 1.5)
    assert any(r.get('no_cavity') for r in b3.network['lattice']['IN']['regions'])
    c, mt, _ = route(lib3, 'b')
    assert not c['open'] and mt['print_runs'] == 1


def test_single_bead_override_is_inherited_by_the_derived_design():
    lib = lib_of('wave', 'round', ('rect1_top',), line_thickness=0)
    assert lib.document('gaps')['source_paths'][-1]['wall'] == {'thickness': 0, 'align': 'auto'}
    assert lib.build('gaps').network['return_lanes'] == ['L1']
    c, mt, _ = route(lib, 'gaps')
    assert not c['open'] and mt['print_runs'] == 1


def test_effective_walls_follow_network_membership():
    """Semantic transforms / attribution see the Designer's wall rule: a
    member inherits the Network Wall (not only its anchor path); an explicit
    0 is a single bead — a return-lane pair under physical rules."""
    w = ST.effective_walls(RN.document())
    assert {k: v[0] for k, v in w.items()} == {'C1': 10.0, 'R1': 10.0, 'R2': 10.0, 'L1': 10.0}
    w0 = ST.effective_walls(RN.document(line_thickness=0))
    assert w0['L1'] == (0.0, 'auto', 'lanes') and w0['C1'][2] == 'wall'


# -- derived junctions vs explicit connectors; the separation sweep ---------------

def test_derived_junctions_and_explicit_connectors_are_distinguished():
    rel = ST.relations(RN.document())
    assert rel['derived'] == [['C1', 'R1'], ['R1', 'R2']]          # forms that merely intersect
    (con,) = rel['connectors']                                      # the authored wall
    assert con['source'] == 'L1' and con['path'] == 'straight'
    assert [a['host'] for a in con['attachments']] == ['R2', 'C1']


def _doc(with_line):
    d = RN.document('wave')
    if not with_line:
        d['source_paths'] = [s for s in d['source_paths'] if s['id'] != 'L1']
    return d


def _faces_of(b, sid):
    return [s for s in b.printable if b.strand_sources.get(s['id']) == sid]


SWEEP = [(0.0, True), (4.0, True), (8.0, True), (9.9, True), (12.0, False), (30.0, False)]


@pytest.mark.parametrize('with_line', [False, True])
def test_junction_separation_sweep(with_line):
    """Circle 1 (one group) moves away from Rect 1 (another): strongly
    intersecting → barely → tangent → separated. At every step the circle
    keeps its form and wall thickness, the junction is RECOMPUTED from the
    transformed forms (derived: present only while they intersect — no
    stretched remnant, no invented bridge), ownership is deterministic and
    every connected piece closes. Without Line 1 the separated forms are two
    pieces; with it (an explicit connector) Line 1 is regenerated between
    them and the network stays one piece."""
    lib = DesignLibrary([{'id': 'b', 'name': 'B', 'document': _doc(with_line)}])
    for dx, present in SWEEP:
        tf = {'C1': (1.0, dx, 0.0)}
        b = lib.build('b', transforms=tf)
        js = b.semantic.get('junctions')                 # (identity: the plain build, all present)
        if js is None:
            js = [{'sources': p, 'present': True} for p in ST.relations(b.document)['derived']]
        jn = {tuple(j['sources']): j['present'] for j in js}
        assert jn[('C1', 'R1')] is present, dx
        cx = RN.CX + dx
        # the circle keeps its form: its faces at r = 90 and 80 (10 in wall), away from the junction
        far = [q for s in _faces_of(b, 'C1') for q in s['pts'] if q[0] > cx - 40]
        assert far and all(min(abs(math.dist(q, (cx, RN.CY)) - 90), abs(math.dist(q, (cx, RN.CY)) - 80)) < 0.05
                           for q in far), dx
        if not present:                                   # nothing left in the gap between the forms
            gap = [q for s in b.printable for q in s['pts']
                   if 300 + 0.5 < q[0] < cx - 90 - 0.5 and 120 < q[1] < 220]
            assert not gap, (dx, gap[:3])
        c, mt, _ = route(lib, 'b', tf)
        assert not c['open'], dx
        pieces = 1 if (present or with_line) else 2
        assert mt['print_runs'] == pieces, (dx, mt['print_runs'])
        if with_line and dx:
            assert [x['source'] for x in b.semantic['connectors']] == ['L1']   # regenerated, kept
        # deterministic, source-derived ownership: the same tags twice
        t1 = SA.attribute(b.document, b.printable, b.strand_sources)
        t2 = SA.attribute(b.document, b.printable, b.strand_sources)
        assert t1 == t2


def test_a_design_planned_alone_is_one_connected_route():
    """With rounded junctions the hand-off points sit inside the wall, so
    per-ring transfers alone left the lattice as TWO closed systems (two
    print runs + a travel) although the material is one piece. A transfer
    landing now joins every lattice system (parity-neutral)."""
    d = RN.document('wave')
    d['junction_style'], d['junction_radius'] = 'round', 6
    d['openings'] = [{'id': 'o', 'source_path_id': 'R1', 'center_s': 114.7, 'width': 24}]
    lib = DesignLibrary([{'id': 'b', 'name': 'B', 'document': d}])
    c, mt, _ = route(lib, 'b')
    assert not c['open'] and c['components'] == 1 and mt['print_runs'] == 1


@pytest.mark.parametrize('sid,s,w', [('R1', 60.0, 30.0), ('R1', 395.0, 24.0), ('R2', 330.0, 30.0),
                                     ('R2', 470.0, 24.0), ('C1', 30.0, 36.0), ('C1', 480.0, 30.0)])
def test_openings_clear_of_other_walls_close_with_the_shared_lattice(sid, s, w):
    """A deterministic slice of the randomised search that found the round-
    junction failures (a stitch leaving a concave fillet; a corner station
    beside a jamb; a jamb just short of a junction end): clean openings on
    every wall close with the SHARED scaffold, mitred and rounded."""
    for style in ('miter', 'round'):
        base = RN.document('wave')
        base['junction_style'], base['junction_radius'] = style, 6
        lib = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base},
                             {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': {
                                 'o': {'source_path_id': sid, 'center_s': s, 'width': w}}}}])
        for d in ('base', 'gaps'):
            c, mt, _ = route(lib, d)
            assert not c['open'] and mt['print_runs'] == 1, (style, d)
            assert lib.lattice_reference(d), (style, d)


def test_cross_z_groups_close_and_stay_tracked():
    """Circle 1, Rect 1 and Rect 2 scale independently (three groups) through
    Z. Every layer closes; where the skeleton is unchanged the plan is fully
    tracked, and where a junction lens shrinks away only the changed runs are
    planned afresh (PARTIAL tracking) — pass counts repaired for parity
    through the changed runs, sides re-searched only if the carried ones
    cannot pair. Tracked counts honour the parity asked for (a pass never
    ends on the wrong face)."""
    lib = lib_of('wave', 'round', ('rect1_top',))
    for j in range(0, 12, 3):
        k1, k2, k3 = 1 - 0.012 * j, 1 - 0.006 * j, 1 - 0.008 * j
        tf = {'C1': (k1, RN.CX * (1 - k1), RN.CY * (1 - k1)), 'R1': (k2, 200 * (1 - k2), 170 * (1 - k2)),
              'R2': (k3, 90 * (1 - k3), 260 * (1 - k3))}
        for d in ('base', 'gaps'):
            c, mt, _ = route(lib, d, tf)
            assert not c['open'] and mt['print_runs'] == 1, (j, d)
            if j and lib.lattice_reference(d, tf):
                tr = lib.scaffold(d, tf)['IN']['report'][0]['track']
                assert tr['tracked_runs'] >= 1, (j, d)

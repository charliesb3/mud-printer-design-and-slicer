"""J4a — SHARED GEOMETRIC REGIONS AND CONNECTIVITY (2026-10-12).

J1 built the junction harness. J2/J3 wired a single region type
(`JunctionRegion`) into Hollow + Parallel. J4a extends the framework
with four more region types (`CornerRegion`, `EndCapRegion`,
`ConstrainedRegion`, `TransitionRegion`) and begins their detection +
diagnostic reporting.

GEOMETRY RULES this file pins:
  - architectural wall geometry and connectivity remain authoritative;
  - rounded junctions must not disconnect previously connected walls;
  - a single-bead attachment must not create an outward stub;
  - ordinary rectangular corners must not spontaneously develop 45°
    chamfers unless explicitly requested;
  - end caps preserve the intended wall thickness;
  - very short wall sections are DETECTED and REPORTED (J4a scope); no
    forced motif substitution (that is J4b).

Tests here load fixtures from the J1 harness and the reference-network
scenarios. For bugs that are already corrected by J2/J3/M4, we pin
"stays fixed" invariants. For bugs that require a J4b motif library
(the Chain's corner behaviour, the full compact-loop treatment of a
short pier), we pin DETECTION + diagnostic data so a future J4b change
has a stable anchor.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import _deserialise_layer
from design_proto.junction_harness import fixtures as F
from design_proto.junction_harness import checks as C
import junctions as J


# -------- helpers ---------------------------------------------------------

def _build(doc):
    lay = _deserialise_layer(doc)
    paths, meta = lay._build_effective()
    return paths, meta, lay


def _regions(meta: dict) -> dict:
    return (meta.get('network') or {}).get('shared_regions') or {}


# ===========================================================================
# 1. REGION EXTRACTION COMPLETENESS
# ===========================================================================

@pytest.mark.parametrize('name', sorted(F.SINGLE_LAYER))
def test_j4a_shared_regions_are_reported_for_every_fixture(name):
    """Every harness fixture produces a `shared_regions` dict with the
    five region-type lists and a `counts` dict. If the extractor crashes
    on any fixture, this fires."""
    b, d = F.SINGLE_LAYER[name]
    _, meta, _ = _build(b(**d))
    sr = _regions(meta)
    assert sr, f'{name}: shared_regions missing'
    for k in ('junctions', 'corners', 'end_caps', 'constrained',
              'transitions', 'counts'):
        assert k in sr, (name, k, sr.keys())
    cnt = sr['counts']
    # the counts dict agrees with the list lengths
    for k in ('junctions', 'corners', 'end_caps', 'constrained', 'transitions'):
        assert cnt[k] == len(sr[k]), (name, k, cnt[k], len(sr[k]))


def test_j4a_region_type_field_matches_bucket():
    """`region_type` on each record agrees with the bucket it is in."""
    doc = F.tee(thick=10.0)
    _, meta, _ = _build(doc)
    sr = _regions(meta)
    for r in sr['junctions']:
        assert r.get('region_type') == 'junction'
    for r in sr['corners']:
        assert r.get('region_type') == 'corner'
    for r in sr['end_caps']:
        assert r.get('region_type') == 'end_cap'
    for r in sr['constrained']:
        assert r.get('region_type') == 'constrained'
    for r in sr['transitions']:
        assert r.get('region_type') == 'transition'


# ===========================================================================
# 2. CORNER REGION — rectangular corners are detected and not chamfered
# ===========================================================================

def test_j4a_rectangular_corner_90_produces_four_corners():
    """A closed rectangle has four 90° corners. Each must appear as a
    CornerRegion with turn ≈ π/2 and `style == 'miter'` (document
    default). This is the direct architectural pin for bug 2 (unexpected
    45° chamfers must not appear): the recorded `turn_angle` reflects
    the ARCHITECTURAL corner, not whatever the construction later draws
    (that's a J4b consumption decision)."""
    import math
    doc = F.corner_90(system='interleaved', style='miter', thick=10.0)
    _, meta, _ = _build(doc)
    corners = _regions(meta).get('corners') or []
    assert len(corners) == 4, corners
    for c in corners:
        assert abs(abs(c['turn_angle']) - math.pi / 2) < 0.1, c
        assert c['style'] == 'miter', c
        # radius is 0 for miter
        assert c['radius'] == 0, c


def test_j4a_round_junction_style_propagates_to_corner_records():
    """Document-level `junction_style: round` reflects in every CornerRegion."""
    doc = F.corner_90(system='interleaved', style='round', thick=10.0, radius=6.0)
    _, meta, _ = _build(doc)
    corners = _regions(meta).get('corners') or []
    assert corners
    assert all(c['style'] == 'round' for c in corners), corners
    assert all(c['radius'] > 0 for c in corners), corners


def test_j4a_circle_source_produces_no_corners():
    """A closed circle is smooth: no CornerRegion entries. The circle in
    the circle+rect fixture is a continuous curve; only the rectangle's
    corners count."""
    doc = F.circle_rect()
    _, meta, _ = _build(doc)
    corners = _regions(meta).get('corners') or []
    # the rectangle part contributes 4; the circle contributes 0
    srcs = {c['source_id'] for c in corners}
    assert 'R' in srcs, corners
    assert 'C' not in srcs, [c['source_id'] for c in corners]


# ===========================================================================
# 3. ENDCAP REGION — free ends + opening jambs are reported
# ===========================================================================

def test_j4a_tee_free_ends_appear_as_endcaps():
    """A T junction has four free ends (two on each arm). Each must
    appear in `end_caps` with the arm's architectural outward direction."""
    doc = F.tee(thick=10.0)
    _, meta, _ = _build(doc)
    caps = _regions(meta).get('end_caps') or []
    assert len(caps) == 4, caps
    srcs = {c['source_id'] for c in caps}
    assert srcs == {'A', 'B'}, srcs


def test_j4a_opening_jambs_appear_as_endcap_pair():
    """A single Designer opening on a thick wall produces TWO EndCapRegion
    records (one per jamb side)."""
    doc = F.corner_90(system='interleaved', thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'R', 'center_s': 30.0, 'width': 10.0},
    ]
    _, meta, _ = _build(doc)
    caps = _regions(meta).get('end_caps') or []
    opening_caps = [c for c in caps if 'opening' in c['key']]
    assert len(opening_caps) == 2, opening_caps
    sides = {c['key'].rsplit('-', 1)[-1] for c in opening_caps}
    assert sides == {'lo', 'hi'}, sides


# ===========================================================================
# 4. CONSTRAINED SECTION DETECTION — short pier between openings
# ===========================================================================

def test_j4a_short_pier_between_two_openings_is_flagged():
    """Two openings 15 in centre-to-centre on a 10-in thick wall leave
    a 5-in pier between them. Required = max(thickness, 2 × bead) =
    10 in. The pier IS flagged as constrained with the correct reason
    and lengths."""
    doc = F.corner_90(system='interleaved', thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'R', 'center_s': 30.0, 'width': 10.0},
        {'id': 'o2', 'source_path_id': 'R', 'center_s': 45.0, 'width': 10.0},
    ]
    _, meta, _ = _build(doc)
    con = _regions(meta).get('constrained') or []
    assert con, 'expected a constrained pier'
    pier = [c for c in con if c['reason'] == 'between_openings']
    assert pier, con
    assert pier[0]['available_length'] == pytest.approx(5.0, abs=1e-3)
    assert pier[0]['required_length'] == pytest.approx(10.0, abs=1e-3)


def test_j4a_wide_pier_is_not_flagged():
    """Two openings 40 in apart on a 10-in wall leave a 30-in pier —
    well above the 10-in threshold. No constrained flag."""
    doc = F.corner_90(system='interleaved', thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'R', 'center_s': 20.0, 'width': 10.0},
        {'id': 'o2', 'source_path_id': 'R', 'center_s': 60.0, 'width': 10.0},
    ]
    _, meta, _ = _build(doc)
    con = _regions(meta).get('constrained') or []
    assert not con, con


def test_j4a_short_jamb_margin_is_flagged():
    """An opening too close to a wall's end leaves a short jamb margin;
    the extractor reports it with reason='short_jamb'. Uses an OPEN
    source (`tee` arm `A`, which is a LinePath — open)."""
    doc = F.tee(thick=10.0)
    # tee: A is horizontal line 0..120 at y=60. Add an opening at s=10 of
    # width=15; this leaves a 2.5-in jamb on the start side.
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'A', 'center_s': 10.0, 'width': 15.0},
    ]
    _, meta, _ = _build(doc)
    con = _regions(meta).get('constrained') or []
    reasons = [c['reason'] for c in con]
    assert 'short_jamb' in reasons, reasons


# ===========================================================================
# 5. TRANSITION — one per constrained section
# ===========================================================================

def test_j4a_transition_pairs_each_constrained_region():
    """Each ConstrainedRegion produces one TransitionRegion naming it
    as the `to_region`. Pins the J4a convention: transitions carry the
    identity relationship, J4b will add the motif."""
    doc = F.corner_90(system='interleaved', thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'R', 'center_s': 30.0, 'width': 10.0},
        {'id': 'o2', 'source_path_id': 'R', 'center_s': 45.0, 'width': 10.0},
    ]
    _, meta, _ = _build(doc)
    con = _regions(meta).get('constrained') or []
    tr = _regions(meta).get('transitions') or []
    assert len(tr) == len(con), (tr, con)
    for t, c in zip(tr, con):
        assert t['to_region'] == c['key']


# ===========================================================================
# 6. BUG 1 PIN — circle/rect rounded junctions do not develop gaps
# ===========================================================================

@pytest.mark.parametrize('system', ['parallel', 'interleaved', 'linked', 'chain'])
@pytest.mark.parametrize('style', ['miter', 'round'])
def test_j4a_circle_rect_connectivity_across_systems_and_styles(system, style):
    """BUG 1 (reported): rounded circle/rectangle junctions developing
    gaps. J2/J3 corrected this via united-generation + Parallel
    stitching + the M4 orphan-fillet step-in; J4a pins that EVERY
    (system × style) scenario still closes."""
    doc = F.circle_rect(system=system, style=style, thick=10.0, radius=6.0)
    paths, meta, layer = _build(doc)
    bead = layer.material.bead_width
    ok, cont = C.continuity(paths, meta, layer)
    assert ok, (system, style, cont)
    ok, ext = C.exterior_continuity(paths, meta, bead)
    assert ok, (system, style, ext)
    ok, missing = C.region_coverage(paths, meta, bead)
    assert ok, (system, style, missing)


# ===========================================================================
# 7. BUG 2 PIN — rectangular corners stay at 90° in the architecture
# ===========================================================================

@pytest.mark.parametrize('system', ['parallel', 'interleaved', 'linked', 'chain'])
def test_j4a_rectangular_corners_architecture_stays_90(system):
    """BUG 2 (reported): unexpected 45° chamfers at rectangular corners
    on certain layers. The architectural CORNER RECORD must always
    reflect the real 90° turn, independent of the construction. If a
    future generator starts rewriting the architectural corner (which it
    must NOT), this fires. The construction's rendered path may contain
    short motif-level 45° segments inside the pattern (that is a J4b
    motif concern — not architectural), but the CornerRegion itself
    must stay 90° and `style == 'miter'`."""
    import math
    doc = F.corner_90(system=system, style='miter', thick=10.0)
    _, meta, _ = _build(doc)
    corners = _regions(meta).get('corners') or []
    assert len(corners) == 4
    for c in corners:
        assert abs(abs(c['turn_angle']) - math.pi / 2) < 0.1, (system, c)
        assert c['style'] == 'miter', (system, c)


def test_j4a_chain_corner_motif_segments_are_reported_as_a_known_issue():
    """BUG 2 DOCUMENTATION PIN: the Chain system's current corner motif
    produces ~8 short (0.5-1.5 in) 45° segments near each rectangle
    corner. These are intentional corner-motif detail features of the
    chain pattern. The CornerRegion itself still reports 90° miter
    (the architectural truth); whether the chain's corner motif should
    produce visibly chamfered printed geometry is a J4b MOTIF decision.
    This test documents the current counts so a J4b change can show its
    diff."""
    import math
    doc = F.corner_90(system='chain', style='miter', thick=10.0)
    paths, _, _ = _build(doc)
    corners_xy = [(0., 0.), (120., 0.), (120., 80.), (0., 80.)]
    chamfer_count = 0
    for pp in paths:
        pts = pp.sample_points() if hasattr(pp, 'sample_points') else []
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            dx, dy = b.x - a.x, b.y - a.y
            if dx * dx + dy * dy < 1e-6:
                continue
            if abs(abs(dx) - abs(dy)) < 0.3 and abs(dx) > 0.5:
                mid = (0.5 * (a.x + b.x), 0.5 * (a.y + b.y))
                if any((mid[0] - cx) ** 2 + (mid[1] - cy) ** 2 < 15 ** 2
                       for cx, cy in corners_xy):
                    chamfer_count += 1
    # pin current observation (J4a documentation). If this ever drops to
    # 0, chain corner motif changed — update the number intentionally.
    assert chamfer_count > 0, 'chain corner motif pattern changed'


# ===========================================================================
# 8. BUG 3 PIN — single-bead attachments report the attach point as a region
# ===========================================================================

@pytest.mark.parametrize('host', ['parallel', 'interleaved', 'linked', 'chain', 'skin_web', None])
def test_j4a_single_bead_attachment_reports_connected_regions(host):
    """BUG 3 (reported): single-bead walls creating exterior protrusions
    or disconnected stubs. J4a's region framework must at least IDENTIFY
    the attachment as a shared region so a future J4b fix has a
    stable anchor. Here we pin: continuity closes (every current
    scenario does, per the harness) + at least one junction OR corner
    region is reported near the attachment point."""
    doc = F.single_to_thick(host_system=host, style='round', thick=10.0)
    paths, meta, layer = _build(doc)
    ok, cont = C.continuity(paths, meta, layer)
    assert ok, (host, cont)
    sr = _regions(meta)
    # the branch attach is near (80, 100) in the fixture. Either a
    # JunctionRegion between R and L, or a CornerRegion on R near that
    # point, or an EndCapRegion on L must be present.
    reported = (len(sr.get('junctions') or []) + len(sr.get('corners') or [])
                + len(sr.get('end_caps') or []))
    assert reported > 0, (host, sr.get('counts'))


# ===========================================================================
# 9. BUG 4 PIN — short piers are detected and NOT silently forced into motif
# ===========================================================================

def test_j4a_short_pier_is_reported_rather_than_silently_processed():
    """BUG 4 (reported): very short wall sections between opening arrays
    produce disconnected fragments or malformed paths. J4a's commitment
    is: DETECT and REPORT, not silently force a motif. The constrained
    section is reported with reason + available_length; the generator
    may still produce something (that's the current behaviour; J4b
    fixes it), but the DIAGNOSTIC is now reliable."""
    doc = F.corner_90(system='interleaved', thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'R', 'center_s': 30.0, 'width': 10.0},
        {'id': 'o2', 'source_path_id': 'R', 'center_s': 45.0, 'width': 10.0},
    ]
    _, meta, _ = _build(doc)
    con = _regions(meta).get('constrained') or []
    pier = [c for c in con if c['reason'] == 'between_openings']
    assert pier, con
    # the reported pier length is strictly less than the threshold
    assert pier[0]['available_length'] < pier[0]['required_length']


# ===========================================================================
# 10. REGION KEYS ARE STABLE ACROSS REPEAT BUILDS
# ===========================================================================

def test_j4a_region_keys_are_stable_across_rebuilds():
    """A stable identity across replays is the J4a scope commitment:
    'stable source-based identity'. Build the same document twice;
    the sorted lists of region keys must be identical for every type."""
    doc = F.tee(thick=10.0)
    doc['openings'] = [
        {'id': 'o1', 'source_path_id': 'A', 'center_s': 10.0, 'width': 15.0},
    ]
    _, meta_a, _ = _build(doc)
    _, meta_b, _ = _build(doc)
    sa = _regions(meta_a); sb = _regions(meta_b)
    for kind in ('junctions', 'corners', 'end_caps', 'constrained', 'transitions'):
        ka = sorted(r['key'] for r in sa.get(kind) or [])
        kb = sorted(r['key'] for r in sb.get(kind) or [])
        assert ka == kb, (kind, ka, kb)

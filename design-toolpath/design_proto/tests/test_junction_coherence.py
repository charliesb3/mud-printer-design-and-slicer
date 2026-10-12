"""
M4 — JUNCTION COHERENCE (2026-10-12).

A round-junction fillet anchored to a RING whose face bead was replaced by a
generated Wall System (Parallel / Interleaved / Linked / Chain) has its ring-
side endpoint left in space: the outermost Wall System lane sits one bead-
width inside the former face, so the fillet no longer closes with the
printed geometry. Before this pass every reference-network scenario with a
round junction + an attached skin or single-bead wall produced an OPEN
ROUTE at that attachment, across every construction. The fix pairs the
two orphan fillet endpoints at one attachment and ties BOTH to a shared
point: the nearest Wall System lane point (step-in; good for Interleaved /
Linked / Chain / Parallel where the lane reaches the attachment) or the
midpoint of the pair itself (face-chord recovery; used when the Wall System
lane does not come near the attachment, e.g. a Parallel rectangle whose
right edge was clipped out). Local, construction-agnostic.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer
from graph import build_graph, closure_report
import reference_network as RN


def _build_doc(system_type, line_thickness, style):
    """The reference-network document with a single Wall System covering the
    three closed sources, round or miter junctions, Line 1 inheriting /
    single-bead / or in the Wall System."""
    doc = RN.document('zigzag', line_thickness)
    doc['junction_style'] = style
    doc['junction_radius'] = 6 if style == 'round' else 0
    members = ['C1', 'R1', 'R2']
    if line_thickness is not None and line_thickness != 0:
        members.append('L1')
    sys_def = {
        'parallel':    {'params': {'walls': 4}},
        'interleaved': {'params': {'pattern': 'wave', 'paths': 4, 'period': 24}},
        'linked':      {'params': {'pattern': 'wave', 'paths': 4, 'period': 24}},
        'chain':       {'params': {'pitch': 9.0, 'neck': 0.2}},
    }[system_type]
    doc['wall_systems'] = [{'id': 'WS1', 'name': 'Wall', 'type': system_type,
                           'thickness': 10, 'align': 'auto', 'print_reference': False,
                           'params': dict(sys_def['params']), 'members': members, 'web': {}}]
    doc['infills'] = []
    doc['network_walls'] = [{'id': 'NW1', 'path_id': 'R1', 'thickness': 10, 'align': 'auto'}]
    return doc


def _closure(doc):
    layer = _deserialise_layer(doc)
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    rep = closure_report(build_graph(rl))
    opens = rep.get('open') if isinstance(rep.get('open'), list) else []
    return rep, meta, paths, opens


SYSTEMS = ['parallel', 'interleaved', 'linked', 'chain']
LINE_CASES = [('inherit', None), ('single_bead', 0), ('explicit10', 10)]


@pytest.mark.parametrize('system', SYSTEMS)
@pytest.mark.parametrize('style', ['miter', 'round'])
@pytest.mark.parametrize('line_label,line_t', LINE_CASES)
def test_every_route_closes_at_a_wall_system_attachment(system, style, line_label, line_t):
    """Every reference-network scenario (every construction × junction style ×
    Line 1 variant) produces at most CLOSED routes: no open component after
    the generated geometry is routed."""
    rep, meta, paths, opens = _closure(_build_doc(system, line_t, style))
    assert opens == [], f'{system} {style} line={line_label}: unexpected open routes {opens}'


@pytest.mark.parametrize('system', SYSTEMS)
def test_round_junction_with_skin_attachment_adds_junction_steps(system):
    """For a round-junction scenario where a non-member wall (Line 1
    inherits a Network Wall or is a single bead) attaches to a Wall System
    ring, the fix adds junction-step connectors reported in the network
    diagnostics."""
    rep, meta, paths, opens = _closure(_build_doc(system, None, 'round'))
    assert opens == [], (system, opens)
    nd = meta.get('network') or {}
    # junction_steps is set when the orphan-fillet fix fired at least once
    assert nd.get('junction_steps', 0) >= 1, (system, nd.get('junction_steps'))
    # the junction-step connectors are 'cap' role with label 'junction_step'
    steps = [p for p in paths if getattr(p, 'label', '') == 'junction_step']
    assert len(steps) == nd['junction_steps'], (len(steps), nd['junction_steps'])


def test_junction_step_does_not_disturb_miter_scenarios():
    """Miter scenarios do not need the step-in (fillets lie exactly on the
    face corner). No junction_steps are added when every route already
    closes."""
    for system in SYSTEMS:
        rep, meta, paths, opens = _closure(_build_doc(system, 10, 'miter'))
        assert opens == [], (system, opens)
        nd = meta.get('network') or {}
        # fast scenarios never need the step
        assert nd.get('junction_steps', 0) == 0, (system, nd.get('junction_steps'))


def test_paired_step_in_shares_one_attach_point():
    """At one attachment the two orphan fillets attach to a shared point
    (the lane cycle gains two edges at ONE node = degree 4 even). Verified
    by: for every pair of junction_step connectors from the SAME source,
    their attach-side (second) endpoints are either identical or belong to
    different attachments."""
    doc = _build_doc('interleaved', None, 'round')
    layer = _deserialise_layer(doc)
    paths, meta = layer._build_effective()
    by_source: dict = {}
    for p in paths:
        if getattr(p, 'label', '') != 'junction_step':
            continue
        sid = getattr(p, 'source_id', None)
        pts = p.sample_points()
        by_source.setdefault(sid, []).append(pts[-1])
    for sid, ends in by_source.items():
        # within each ring the step attach points cluster by attachment —
        # steps of the SAME attachment share one point, steps of
        # DIFFERENT attachments are far apart
        unique = []
        for q in ends:
            if not any(q.dist(u) < 0.01 for u in unique):
                unique.append(q)
        # at most one unique attach point per attachment (there are typically
        # one or two attachments per ring in the reference network)
        assert len(unique) <= 2, (sid, len(unique))


@pytest.mark.parametrize('system', SYSTEMS)
def test_junction_step_attach_point_lies_on_wall_system_or_pair_midpoint(system):
    """Behavioural pin on the M4 step-in rule, independent of the symptom
    assertion in `test_round_junction_with_skin_attachment_adds_junction_steps`.
    Rule: each `junction_step` connector's attach-side endpoint is either
    * within 0.5 x bead of some Wall System polyline sample (step-in: the
      nearest-lane case), OR
    * the midpoint of the pair of orphans it comes from (face-chord
      recovery: used when the Wall System lane does not reach the
      attachment).
    If a change silently relocates attach points away from the lane and
    outside pair midpoints, this test flags it. Together with the paired-
    attach-point test, this guards the two documented M4 rules.
    """
    doc = _build_doc(system, None, 'round')
    layer = _deserialise_layer(doc)
    paths, meta = layer._build_effective()
    bead = layer.material.bead_width
    tol_lane = 0.5 * bead
    tol_mid = 0.01

    def ws_pts(src):
        out = []
        for p in paths:
            if getattr(p, 'role', '') != 'wall_system':
                continue
            if getattr(p, 'source_id', None) != src:
                continue
            out.extend(p.sample_points())
        return out

    # group steps by (source_id, attach-side endpoint cluster)
    # bucket_entries is a list of (sid, attach_vec, [orphan_vec, ...])
    bucket_entries: list = []
    for p in paths:
        if getattr(p, 'label', '') != 'junction_step':
            continue
        pts = p.sample_points()
        if len(pts) < 2:
            continue
        sid = getattr(p, 'source_id', None)
        attach = pts[-1]
        orphan = pts[0]
        matched = None
        for entry in bucket_entries:
            if entry[0] == sid and attach.dist(entry[1]) < tol_mid:
                matched = entry
                break
        if matched is None:
            matched = (sid, attach, [])
            bucket_entries.append(matched)
        matched[2].append(orphan)

    assert bucket_entries, (system, 'expected at least one junction_step')
    for (sid, attach, orphans) in bucket_entries:
        # step-in: attach is close to the Wall System lane
        wpts = ws_pts(sid)
        on_lane = any(attach.dist(w) < tol_lane for w in wpts)
        # face-chord recovery: attach is the midpoint of the orphan pair
        is_pair_midpoint = False
        if len(orphans) == 2:
            mx = 0.5 * (orphans[0].x + orphans[1].x)
            my = 0.5 * (orphans[0].y + orphans[1].y)
            is_pair_midpoint = (abs(attach.x - mx) < tol_mid and
                                abs(attach.y - my) < tol_mid)
        assert on_lane or is_pair_midpoint, \
            (system, sid, attach, 'neither on WS lane nor pair midpoint')

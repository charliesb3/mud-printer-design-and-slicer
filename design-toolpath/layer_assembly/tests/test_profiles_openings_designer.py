"""
SELF-SUPPORTING OPENINGS, extended (2026-10-09) — through the REAL Designer:
arrays / arch shapes cut the whole wall assembly into capped, closed pieces
on every Wall System; the support checker (unchanged) finds no HEADER NEEDED
at a valid profile; and an ordinary Designer opening that needs a header can
be CONVERTED: its rectangle stays the Designer's, a self-supporting closure
is added above it, the header finding disappears because the geometry is
supported — removing the conversion restores it; unconverted openings keep
their header behaviour.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.dirname(__file__))

from test_profiles_designer import (line_doc, designs, targets_of, open_ends, _printed_gap, _changed_tol,
                                    BEAD, T, CLOSING)
from layer_assembly.designer_source import DesignerSource
from layer_assembly.demo import demo_designs
from layer_assembly.model import Assembly, Section, SectionTransform, resolve
from layer_assembly.profiles import OpeningProfile, OpeningArray, WallRef, PlaneProfile, apply_profiles
from layer_assembly.source import stack_geometry
from layer_assembly.support import analyse_support


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def stack(src, a, ids=('A',)):
    res = resolve(a, list(ids))
    res, rep = apply_profiles(res, a.profiles, targets_of(src, ids), BEAD, a.max_overhang,
                              {d: src.sources(d) for d in ids}, {d: src.openings(d) for d in ids})
    return res, rep, stack_geometry(res, src)


def _bridges(res, geo, a):
    return [f for f in analyse_support(res, geo, a).findings if f.kind == 'bridge']


# ---- arrays + shapes on real walls ------------------------------------------------------

@pytest.mark.parametrize('system,params', CLOSING, ids=[c[0] for c in CLOSING])
def test_several_openings_in_one_layer_are_capped_closed_pieces(system, params):
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=240.0)))
    cuts = [{'op': 'opening', 'source': 'L', 'edge': None, 'at': x, 'width': 18.0, 'profile': 'P'} for x in (50, 110, 170)]
    g = src.geometry('A', None, cuts)
    for x in (50, 110, 170):                                           # each clear, through the whole wall
        l_, r_ = _printed_gap(g.polylines, float(x))                   # (bead edges; caps print on the cut face)
        assert r_ - l_ >= 18 - BEAD - 1e-6
    assert open_ends(src, 'A', cuts) == []
    ids = [r['center_s'] for r in g.diagnostics['profile']]
    assert ids == [50.0, 110.0, 170.0]


ARRAYS = [('horizontal', dict(count=4, h_spacing=36)), ('grid', dict(cols=3, rows=2, h_spacing=36, v_spacing=6)),
          ('staggered', dict(cols=3, rows=2, h_spacing=40, v_spacing=6)),
          ('diagonal_up', dict(count=3, h_spacing=30, v_spacing=-6))]


@pytest.mark.parametrize('arrangement,params', ARRAYS, ids=[a[0] for a in ARRAYS])
@pytest.mark.parametrize('system,params_', [('plain', {}), ('hollow', {}), ('skin_web', {}), ('interleaved', {'paths': 4})],
                         ids=['plain', 'hollow', 'skin_web', 'interleaved'])
def test_diamond_arrays_are_headerless_on_every_construction(arrangement, params, system, params_):
    src = DesignerSource.from_designs(designs(line_doc(system, params_, length=260.0)))
    p = OpeningProfile('P', WallRef('L'), 40, 3, 30, 18, 'diamond', array=OpeningArray(arrangement, **params))
    a = Assembly(1.5, [Section('A', 90)], profiles=[p])
    res, rep, geo = stack(src, a)
    assert rep[0].valid, rep[0].errors
    # each layer prints exactly its clones' gaps
    for L in rep[0].layers[::7]:
        polys = geo[res.instances[L['layer']].geometry_key][0]
        l_, r_ = _printed_gap(polys, L['at'])
        assert r_ - l_ >= L['width'] - BEAD - 1e-6
    assert not _bridges(res, geo, a), 'a valid self-supporting array needs no header'


@pytest.mark.parametrize('system,params', [('plain', {}), ('hollow', {}), ('skin_web', {}), ('interleaved', {'paths': 4}),
                                           ('linked', {'paths': 4}), ('chain', {})],
                         ids=['plain', 'hollow', 'skin_web', 'interleaved', 'linked', 'chain'])
@pytest.mark.parametrize('upper', ['pointed', 'curve'])
def test_ground_level_arched_door_is_self_supporting(system, params, upper):
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=160.0)))
    p = OpeningProfile('P', WallRef('L'), 80, 0, 66, 24, 'arch', upper=upper, lower='flat')
    a = Assembly(1.5, [Section('A', 84)], profiles=[p])
    res, rep, geo = stack(src, a)
    assert rep[0].valid, rep[0].errors
    first = geo[res.instances[0].geometry_key][0]
    l_, r_ = _printed_gap(first, 80.0)
    assert r_ - l_ >= 24 - BEAD - 1e-6, 'the door is open from the ground layer'
    # the printed closure: no surviving side advances inward by more than h
    cut = sorted({L['layer'] for L in rep[0].layers})
    edges = [_printed_gap(geo[res.instances[k].geometry_key][0], 80.0) for k in range(cut[0], cut[-1] + 2)]
    for (l0, r0), (l1, r1) in zip(edges, edges[1:]):
        assert l1 - l0 <= a.max_overhang + 1e-6 and r0 - r1 <= a.max_overhang + 1e-6, (system, upper)
    assert not _bridges(res, geo, a)


@pytest.mark.parametrize('lower', ['semicircle', 'ellipse', 'pointed', 'flat'])
def test_lower_profiles_cut_and_the_closure_stays_supported_on_a_lattice_wall(lower):
    """On a wall whose cavity is filled (Skin + Web lattice), every lower
    profile cuts as drawn and nothing needs a header; its closure is the
    same self-supporting one."""
    src = DesignerSource.from_designs(designs(line_doc('skin_web', {}, length=160.0)))
    p = OpeningProfile('P', WallRef('L'), 80, 6, 48, 24, 'arch', upper='pointed', lower=lower, bottom_depth=6)
    a = Assembly(1.5, [Section('A', 72)], profiles=[p])
    res, rep, geo = stack(src, a)
    assert rep[0].valid
    assert not [f for f in analyse_support(res, geo, a).findings if f.status == 'needs_header'], lower


def test_rounded_bottom_on_a_hollow_wall_reports_its_unsupported_caps():
    """Not hidden: a semicircle's first layers widen faster than the jamb
    caps can follow over a hollow wall's cavity — the support check reports
    it at the profile and the profile says why."""
    src = DesignerSource.from_designs(designs(line_doc('hollow', {}, length=160.0)))
    p = OpeningProfile('P', WallRef('L'), 80, 6, 48, 24, 'arch', lower='semicircle')
    a = Assembly(1.5, [Section('A', 72)], profiles=[p])
    res, rep, geo = stack(src, a)
    fs = analyse_support(res, geo, a).findings
    assert rep[0].valid and any('jamb caps' in w for w in rep[0].warnings)
    assert fs and all(f.layer <= min(L['layer'] for L in rep[0].layers) + 2 for f in fs)


def test_array_cut_stays_local_on_a_lattice_wall():
    """Lattice coherence: on a Skin + Web wall the openings change the
    geometry only near them; the rest of the wall prints the uncut pattern."""
    src = DesignerSource.from_designs(designs(line_doc('skin_web', {}, length=320.0)))
    p = OpeningProfile('P', WallRef('L'), 60, 12, 30, 18, array=OpeningArray('horizontal', 3, h_spacing=30))
    a = Assembly(1.5, [Section('A', 60)], profiles=[p])
    res, rep, geo = stack(src, a)
    k = next(L['layer'] for L in rep[0].layers if L['width'] > 12)
    cut, plain = geo[res.instances[k].geometry_key][0], src.geometry('A').polylines
    far = [c for c in _changed_tol(cut, plain) if c[0] > 60 + 2 * 48 + 9 + 2 * T]
    assert not far, far[:5]


def test_array_coexists_with_a_section_transform_and_a_planar_cut():
    src = DesignerSource.from_designs(designs(line_doc('hollow', {}, length=240.0)))
    p = OpeningProfile('P', WallRef('L'), 40, 3, 30, 18, array=OpeningArray('horizontal', 3, h_spacing=30))
    a = Assembly(1.5, [Section('A', 72, transform=SectionTransform('linear', (0.05, 0.0), 0.0))], profiles=[p])
    res, rep, geo = stack(src, a)
    assert rep[0].valid and not _bridges(res, geo, a)
    q = PlaneProfile('Q', sources=('L',), direction=180.0, slope=0.25, anchor=(0.0, 0.0), z_anchor=50.0)
    a2 = Assembly(1.5, [Section('A', 72)], profiles=[p, q])
    res, rep, geo = stack(src, a2)
    assert rep[0].valid and any('planar top cut Q' in w for w in rep[0].warnings)
    assert all(geo[i.geometry_key][0] is not None for i in res.instances)


# ---- CONVERSION of a Designer opening (the demo door) -----------------------------------

DOOR_STACK = [Section('A', 36, 'Base'), Section('B', 24, 'Door Gap'), Section('A', 12, 'Base'), Section('C', 36, 'Window Gap')]


def conv(upper='pointed', z_ref=40.0, pid='K'):
    return OpeningProfile(pid, WallRef('W', 0), 100, 0, 0, 36, 'arch', upper=upper, lower='flat',
                          source_opening={'id': 'door', 'z_ref': z_ref})


@pytest.fixture(scope='module')
def demo():
    return DesignerSource.from_designs(demo_designs())


def test_designer_openings_are_reported_on_their_straight_edge(demo):
    assert demo.openings('A') == []
    o, = demo.openings('B')
    assert (o['id'], o['source'], o['edge'], o['at'], o['width']) == ('door', 'W', 0, 100.0, 36.0)


@pytest.mark.parametrize('upper', ['pointed', 'curve'])
def test_converted_doorway_keeps_its_clearance_and_needs_no_header(demo, upper):
    base = Assembly(1.5, DOOR_STACK)
    r0, _, g0 = stack(demo, base, ('A', 'B', 'C'))
    f0 = _bridges(r0, g0, base)
    lintel = [f for f in f0 if f.layer == 40]
    assert lintel, 'the door lintel needs a header'
    a = Assembly(1.5, DOOR_STACK, profiles=[conv(upper)])
    res, rep, geo = stack(demo, a, ('A', 'B', 'C'))
    r = rep[0]
    assert r.valid, r.errors
    assert (r.conversion['width'], r.conversion['rect_bottom'], r.conversion['rect_top']) == (36.0, 36.0, 60.0)
    # the door's own layers are the Designer's, untouched (no Assembly cut on them)
    for k in range(24, 40):
        assert not [c for c in res.instances[k].cuts if c[0] == 'opening']
        assert res.instances[k].geometry_key == r0.instances[k].geometry_key
    # the closure: genuinely supported — the support checker finds nothing at the door
    f1 = _bridges(res, geo, a)
    assert not [f for f in f1 if abs(f.local_centre[0] - 200) < 40 and abs(f.local_centre[1] - 140) < 20], f1
    # the clearance is kept: 36 in wide up to z 60, then the closure narrows
    cl = [L for L in r.layers if not L.get('designer')]
    assert cl[0]['layer'] == 40 and cl[0]['width'] < 36 and all(L['at'] == 100.0 for L in cl)


def test_removing_the_conversion_restores_the_header_finding(demo):
    a = Assembly(1.5, DOOR_STACK, profiles=[conv()])
    res, rep, geo = stack(demo, a, ('A', 'B', 'C'))
    assert rep[0].valid
    b = Assembly(1.5, DOOR_STACK)
    r2, _, g2 = stack(demo, b, ('A', 'B', 'C'))
    f2 = [f for f in _bridges(r2, g2, b) if f.layer == 40]
    assert f2, 'without the conversion the Designer opening needs its header again'


def test_insufficient_wall_above_the_door_is_reported_and_nothing_is_hidden(demo):
    short = [Section('A', 36), Section('B', 24), Section('A', 12)]
    a = Assembly(1.5, short, profiles=[conv()])
    res, rep, geo = stack(demo, a, ('A', 'B'))
    r = rep[0]
    assert not r.valid and r.hint['available'] == 12.0 and r.hint['required_height'] > 12
    assert not any(i.cuts for i in res.instances)
    f = _bridges(res, geo, a)
    assert any(x.layer == 40 for x in f), 'the header finding stays — nothing suppressed'


def test_only_the_converted_opening_changes(demo):
    """Two Door Gap runs: converting the first leaves the second (and its
    header finding) exactly as it was."""
    secs = [Section('A', 36), Section('B', 24), Section('A', 30), Section('B', 24), Section('A', 30)]
    base = Assembly(1.5, secs)
    r0, _, g0 = stack(demo, base, ('A', 'B'))
    second = [f for f in _bridges(r0, g0, base) if f.layer == 76]
    assert second
    a = Assembly(1.5, secs, profiles=[conv(z_ref=40.0)])
    res, rep, geo = stack(demo, a, ('A', 'B'))
    assert rep[0].valid and rep[0].conversion['rect_top'] == 60.0
    f = _bridges(res, geo, a)
    assert any(x.layer == 76 for x in f) and not any(x.layer == 40 for x in f)


def test_web_tags_the_designer_opening_and_offers_conversion(demo):
    """/api/assembly/resolve: the lintel finding names the Designer opening
    (convertible); after converting, it is gone; an invalid conversion is
    named on the finding that stays."""
    from flask import Flask
    from layer_assembly.web import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    body = {'assembly': Assembly(1.5, DOOR_STACK).to_dict(), 'designs': demo_designs()}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    lint = [f for f in out['support']['findings'] if f['kind'] == 'bridge' and f['layer'] == 40]
    assert lint and lint[0]['opening']['id'] == 'door' and lint[0]['opening']['convertible']
    assert lint[0]['opening']['at'] == 100.0 and lint[0]['opening']['edge'] == 0 and not lint[0]['opening']['converted']
    body['assembly'] = Assembly(1.5, DOOR_STACK, profiles=[conv()]).to_dict()
    out = c.post('/api/assembly/resolve', json=body).get_json()
    assert out['profiles'][0]['valid'] and not [f for f in out['support']['findings'] if f['layer'] == 40]
    body['assembly'] = Assembly(1.5, [Section('A', 36), Section('B', 24), Section('A', 12)], profiles=[conv()]).to_dict()
    out = c.post('/api/assembly/resolve', json=body).get_json()
    lint = [f for f in out['support']['findings'] if f['layer'] == 40]
    assert not out['profiles'][0]['valid'] and lint and lint[0]['opening']['converted'] == 'K'


# ---- HEADER upper: a flat top carried by a generated header (2026-10-10) ------------------

def _header_door(position=80, **kw):
    d = dict(shape='arch', upper='header', lower='flat', middle=True, middle_height=48.0, upper_height=None)
    d.update(kw)
    return OpeningProfile('P', WallRef('L'), position, 0, 0, 30, **d)


@pytest.mark.parametrize('system,params', [('plain', {}), ('hollow', {}), ('parallel', {'walls': 4}), ('skin_web', {})],
                         ids=['plain', 'hollow', 'parallel4', 'skin_web'])
def test_header_door_is_carried_by_its_generated_header(system, params):
    """The layer over a Header-upper opening spans it: the support checker
    finds that span and the GENERATED header (same Header object, same
    support rule as a placed one) carries it — nothing is left unsupported;
    its bearings pass through the jamb layers below (pockets reported)."""
    from layer_assembly.profiles import profile_headers
    src = DesignerSource.from_designs(designs(line_doc(system, params, length=160.0)))
    p = _header_door()
    a = Assembly(1.5, [Section('A', 72)], profiles=[p])
    res, rep, geo = stack(src, a)
    assert rep[0].valid, rep[0].errors
    first = geo[res.instances[0].geometry_key][0]
    l_, r_ = _printed_gap(first, 80.0)
    assert r_ - l_ >= 30 - BEAD - 1e-6, 'open from the ground layer, flat sill'
    (h,) = profile_headers(rep)
    assert (h.profile, h.layer, h.span, h.x) == ('P', 32, 30, 80)
    a.objects = list(a.objects) + profile_headers(rep)
    S = analyse_support(res, geo, a)
    open_ = [f for f in S.findings if f.status in ('needs_header', 'insufficient_support') and abs(f.layer - 32) <= 1]
    assert open_ == [], [(f.layer, f.kind, f.status, f.coverage) for f in open_]
    hd = next(x for x in S.headers if x['id'] == 'P-header')
    assert hd['profile'] == 'P' and not hd['error'] and hd['z_top'] == 48.0 and hd['z_bottom'] == 42.0
    assert hd['supports'] or not [f for f in S.findings if f.layer == 32], 'it carries the span over the flat top'
    if system != 'skin_web':
        assert hd['pocket_layers'], 'its bearings pass through the jamb layers below (pockets reported)'


def test_header_door_through_the_web_resolve_and_the_print_plan():
    """/api/assembly/resolve lists the generated header with its opening;
    the print plan's headers (installation) include it; nothing is stored in
    the Assembly's own objects."""
    from flask import Flask
    from layer_assembly.web import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    ds = designs(line_doc('hollow', {}, length=160.0))
    arr = OpeningArray('horizontal', count=2, h_spacing=24)
    asm = Assembly(1.5, [Section('A', 72)], profiles=[_header_door(position=40, array=arr)]).to_dict()
    body = {'assembly': asm, 'design_ids': ['A'], 'designs': ds}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    assert 'error' not in out, out
    pr = out['profiles'][0]
    assert pr['valid'], pr['errors']
    assert [h['id'] for h in pr['headers']] == ['P-header-1', 'P-header-2']
    hs = [h for h in out['support']['headers'] if h.get('profile') == 'P']
    assert sorted(h['id'] for h in hs) == ['P-header-1', 'P-header-2'] and all(not h['error'] for h in hs)
    assert not [f for f in out['support']['findings'] if f['status'] == 'needs_header'], 'both spans carried'
    plan = c.post('/api/assembly/print_plan', json=dict(body, detail='stream')).get_json()
    assert not plan.get('error'), plan.get('error')
    assert sorted(h['id'] for h in plan['headers'] if h.get('profile') == 'P') == ['P-header-1', 'P-header-2']
    assert asm['objects'] == []

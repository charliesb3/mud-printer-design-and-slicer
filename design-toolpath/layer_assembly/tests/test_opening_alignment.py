"""
OPENING ALIGNMENT (2026-10-10, ONE global Assembly setting —
`Assembly.opening_alignment`): Follow Wall (default — every opening follows
the Assembly transforms) | Vertical / Plumb (every opening — Designer,
Assembly, arrays, headers — vertical in WORLD coordinates from its first
active layer while the walls keep transforming; one algorithm,
profiles.plumb_track). Pure tests first, then the real Designer.
"""
import json
import math
import os
import sys

import pytest

from layer_assembly import opening_shape as OS
from layer_assembly.model import Assembly, Footprint, Section, SectionTransform, resolve


# (written for the 1.5 in default Maximum Overhang — since 2026-10-11 the
# default is 1 in: these cases state the 1.5 in they were designed with)
_Assembly = Assembly


def Assembly(*a, **k):          # noqa: F811
    if len(a) < 9:
        k.setdefault('max_overhang', 1.5)
    return _Assembly(*a, **k)


Assembly.from_dict = _Assembly.from_dict
from layer_assembly.profiles import (OpeningArray, OpeningProfile, WallRef, WallTarget, apply_profiles,
                                     cuts_from_dicts, cuts_to_dicts, plumb_track, profile_from_dict)
from dataclasses import replace

LH, H, BEAD = 1.5, 1.5, 3.0
WALL = WallTarget('L', None, (0.0, 0.0), (240.0, 0.0), 240.0, 10.0, True, 'Wall')
FP = {'A': Footprint.of_bbox(0, -5, 240, 5)}


def _door(zb=0.0, pos=100.0, width=30.0, upper='pointed', mh=60.0, U=18.0, lower='flat', **kw):
    return OpeningProfile('P', WallRef('L'), pos, zb, 0, width, upper=upper, lower=lower, middle=True,
                          middle_height=mh, upper_height=U, bottom_depth=None if lower == 'flat' else 12.0, **kw)


def _run(p, tf=SectionTransform('linear', (0.3, 0.0), 0.0), top=150, profiles=None, al='plumb', res=None):
    ps = profiles or [p]
    if res is None:
        res = resolve(Assembly(LH, [Section('A', top, '', tf)], profiles=ps), ['A'], FP)
    return apply_profiles(res, ps, {'A': {'L': WALL}}, BEAD, H, None, None, al)


def _world(res, L):
    """(world centre along the wall, world width) of a resolved cut layer."""
    T = res.instances[L['layer']].transform
    return T.scale * L['at'] + T.tx, L['width'] * T.scale


# ---- Follow Wall stays the default and unchanged -------------------------------------------

def test_follow_wall_is_the_default_and_unchanged():
    assert Assembly(LH, []).opening_alignment == 'follow' and 'opening_alignment' not in Assembly(LH, []).to_dict()
    res, rep = _run(_door(), al='follow')
    res0, rep0 = apply_profiles(resolve(Assembly(LH, [Section('A', 150, '', SectionTransform('linear', (0.3, 0.0), 0.0))]),
                                        ['A'], FP), [_door()], {'A': {'L': WALL}}, BEAD, H)
    assert rep[0].layers == rep0[0].layers and [i.cuts for i in res.instances] == [i.cuts for i in res0.instances], \
        'Follow Wall = exactly the behaviour without the setting'
    assert rep[0].valid and {L['at'] for L in rep[0].layers} == {100.0}, 'the opening follows the wall (leans)'
    xs = [_world(res, L)[0] for L in rep[0].layers]
    assert xs[-1] - xs[0] > 10, 'world position drifts with the shift (as before)'


# ---- Vertical / Plumb ----------------------------------------------------------------------

@pytest.mark.parametrize('tf', [SectionTransform('linear', (0.3, 0.0), 0.0), SectionTransform('linear', (-0.25, 0.1), 0.0),
                                SectionTransform('quadratic', (0.4, 0.0), 0.0)], ids=['linear', 'diagonal', 'quadratic'])
def test_plumb_doorway_from_the_ground_stays_put_in_world(tf):
    res, rep = _run(_door(), tf)
    r = rep[0]
    assert r.valid, r.errors
    T0 = res.instances[0].transform
    X0 = T0.scale * 100 + T0.tx
    for L in r.layers:
        x, w = _world(res, L)
        assert x == pytest.approx(X0, abs=2e-4)
        if L['width'] >= 29.99:
            assert w == pytest.approx(30, abs=2e-4)
    assert len({L['at'] for L in r.layers}) > 10, 'cut at a different design position on each layer'
    assert r.members[0]['anchor_layer'] == 0


def test_plumb_window_above_ground_is_anchored_at_its_first_layer():
    res, rep = _run(_door(zb=48, mh=24))
    r = rep[0]
    assert r.valid, r.errors
    first = min(L['layer'] for L in r.layers)
    assert first == 32 and r.members[0]['anchor_layer'] == 32
    T = res.instances[first].transform
    X0 = T.scale * 100 + T.tx                                # its place on the TRANSFORMED wall at z 48
    assert X0 == pytest.approx(100 + 0.3 * 32)
    assert all(_world(res, L)[0] == pytest.approx(X0, abs=2e-4) for L in r.layers)


@pytest.mark.parametrize('scale_in', [0.15, -0.15], ids=['inward', 'outward'])
def test_inward_and_outward_scaling_keeps_the_world_shape(scale_in):
    res, rep = _run(_door(), SectionTransform('linear', (0.0, 0.0), scale_in))
    r = rep[0]
    assert r.valid, r.errors
    sc = [res.instances[L['layer']].transform.scale for L in r.layers]
    assert max(sc) - min(sc) > 0.05, 'the layers really scale'
    xs = {round(_world(res, L)[0], 3) for L in r.layers}
    assert len(xs) == 1
    full = [_world(res, L)[1] for L in r.layers if L['layer'] < 30]
    assert all(w == pytest.approx(30, abs=2e-4) for w in full), 'the intended (physical) width on every layer'
    f_res, f_rep = _run(_door(), SectionTransform('linear', (0.0, 0.0), scale_in), al='follow')
    assert len({round(_world(f_res, L)[1], 3) for L in f_rep[0].layers if L['layer'] < 30}) > 1, \
        'Follow Wall scales with the wall'


@pytest.mark.parametrize('upper,lower', [('pointed', 'flat'), ('curve', 'round'), ('header', 'flat'), ('curve', 'arc'),
                                         ('pointed', 'pointed')])
def test_plumb_keeps_the_upper_profile_and_overhang_rule(upper, lower):
    s, _ = OS.constrain(dict(upper=upper, lower=lower, width=24, upper_height=6, middle=True, middle_height=30,
                             bottom_depth=None if lower == 'flat' else 15, z_bottom=6, array=None, height=0),
                        'width', LH, H, BEAD)
    p = OpeningProfile('P', WallRef('L'), 110, 6, s['height'], 24, upper=upper, lower=lower, middle=True,
                       middle_height=30, upper_height=s['upper_height'], bottom_depth=s['bottom_depth'])
    res, rep = _run(p, SectionTransform('linear', (0.2, 0.0), 0.08))
    r = rep[0]
    assert r.valid, r.errors
    assert r.worst_step <= H + 1e-9, 'Maximum Overhang on the WORLD widths'
    d, m, U, Ht = OS.parts(s)
    for L in r.layers:                                   # the intended profile, in world, at every layer
        zc = L['z'] - 6
        assert _world(res, L)[1] == pytest.approx(max(OS.width_at(zc, 24, d, m, U, upper, lower), OS.wmin(BEAD, H)),
                                                  abs=2e-4)
    if upper == 'header':
        (hd,) = r.headers
        last = max(L['layer'] for L in r.layers)
        T = res.instances[last + 1].transform
        T0 = res.instances[min(L['layer'] for L in r.layers)].transform
        assert T.scale * hd['x'] + T.tx == pytest.approx(T0.scale * 110 + T0.tx, abs=1e-3), 'its header too'


def test_too_steep_in_world_is_reported():
    p = _door(width=40, U=6)
    _, rep = _run(p)
    assert not rep[0].valid and any('not self-supporting' in e for e in rep[0].errors)


def test_plumb_array_each_opening_anchored_spacing_kept():
    arr = OpeningArray('grid', cols=3, rows=2, h_spacing=14, v_spacing=6)
    p = _door(zb=6, pos=40, width=20, mh=18, U=12, array=arr)
    res, rep = _run(p, SectionTransform('linear', (0.2, 0.0), 0.0))
    r = rep[0]
    assert r.valid, r.errors
    assert [m['anchor_layer'] for m in r.members] == [4, 4, 4, 28, 28, 28]       # each row: its own first layer
    by = {}
    for L in r.layers:
        by.setdefault(L['member'], set()).add(round(_world(res, L)[0], 3))
    assert all(len(v) == 1 for v in by.values()), 'every opening vertical in world'
    row0 = sorted(next(iter(by[j])) for j in range(3))
    assert row0[1] - row0[0] == pytest.approx(34) and row0[2] - row0[1] == pytest.approx(34), 'clear spacing kept'


def test_an_opening_that_cannot_stay_plumb_follows_the_wall_and_is_reported():
    # along the wall: it would slide off the end
    res, rep = _run(_door(pos=40), SectionTransform('linear', (1.2, 0.0), 0.0))
    r = rep[0]
    assert r.valid and any('cannot stay vertical' in w and 'follows the wall' in w for w in r.warnings)
    assert {L['at'] for L in r.layers} == {40.0}, 'it follows the wall (no broken geometry)'
    # across the wall: the wall moves completely away from its vertical line
    res, rep = _run(_door(), SectionTransform('linear', (0.0, 0.3), 0.0))
    r = rep[0]
    assert r.valid and any('moved' in w and 'away' in w for w in r.warnings)
    # a smaller sideways move: still plumb, cut through the moved wall
    res, rep = _run(_door(), SectionTransform('linear', (0.0, 0.1), 0.0), top=60)
    assert not rep[0].warnings and rep[0].members[0].get('anchor_layer') == 0


def test_transform_group_walls_are_compensated():
    """'multiple' centre mode: the wall is moved by its GROUP (inst.semantic,
    relative to the layer's own placement) — plumb uses the wall's actual
    placement, so it stays vertical too."""
    res = resolve(Assembly(LH, [Section('A', 60)]), ['A'], FP)
    res = replace(res, instances=[replace(i, semantic=(('L', 1.0, 0.4 * i.index, 0.0),)) for i in res.instances])
    _, rep = _run(_door(mh=30, U=18), res=res)
    r = rep[0]
    assert r.valid and not r.warnings
    for L in r.layers:
        assert L['at'] + 0.4 * L['layer'] == pytest.approx(100.0, abs=1e-6), 'world = placement_group(design)'


def test_plumb_track_math():
    insts = resolve(Assembly(LH, [Section('A', 30, '', SectionTransform('linear', (0.5, 0.25), 0.1))]), ['A'],
                    FP).instances
    tr = plumb_track(insts, range(len(insts)), 'L', (120.0, 0.0), 3)
    assert tr[3][0] == pytest.approx((120.0, 0.0))
    X0 = insts[3].transform.apply(120.0, 0.0)
    for k, (q, f) in tr.items():
        assert insts[k].transform.apply(*q) == pytest.approx(X0) and f == pytest.approx(1 / insts[k].transform.scale)


# ---- persistence --------------------------------------------------------------------------

def test_save_open_and_migration_of_the_per_opening_form():
    a = Assembly(LH, [Section('A', 120)], profiles=[_door()], opening_alignment='plumb')
    d = json.loads(json.dumps(a.to_dict()))
    assert d['opening_alignment'] == 'plumb' and 'alignment' not in d['profiles'][0]
    assert Assembly.from_dict(d).opening_alignment == 'plumb'
    base = {'layer_height': 1.5, 'max_overhang': 1.5, 'sections': [{'design_id': 'A', 'height': 12}]}
    pd = _door().to_dict()
    mig = lambda **kw: Assembly.from_dict(dict(base, **kw)).opening_alignment
    assert mig() == 'follow', 'older projects: Follow Wall'
    assert mig(opening_alignment={'door': 'plumb'}) == 'plumb', 'Designer openings plumb, no Assembly opening'
    assert mig(profiles=[dict(pd, alignment='plumb')]) == 'plumb'
    assert mig(opening_alignment={'door': 'plumb'}, profiles=[dict(pd, alignment='plumb')]) == 'plumb'
    assert mig(opening_alignment={'door': 'plumb'}, profiles=[pd]) == 'follow', 'mixed: cannot be kept → Follow Wall'
    assert mig(profiles=[dict(pd, alignment='plumb'), dict(pd, id='Q')]) == 'follow'
    assert profile_from_dict(dict(pd, alignment='plumb')) == profile_from_dict(pd), 'no per-opening alignment any more'
    with pytest.raises(ValueError):
        mig(opening_alignment='sideways')
    cuts = (('move', 'W', 'door', 197.5, 140.0, 'align:door', 36.0),)
    assert cuts_from_dicts(cuts_to_dicts(cuts)) == cuts


# ---- the real Designer ---------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(__file__))
from test_profiles_designer import designs, line_doc, open_ends, targets_of, MAT, _printed_gap  # noqa: E402


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _real(src, a, ids=('A',)):
    from layer_assembly.source import stack_geometry
    from layer_assembly.web import bbox_of
    fps = {d: Footprint.of_bbox(*bbox_of(src.geometry(d).polylines)) for d in ids}
    res = resolve(a, list(ids), fps)
    res, rep = apply_profiles(res, a.profiles, targets_of(src, ids), BEAD, a.max_overhang,
                              {d: src.sources(d) for d in ids}, {d: src.openings(d) for d in ids},
                              a.opening_alignment)
    rep = [r for r in rep if r.kind != 'alignment'] + [r for r in rep if r.kind == 'alignment']
    return res, rep, stack_geometry(res, src)


def _gap_x(polys, y, x_lo, x_hi, band=6.0):
    xs = sorted(x for pl in polys for x, yy in pl['pts'] if abs(yy - y) < band and x_lo < x < x_hi)
    g = max(((b - a, a, b) for a, b in zip(xs, xs[1:])), default=None)
    return None if g is None else (g[1], g[2])


@pytest.fixture(scope='module')
def demo():
    from layer_assembly.designer_source import DesignerSource
    from layer_assembly.demo import demo_designs
    return DesignerSource.from_designs(demo_designs())


@pytest.mark.parametrize('al', ['follow', 'plumb'])
def test_designer_door_plumb_on_the_demo_room(demo, al):
    """The demo Door Gap's door under a sideways shift: plumb = the same world
    gap on every layer (full width, both faces), no new support findings;
    Follow Wall leans as before."""
    from layer_assembly.support import analyse_support
    tf = SectionTransform('linear', (0.25, 0.0), 0.0)
    secs = [Section('A', 36, 'Base', tf), Section('B', 24, 'Door Gap', tf), Section('A', 12, 'Base', tf)]
    a = Assembly(1.5, secs, opening_alignment=al)
    res, rep, geo = _real(demo, a, ('A', 'B'))
    centres = []
    for k in range(24, 40):
        T = res.instances[k].transform
        g = _gap_x(geo[res.instances[k].geometry_key][0], 140, 150, 250)
        assert g is not None
        centres.append(round((g[0] + g[1]) / 2 + T.tx, 3))
        assert g[1] - g[0] == pytest.approx(36, abs=0.6), 'the full opening on every layer'
    if al == 'plumb':
        assert len(set(centres)) == 1 and rep[0].valid and rep[0].members[0]['anchor_layer'] == 24
        assert sum(1 for i in res.instances for c in i.cuts if c[0] == 'move') == 16
    else:
        assert centres[-1] - centres[0] > 3
    finds = analyse_support(res, geo, a).findings
    assert [(f.layer, f.kind) for f in finds] == [(40, 'bridge')], 'only the door lintel (as without alignment)'
    for k in (24, 31, 39):                                # closed routes: proper caps on the moved layers
        assert open_ends(demo, 'B', res.instances[k].cut_spec()) == []


def test_designer_opening_on_a_curved_wall_follows_the_nearest_point(demo):
    """A Designer opening on a CIRCLE: plumb = on every layer at the point of
    the transformed circle nearest its world anchor (re-placed by arc
    length on the processed curve)."""
    from layer_assembly.designer_source import DesignerSource
    doc = {'id': 'c', 'material': dict(MAT),
           'source_paths': [{'id': 'C', 'type': 'CirclePath', 'label': 'Room', 'cx': 0, 'cy': 0, 'radius': 60,
                             'closed': True, 'wall': {'thickness': 10}}],
           'openings': [{'id': 'win', 'type': 'opening', 'source_path_id': 'C', 'center_s': 30.0, 'width': 18,
                         'end_treatment': 'inherit', 'z_min': None, 'z_max': None, 'label': 'Window'}]}
    src = DesignerSource.from_designs(designs(doc))
    o = src.openings('A')[0]
    assert o.get('at') is None and o['xy'] and o['thickness'] == 10
    tf = SectionTransform('linear', (0.2, 0.0), 0.0)
    a = Assembly(1.5, [Section('A', 30, '', tf)], opening_alignment='plumb')
    res, rep, geo = _real(src, a)
    r = rep[0]
    assert r.valid, r.errors
    X0 = res.instances[0].transform.apply(*o['xy'])
    for k in (0, 10, 19):
        mv = next(c for c in res.instances[k].cuts if c[0] == 'move')
        T = res.instances[k].transform
        assert T.apply(mv[3], mv[4]) == pytest.approx(X0, abs=1e-4), 'the same world anchor on every layer'
        g = src.geometry('A', None, res.instances[k].cut_spec())   # (the Designer applied the move)
        moved = [p for p in g.diagnostics.get('profile') or [] if p.get('op') == 'move']
        assert moved and moved[0]['applied'] and moved[0]['off'] < 5, 'on the curve, near the anchor'
    # far too much shift: the vertical line leaves the curved wall → reported, not applied
    a2 = Assembly(1.5, [Section('A', 30, '', SectionTransform('linear', (1.5, 0.0), 0.0))],
                  opening_alignment='plumb')
    res2, rep2, _ = _real(src, a2)
    assert rep2[0].valid and 'cannot stay vertical' in rep2[0].warnings[0] and 'follows the wall' in rep2[0].warnings[0]
    assert not any(c[0] == 'move' for i in res2.instances for c in i.cuts)


def test_plumb_assembly_opening_on_a_lattice_wall_is_coherent():
    """Skin + Web wall, a shifting stack: every cut layer closes (caps), the
    web stays coherent (no support findings), the gap is vertical in world."""
    from layer_assembly.designer_source import DesignerSource
    from layer_assembly.support import analyse_support
    src = DesignerSource.from_designs(designs(line_doc('skin_web', length=200.0)))
    p = OpeningProfile('P', WallRef('L'), 90, 6, 0, 20, upper='pointed', lower='flat', middle=True, middle_height=24,
                       upper_height=12)
    a = Assembly(1.5, [Section('A', 60, '', SectionTransform('linear', (0.15, 0.0), 0.0))], profiles=[p],
                 opening_alignment='plumb')
    res, rep, geo = _real(src, a)
    assert rep[0].valid, rep[0].errors
    X = set()
    for L in rep[0].layers:
        if L['width'] < 19.9:
            continue
        k = L['layer']
        g = _printed_gap(geo[res.instances[k].geometry_key][0], L['at'])
        assert g[1] - g[0] >= 20 - BEAD - 1e-6, 'through the whole wall, full width'
        X.add(round((g[0] + g[1]) / 2 + res.instances[k].transform.tx, 2))
        assert open_ends(src, 'A', res.instances[k].cut_spec()) == []
    assert len(X) == 1
    assert analyse_support(res, geo, a).findings == []


def test_print_plan_and_web_reflect_plumb_geometry():
    """/api/assembly/resolve lists the Designer openings + alignment results;
    the print plan routes the moved (plumb) geometry."""
    from flask import Flask
    from layer_assembly.web import bp, _planned
    from layer_assembly.demo import demo_designs
    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    tf = {'profile': 'linear', 'shift': [0.25, 0], 'scale_in': 0}
    asm = {'layer_height': 1.5, 'max_overhang': 1.5, 'sections': [{'design_id': 'A', 'height': 36, 'transform': tf},
                                             {'design_id': 'B', 'height': 24, 'transform': tf}]}
    body = {'assembly': asm, 'designs': demo_designs()}
    out = c.post('/api/assembly/resolve', json=body).get_json()
    assert any(o['id'] == 'door' for o in out['designer_openings']) and out['alignments'] == []
    body_p = {'assembly': dict(asm, opening_alignment='plumb'), 'designs': demo_designs()}
    out_p = c.post('/api/assembly/resolve', json=body_p).get_json()
    (al,) = out_p['alignments']
    assert al['opening'] == 'door' and al['valid'] and len(al['layers']) == 16
    from layer_assembly.print_plan import items

    def door_centre(plan, layer):
        """The door gap of a layer IN THE PRINT PLAN (world XY of its print moves)."""
        xs = sorted(x for it in items(plan) if it[0] == 'print' and it[1] == layer
                    for x, y in it[3] if abs(y - 140) < 6 and 150 < x < 270)
        g = max(((b_ - a_, a_, b_) for a_, b_ in zip(xs, xs[1:])))
        return round((g[1] + g[2]) / 2, 3), round(g[0], 3)
    plan_f, _ = _planned(body)
    plan_p, _ = _planned(body_p)
    assert door_centre(plan_p, 24) == door_centre(plan_p, 39) == (206.0, 36.0), 'the plan prints the plumb door'
    assert door_centre(plan_f, 39)[0] > 209, 'Follow Wall: the plan prints the leaning door'
    keys_f = [i['geometry_key'] for i in out['instances']]
    keys_p = [i['geometry_key'] for i in out_p['instances']]
    assert keys_f[:24] == keys_p[:24] and keys_f[24:] != keys_p[24:]


def test_designer_and_assembly_openings_together_are_plumb(demo):
    """ONE setting, every opening: the demo door (Designer) and an Assembly
    window array with a header-supported opening — all vertical in world;
    Follow Wall: all lean."""
    tf = SectionTransform('quadratic', (0.5, 0.0), 0.0)
    secs = [Section('A', 36, 'Base', tf), Section('B', 24, 'Door Gap', tf), Section('A', 24, 'Base', tf)]
    win = OpeningProfile('P', WallRef('W', 2), 50, 12, 0, 16, upper='curve', lower='flat', middle=True, middle_height=12,
                         upper_height=24, array=OpeningArray('horizontal', count=2, h_spacing=24))
    hdr = OpeningProfile('Q', WallRef('W', 0), 50, 6, 0, 24, upper='header', lower='flat', middle=True, middle_height=24)
    xs = {}
    for al in ('follow', 'plumb'):
        a = Assembly(1.5, secs, profiles=[win, hdr], opening_alignment=al)
        res, rep, geo = _real(demo, a, ('A', 'B'))
        by = {r.id: r for r in rep}
        assert by['P'].valid and by['Q'].valid, (by['P'].errors, by['Q'].errors)
        assert len(by['Q'].headers) == 1
        world = {}
        tg = targets_of(demo, ('A',))['A']
        for rid, key in (('P', 'W#2'), ('Q', 'W#0')):
            t = tg[key]
            ux, uy = (t.b[0] - t.a[0]) / t.length, (t.b[1] - t.a[1]) / t.length
            for L in by[rid].layers:                 # the world point of the cut centre, along the wall (x)
                X = res.instances[L['layer']].transform.apply(t.a[0] + ux * L['at'], t.a[1] + uy * L['at'])
                world.setdefault((rid, L.get('member', 0)), set()).add(round(X[0], 3))
        door = set()
        for k in range(24, 40):
            g = _gap_x(geo[res.instances[k].geometry_key][0], 140, 150, 250)
            door.add(round((g[0] + g[1]) / 2 + res.instances[k].transform.tx, 3))
        xs[al] = (world, door)
    pw, pd = xs['plumb']
    fw, fd = xs['follow']
    assert all(len(v) == 1 for v in pw.values()) and len(pd) == 1, ('plumb: every opening vertical in world', pw, pd)
    assert all(len(v) > 1 for v in fw.values()) and len(fd) > 1, 'follow: every opening leans (unchanged)'

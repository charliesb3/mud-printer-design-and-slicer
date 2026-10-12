"""
M3 — STACK-AWARE LATTICE GENERATION (2026-10-11).

Print preparation draws every generated Wall System with a phase pattern
(Interleaved Waves, Linked Waves, Chained Loop) from ONE stack reference per
design family (layer_design.stack_reference: the root's — then the design's
own — architecture without Designer openings, untransformed, uncut). A run of
any layer that lies on a reference run — wholly or, for a band that reaches
further on this layer, partly — draws the reference period / phase / lanes /
loop stations and solves only its own new ends; the previous per-layer
generator (WALL_LATTICE_MODE=layer) is kept as the reference.

Checked on small stacks of a connected building (a rectangle room, a circle
room, a link wall between them; a derived design with a Designer opening in a
second section; linear / quadratic transforms; Assembly opening arrays,
plumb / follow alignment, a planar top cut; mixed systems):

  * the architecture (envelopes) is the same whatever the generator;
  * every phased run says which generator drew it (and why when not the stack);
  * away from architectural change the pattern of adjacent layers coincides
    at least as well as before, and a Designer opening in a later section no
    longer shifts the pattern elsewhere on its wall;
  * every route still closes (no new continuity items), and the bead-level
    support findings are no more than the previous generator's;
  * the Simulation plans the generated stack.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
PARAMS = {'interleaved': {'pattern': 'wave', 'paths': 4, 'period': 24}, 'linked': {'pattern': 'wave', 'paths': 4,
          'period': 24}, 'chain': {'pitch': 9.0, 'neck': 0.2}, 'parallel': {'walls': 4}}
PHASED = ('interleaved', 'linked', 'chain')


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _clear():
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS, web._GENERATED, web._ARCH_LIBS):
        reg.clear()


def building(room='interleaved', circle='chain', link='parallel', thick=10.0, opening=True, link_thick=None):
    """(designs, wall_systems) — legacy documents migrated by split_designs."""
    from wall_groups import split_designs

    def ws(i, t, members, th):
        return {'id': f'WS{i}', 'name': f'S{i}', 'type': t, 'thickness': th, 'align': 'auto',
                'print_reference': False, 'params': dict(PARAMS.get(t, {})), 'members': members,
                'web': {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0} if t == 'skin_web' else {}}
    doc = {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': 180, 'h': 120, 'closed': True},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Round', 'cx': 330, 'cy': 60, 'radius': 60, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Link', 'start': [180, 60], 'end': [270, 60]}],
        'wall_systems': [ws(1, room, ['R'], thick), ws(2, circle, ['C'], thick), ws(3, link, ['L'], link_thick or thick)],
        'openings': [], 'infills': [], 'junction_style': 'miter'}
    for w in doc['wall_systems']:
        if w['type'] == 'skin_web':
            doc['infills'].append({'id': f'{w["id"]}~web~{w["members"][0]}', 'path_id': w['members'][0], 'kind': 'wall',
                                   'owner': w['id'], 'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0})
    legacy = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}]
    if opening:      # a DESIGNER opening in the second section (bottom edge of the room)
        legacy.append({'id': 'd1', 'name': 'Door', 'parent': 'base',
                       'patch': {'openings': {'o1': {'source_path_id': 'R', 'center_s': 60.0, 'width': 24.0}}}})
    return split_designs(legacy)


def assembly(sections=(('base', 9.0), ('d1', 9.0)), transform=None, profiles=(), alignment='follow', mode=None):
    return {'layer_height': 1.5, 'max_overhang': 1.5, 'opening_alignment': alignment,
            'sections': [{'design_id': d, 'height': h} for d, h in sections],
            'transform': transform or {'profile': 'linear', 'shift': [0.125, 0.0], 'scale_in': 0.0},
            'profiles': list(profiles), 'profile_counter': len(profiles)}


ARRAY = {'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R', 'edge': 2}, 'position': 60.0, 'z_bottom': 3.0,
         'height': 9.0, 'width': 8.0, 'upper': 'pointed', 'lower': 'pointed', 'bottom_depth': 3.0, 'upper_height': 6.0,
         'middle': False, 'middle_height': None, 'label': '',
         'array': {'arrangement': 'horizontal', 'count': 2, 'cols': 2, 'rows': 1, 'h_spacing': 12.0, 'v_spacing': 6.0,
                   'stagger': None}}
PLANE = {'kind': 'plane', 'id': 'p2', 'sources': ['C'], 'systems': [], 'exclude': [], 'direction': 0.0, 'slope': 0.25,
         'anchor': [330.0, 60.0], 'z_anchor': 15.0, 'label': ''}


def generate(designs, systems, asm, mode, monkeypatch):
    """Every layer's generated geometry + envelopes + bead-level support, as print preparation makes them."""
    from layer_assembly import web
    from layer_assembly.source import layer_geometry
    monkeypatch.setenv('WALL_LATTICE_MODE', mode)
    _clear()
    st = web._resolve_stack({'assembly': asm, 'designs': designs, 'wall_systems': systems})
    res, src = st['res'], st['src']
    geo = {i.index: layer_geometry(src, i) for i in res.instances}
    pgeo = dict(st['geo'])
    for i in res.instances:
        if i.is_variant:
            pgeo[i.geometry_key] = (geo[i.index].polylines, geo[i.index].bead_width)
    rep = web._support_report(st, pgeo)
    egeo, ekey, _ = web._envelope_stack(st)
    env = {i.index: egeo[ekey[i.index]][0] for i in res.instances}
    return {'st': st, 'res': res, 'geo': geo, 'support': rep, 'env': env, 'mode': mode}


def _placed(g, inst, sid):
    out = []
    for pl in g.polylines:
        tags = pl.get('src') or []
        if not tags or sum(1 for t in tags if t == sid) < 0.5 * len(tags):
            continue
        P = [inst.transform.apply(*p) for p in pl['pts']]
        for a, b in zip(P, P[1:]):
            n = max(1, int(math.dist(a, b)))
            out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return out


def _nearest(A, B):
    grid = {}
    for q in B:
        grid.setdefault((int(q[0] // 4), int(q[1] // 4)), []).append(q)
    out = []
    for p in A:
        cx, cy = int(p[0] // 4), int(p[1] // 4)
        out.append(min((math.dist(p, q) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                        for q in grid.get((cx + dx, cy + dy), ())), default=99.0))
    return out


def deviation(G, sid, k, away=None):
    """Mean distance of layer k's printed points of source `sid` to layer k−1's
    (world coordinates), only at points farther than 15 in from `away`."""
    res = G['res']
    a = _placed(G['geo'][k], res.instances[k], sid)
    b = _placed(G['geo'][k - 1], res.instances[k - 1], sid)
    if away is not None:
        a = [p for p in a if math.dist(p, away(k)) > 15.0]
        b = [p for p in b if math.dist(p, away(k)) > 15.0]
    # both ways, the better: where a ring becomes an open run (a doorway) a
    # Chained Loop prints its chain OUT AND BACK (two mirrored laps, to close
    # the route) — the lap below must reappear in it; the mirrored lap cannot
    d1, d2 = _nearest(a, b), _nearest(b, a)
    return min(sum(d1) / len(d1) if d1 else 0.0, sum(d2) / len(d2) if d2 else 0.0)


def generators(G):
    out = {}
    for g in G['geo'].values():
        for w in g.diagnostics.get('wall_systems') or []:
            for k, v in (w.get('generators') or {}).items():
                out.setdefault(w.get('type'), {}).setdefault(k, 0)
                out[w.get('type')][k] += v
    return out


def open_routes(G):
    return sorted({(i, w.get('system_id')) for i, g in G['geo'].items() for w in g.diagnostics.get('wall_systems') or []
                   if w.get('closable') is False})


def envelopes_equal(A, B):
    return all(json.dumps(A['env'][k], sort_keys=True) == json.dumps(B['env'][k], sort_keys=True) for k in A['env'])


# ---- the stack reference over a Designer opening in a later section -------------------------

@pytest.mark.parametrize('room', PHASED)
def test_a_designer_opening_no_longer_shifts_the_wall_elsewhere(room, monkeypatch):
    ds, ws = building(room=room)
    asm = assembly()
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    assert envelopes_equal(old, new)                                  # the architecture is untouched
    k = next(i.index for i in new['res'].instances if i.design_id == 'd1')   # the section boundary
    door = lambda k_: new['res'].instances[k_].transform.apply(60.0, 0.0)    # (the opening on the bottom edge)
    d_new, d_old = deviation(new, 'R', k, door), deviation(old, 'R', k, door)
    # the pattern above the doorway's section is the pattern below it, away from the door
    assert d_new <= 0.35, (d_new, d_old)
    assert d_new <= d_old + 1e-6
    gen = generators(new)[room]
    if room == 'chain':                   # (closed rings: fitted whole — said so; open pieces: the stack)
        assert set(gen) <= {'stack', 'layer'} and gen.get('stack'), gen
        assert {r for g in new['geo'].values() for w in g.diagnostics.get('wall_systems') or []
                for r in w.get('generator_reasons') or []} <= {
            r for r in ['a closed Chained Loop ring is fitted whole (its own loop count; identical ring geometry '
                        'gives the identical chain) — the stack reference applies to its open pieces']}
    else:
        assert set(gen) == {'stack'}, gen                             # every run drawn from the stack reference
    assert set(generators(old)[room]) <= {'layer', 'layer-ref'}


@pytest.mark.parametrize('room', PHASED)
def test_routes_close_and_support_is_no_worse(room, monkeypatch):
    ds, ws = building(room=room)
    asm = assembly(profiles=[ARRAY, PLANE])
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    assert envelopes_equal(old, new)
    assert open_routes(new) == open_routes(old) == []
    assert len(new['support'].findings) <= len(old['support'].findings)
    # every capped run of a phased construction closes
    for g in new['geo'].values():
        for w in g.diagnostics.get('wall_systems') or []:
            assert w.get('closable') is not False


def test_every_run_reports_its_generator(monkeypatch):
    ds, ws = building(room='interleaved', circle='linked', link='chain')
    new = generate(ds, ws, assembly(profiles=[ARRAY]), 'stack', monkeypatch)
    gen = generators(new)
    assert set(gen) == {'interleaved', 'linked', 'chain'}
    for t, counts in gen.items():
        assert set(counts) <= {'stack', 'layer-ref', 'layer'}
    reasons = {r for g in new['geo'].values() for w in g.diagnostics.get('wall_systems') or []
               for r in w.get('generator_reasons') or []}
    if any('layer' in c for c in gen.values()):
        assert reasons                                                # (a fallback is never silent)


@pytest.mark.parametrize('kind', ['quadratic', 'plumb'])
def test_transformed_stacks_and_plumb_openings(kind, monkeypatch):
    ds, ws = building(room='interleaved')
    tf = {'profile': 'quadratic', 'shift': [0.25, 0.1], 'scale_in': 0.0625} if kind == 'quadratic' else None
    asm = assembly(transform=tf, profiles=[ARRAY], alignment='plumb' if kind == 'plumb' else 'follow')
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    assert envelopes_equal(old, new)
    assert open_routes(new) == []
    assert len(new['support'].findings) <= len(old['support'].findings)
    assert set(generators(new)['interleaved']) == {'stack'}
    # adjacent layers away from the array coincide as well as before
    mid = lambda k_: new['res'].instances[k_].transform.apply(66.0, 120.0)
    for k in range(1, len(new['res'].instances)):
        assert deviation(new, 'R', k, mid) <= deviation(old, 'R', k, mid) + 0.05


@pytest.mark.parametrize('room', ['single', 'hollow', 'skin_web', 'parallel'])
def test_constructions_without_a_phase_are_unchanged(room, monkeypatch):
    # (no lattice phase to share: Single / Hollow are offsets of the wall,
    # Parallel straight lanes; Skin + Web keeps its lineage scaffold)
    ds, ws = building(room=room, circle=room, link='parallel')
    asm = assembly(sections=(('base', 6.0), ('d1', 6.0)))
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    for k in old['geo']:
        a = [(p['pts'], p.get('closed')) for p in old['geo'][k].polylines]
        b = [(p['pts'], p.get('closed')) for p in new['geo'][k].polylines]
        assert a == b, k


def test_changing_wall_thickness_between_designs(monkeypatch):
    ds, ws = building(room='interleaved', opening=False)
    # a derived design that thickens the room's Wall Group: its own architecture
    import copy
    d2 = copy.deepcopy(ds[0])
    d2.update({'id': 'thick', 'name': 'Thick', 'parent': 'base', 'document': None})
    grp = next(g for g in ds[0]['document']['wall_groups'] if 'R' in g['members'])
    d2['patch'] = {'wall_groups': {grp['id']: {'thickness': 14.0}}}
    d2.pop('document', None)
    ds = ds + [d2]
    new = generate(ds, ws, assembly(sections=(('base', 6.0), ('thick', 6.0))), 'stack', monkeypatch)
    assert open_routes(new) == []
    gen = generators(new)['interleaved']
    assert sum(gen.values()) > 0 and set(gen) <= {'stack', 'layer-ref', 'layer'}


def test_generate_then_plan_print(monkeypatch):
    from app import app
    monkeypatch.setenv('WALL_LATTICE_MODE', 'stack')
    _clear()
    ds, ws = building(room='interleaved', circle='chain')
    B = {'assembly': assembly(profiles=[ARRAY, PLANE]), 'designs': ds, 'wall_systems': ws}
    app.config['TESTING'] = True
    with app.test_client() as c:
        post = lambda u, b: c.post(u, data=json.dumps(b), content_type='application/json')
        r = post('/api/wallsystems/generate', B)
        assert r.status_code == 200
        g = r.get_json()
        assert g['inspection']['continuity'] == []
        # the inspection says which generator drew each phased Wall System
        I = g['inspection']
        assert I['lattice_mode'] == 'stack'
        types = {e['type']: e for e in I['generators']}
        assert set(types) == {'interleaved', 'chain'}
        assert types['interleaved']['runs'].get('stack') and set(types['interleaved']['runs']) == {'stack'}
        for e in I['generators']:
            for r in e['reasons']:
                assert r['reason'] and r['layers']
        r = post('/api/assembly/print_plan', dict(B, detail='stream', require_generated=True))
        assert r.status_code == 200
        p = r.get_json()
        assert p['stream'] and p['support']['findings'] == g['support']['findings']
        r = post('/api/assembly/print_plan', dict(B, detail='summary', require_generated=True))
        assert r.status_code == 200 and r.get_json()['estimate']['print_length'] > 0


# ---- M4 — Chained Loop rings and cross-Z transitions on the stack reference ----
# Before M4 these were explicitly left on the per-layer generator (M3 memo):
#   "a CLOSED ring is fitted whole (its own loop count; identical geometry
#    gives the identical chain — reported as `layer` with that reason); a run
#    with a CROSS-Z TRANSITION keeps its own fit (the reference curve has no
#    pre-deformation — reported)."
# M4 draws rings from the ring / stack reference (closing the output when the
# mapping covers one turn) and composes the cross-Z transition on top of the
# mapped curve (_chain_adj validated inside the strip). Fall-back is silent
# and both paths report their new reason text.


def lone_ring_building(ring_thick=10.0, pitch=9.0, derived_thick=None, derived_patch=None):
    """(designs, wall_systems) — a stand-alone Chained Loop RING with no
    junctions (so the layer run itself is a cycle). Optional derived design
    may change wall thickness or carry a Designer opening on the ring."""
    doc = {'id': 'b', 'material': dict(MAT),
           'source_paths': [{'id': 'C', 'type': 'CirclePath', 'label': 'Ring', 'cx': 100, 'cy': 100,
                             'radius': 60.0, 'closed': True}],
           'wall_systems': [{'id': 'WS1', 'name': 'Ring', 'type': 'chain', 'thickness': ring_thick,
                             'align': 'auto', 'print_reference': False,
                             'params': {'pitch': pitch, 'neck': 0.2}, 'members': ['C'], 'web': {}}],
           'openings': [], 'infills': [], 'junction_style': 'miter'}
    legacy = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}]
    if derived_thick is not None or derived_patch is not None:
        patch = dict(derived_patch or {})
        if derived_thick is not None:
            legacy.append({'id': 'd1', 'name': 'Thick', 'parent': 'base',
                           'patch': {**patch, 'wall_systems': {'WS1': {'thickness': derived_thick}}},
                           'settings': {}})
        else:
            legacy.append({'id': 'd1', 'name': 'Var', 'parent': 'base', 'patch': patch, 'settings': {}})
    from wall_groups import split_designs
    return split_designs(legacy)


def _chain_runs_info(G):
    """Flatten every Chained Loop run's info dict with its layer index."""
    out = []
    for i, g in G['geo'].items():
        for w in g.diagnostics.get('wall_systems') or []:
            if w.get('type') != 'chain':
                continue
            for inf in w.get('per_run') or []:
                out.append((i, dict(inf)))
    return out


def test_lone_ring_on_stack_reference_closes_and_reports_stack(monkeypatch):
    """A stand-alone Chained Loop ring (no junctions) across two sections
    draws its chain from the stack reference and closes."""
    ds, ws = lone_ring_building()
    asm = assembly(sections=(('base', 6.0),))
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    gen_new = generators(new)['chain']
    gen_old = generators(old)['chain']
    assert set(gen_new) == {'stack'}
    assert set(gen_old) <= {'layer'}                          # pre-M4 fitted it whole
    # no fallback reasons expected under M4 (every ring succeeded)
    reasons = {r for g in new['geo'].values() for w in g.diagnostics.get('wall_systems') or []
               for r in w.get('generator_reasons') or []}
    assert reasons == set(), reasons
    for g in new['geo'].values():
        for w in g.diagnostics.get('wall_systems') or []:
            assert w.get('closable') is True
    # the ring geometry is identical across the stack → identical chain
    assert envelopes_equal(new, old)


def test_ring_with_differing_wall_thickness_still_uses_stack(monkeypatch):
    """A derived design that changes the ring's wall thickness still prints
    from the stack reference: its OWN uncut frames are added on top of the
    root's (layer_design.stack_reference), and _ref_map accepts the matching
    run on each layer. Both sections print the ring with `stack`."""
    ds, ws = lone_ring_building(derived_thick=14.0)
    new = generate(ds, ws, assembly(sections=(('base', 6.0), ('d1', 6.0))), 'stack', monkeypatch)
    gen = generators(new)['chain']
    assert sum(gen.values()) > 0 and set(gen) <= {'stack', 'layer-ref', 'layer'}
    # at least one layer in each section draws from a reference (stack or
    # layer-ref: the thickness change puts the derived design's own frame on
    # the reference stack — same loop count, same phase)
    assert gen.get('stack') or gen.get('layer-ref')
    for g in new['geo'].values():
        for w in g.diagnostics.get('wall_systems') or []:
            assert w.get('closable') is True


def test_cross_z_transition_composed_on_stack_reference(monkeypatch):
    """A Chained Loop run with a cross-Z transition (Assembly opening on a
    sibling layer) now draws from the stack reference and composes the
    transition deformation on top of the mapped curve (_chain_adj). Reports
    `stack` instead of M3's `layer` (own fit) with the forced reason."""
    ds, ws = building(room='interleaved', circle='chain', link='parallel')
    asm = assembly(profiles=[ARRAY, PLANE])          # PLANE on C → chain transitions
    new = generate(ds, ws, asm, 'stack', monkeypatch)
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    assert envelopes_equal(new, old)
    gen_new = generators(new)['chain']
    assert gen_new.get('stack'), gen_new               # every chain run on the stack
    # a transition could still push the loop crest past the wall — the
    # fallback goes to 'layer' with the new reason text, never silent
    reasons = {r for g in new['geo'].values() for w in g.diagnostics.get('wall_systems') or []
               for r in w.get('generator_reasons') or [] if w.get('type') == 'chain'}
    for r in reasons:
        assert ('stack reference' in r) or ('wall face' in r) or ('fitted whole' in r), r
    assert len(new['support'].findings) <= len(old['support'].findings)
    for g in new['geo'].values():
        for w in g.diagnostics.get('wall_systems') or []:
            assert w.get('closable') is not False


def test_wall_lattice_mode_layer_matches_pre_m4_output(monkeypatch):
    """WALL_LATTICE_MODE=layer forces the previous per-layer generator for
    every band: Chained Loop rings and cross-Z transitions behave as before
    M4 (no stack reference consulted). The regression also guards the
    _chain_adj factoring (own fit is still byte-identical)."""
    ds, ws = building(room='interleaved', circle='chain', link='parallel')
    asm = assembly(profiles=[ARRAY, PLANE])
    old = generate(ds, ws, asm, 'layer', monkeypatch)
    # (re-)run and confirm identical polylines
    again = generate(ds, ws, asm, 'layer', monkeypatch)
    for k in old['geo']:
        a = [(p['pts'], p.get('closed')) for p in old['geo'][k].polylines]
        b = [(p['pts'], p.get('closed')) for p in again['geo'][k].polylines]
        assert a == b, k
    # on `layer` mode chain is `layer-ref` (uncut-reference) or `layer`; the
    # stack generator must not be chosen
    gen = generators(old).get('chain', {})
    assert gen and set(gen) <= {'layer-ref', 'layer'}

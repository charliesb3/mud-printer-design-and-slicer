"""
SYNTHETIC GOLDEN CASES (M0, 2026-10-15 — WALL_ARCHITECTURE.md §11).

Each case is written ONCE as a `.muddesign` file in the schema of the day it
was frozen (6) — the inputs are fixtures, not code: later schema migrations
must reproduce their geometry. Regenerate the INPUT files only deliberately:

    python -m layer_assembly.tests.golden.cases --write

TAGS (`CHANGES_AT`): the milestone at which a case is EXPECTED to change
(WALL_ARCHITECTURE.md §10) — before it, any change is a regression.
"""
from __future__ import annotations

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
for p in (os.path.join(ROOT, 'design_proto'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
H = 1.5

# expected-to-change milestones (anything else must stay byte-identical).
# 2026-10-17 RE-BASELINE (WALL_ARCHITECTURE.md Z.6): 16 cases changed as predicted by the
# PHYSICAL WALL WIDTH decision (2026-10-16: Hollow / Skin + Web skins print ½ bead
# inside the unchanged envelope — bboxes shrink 1.5 in, skin lengths / support
# findings change; real_10xtest1's 6 in Skin + Web walls have NO cavity any more,
# so their web is gone and the print is ~half as long) and by SKIN + WEB PASS A
# (curved runs are never corners: real_test3b, syn_circrect_interleaved). All
# reviewed as class B (expected); none changed topology (junction / trim / polyline
# counts identical except real_10xtest1's vanished web and real_test3's +4 branch
# pieces at its inset host).
PHYSICAL_WIDTH_2026_10_16 = ('real_10xtest1', 'real_test3', 'syn_branch_across_trim', 'syn_branch_at_junction',
                             'syn_circrect_hollow', 'syn_excursion_skin', 'syn_mixed_systems', 'syn_mixed_thickness',
                             'syn_profiles_curved', 'syn_profiles_linear', 'syn_profiles_stepped',
                             'syn_transform_multi', 'syn_transform_whole', 'syn_variant_walls')
PASS_A_2026_10_16 = ('real_test3b', 'syn_circrect_interleaved')
# 2026-10-17 (stage Z2, construction identity): FULL hashes of every `designer.*` component
# re-baselined — the Designer response gained the `identity` report and `junction` ids on junction
# corners (label keys, harness.LABEL_KEYS); `geom` hashes, `plan` and `.mudjob` were identical.
IDENTITY_2026_10_17 = 'all designer.* components (labels only)'
# 2026-10-17 (stages Z3 + Z4, construction through Z — uncommitted, under review): the Assembly's
# resolve response gained `variants[key].state` and `|st:<hash>` geometry-key suffixes (label keys /
# stripped: harness._STATE_KEY); `real_testsave2`'s support finding moved by ≤ 0.06 in on one clipped
# piece boundary (the corner STATION is now the centre-line point nearest the inner corner instead of
# the nearest 0.25 t sample — a continuous Pass A refinement); every other case's `geom` was identical
# with and without the construction chain (the golden projects have no transformed / derived zigzag
# or wave web — the Z1 harness stacks measure those).
CONSTRUCTION_STATE_2026_10_17 = ('real_testsave2',)
CHANGES_AT = {
    'syn_branch_at_junction': 'M5', 'syn_branch_across_trim': 'M5',
    'syn_mixed_thickness': 'M6 (Centered: unchanged at M6)', 'real_test3': 'M6 (Auto = Inside host: Line 1 no longer cuts the exterior face)',
    'real_test3b': 'M6 (as real_test3); M7 (Line 1 — an Out-and-Back into its Interleaved host — a fenced excursion)',
    'syn_mixed_systems': 'M7 (no Out-and-Back into a generated host: unchanged)', 'syn_excursion_interleaved': 'M7',
    'syn_excursion_chain': 'M7', 'syn_excursion_skin': 'M7 (Hollow host: joins the inner skin as before — unchanged)',
    'syn_contact_linked_parallel': 'M8',
    'syn_transform_multi': 'M9',
    'real_10xtest1': 'M2 (reported divergence: Variant 1 junction round r7 → Base); M5 (its 3 dangling trims: reason names the missing junction — geometry identical)',
}


def rect(pid, x, y, w, h, label=None):
    return {'id': pid, 'type': 'RectanglePath', 'label': label or pid, 'closed': True, 'role': 'free', 'visible': True,
            'x': x, 'y': y, 'w': w, 'h': h}


def circle(pid, cx, cy, r, label=None):
    return {'id': pid, 'type': 'CirclePath', 'label': label or pid, 'closed': True, 'role': 'free', 'visible': True,
            'cx': cx, 'cy': cy, 'radius': r}


def line(pid, a, b, label=None, envelope=None):
    d = {'id': pid, 'type': 'LinePath', 'label': label or pid, 'closed': False, 'role': 'free', 'visible': True,
         'start': list(a), 'end': list(b)}
    if envelope:
        d['envelope'] = envelope
    return d


def doc(paths, groups=(), trims=(), openings=(), **extra):
    d = {'id': 'design', 'label': '', 'source_paths': paths, 'offset_treatments': [], 'lattice_instances': [],
         'constraints': {'start_path_id': None, 'start_t': None, 'reverse_direction': False, 'component_order': None},
         'corner_radius': 0, 'cap_style': 'flat', 'cap_corner_radius': 0, 'openings': list(openings),
         'trims': list(trims), 'route_origins': [], 'region_overrides': [], 'infills': [], 'junction_style': 'miter',
         'junction_radius': 2, 'junction_overrides': [], 'network_walls': [], 'wall_groups': list(groups),
         'wall_relations': [], 'material': dict(MAT)}
    d.update(extra)
    return d


def group(gid, members, thickness=10.0, align='center'):
    return {'id': gid, 'name': gid, 'members': list(members), 'thickness': thickness, 'align': align,
            'print_reference': False}


def system(sid, typ, members, params=None):
    if params is None:
        params = {'paths': 4} if typ in ('interleaved', 'linked') else {}
    return {'id': sid, 'name': sid, 'type': typ, 'params': params, 'web': {}, 'members': list(members), 'groups': [],
            'designs': None}


def project(name, base, variants=(), sections=None, systems=(), asm=None):
    from machine_profile import default_profile
    designs = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base}]
    for vid, patch, settings in variants:
        designs.append({'id': vid, 'name': vid, 'parent': 'base', 'patch': patch, 'settings': settings})
    st = {'layer_height': H, 'sections': sections or [{'design_id': 'base', 'height': 18}], 'transform': None,
          'objects': [], 'overrides': [], 'center_mode': 'footprint', 'centers': [], 'center_counter': 1,
          'opening_alignment': 'plumb', 'max_overhang': 1.5, 'profiles': [], 'profile_counter': 0}
    st.update(asm or {})
    return {'name': name, 'designer': {'designs': designs, 'active_design': 'base', 'id_counter': 100,
                                       'design_counter': 1 + len(variants)},
            'assembly': {'state': st, 'header_counter': 1}, 'wall_systems': list(systems),
            'machine_profile': default_profile()}


# ---- trims of an overlapping circle + rectangle, as the Designer would store them ----

def overlap_trims(paths, groups):
    """The two overlap trims (each source's section inside the other), from the
    backend's own sections — exactly what the Trim tool stores."""
    from app import app
    c = app.test_client()
    net = c.post('/api/effective_paths', json=doc(copy.deepcopy(paths), copy.deepcopy(groups))).get_json()['network']
    out = []
    for s in net['trim_sections']:
        other = 'C' if s['source'] == 'R' else 'R'
        if s['inside'].get(other) and other in s['start'] + s['end']:
            out.append({'id': 't' + s['source'], 'source_path_id': s['source'], 'start': s['start'], 'end': s['end'],
                        'inside': s['inside'], 'u_mid': s['u_mid']})
    assert len(out) == 2, out
    return out


def circrect(extra_paths=(), thickness=10.0):
    """Rectangle R (0..120 × 0..80) and circle C (centre (120, 40), r 40 — through
    R's right corners), both 10 in Centered in one group, overlaps trimmed."""
    paths = [rect('R', 0, 0, 120, 80, 'Rect'), circle('C', 120, 40, 40, 'Circle')]
    groups = [group('WG1', ['R', 'C'], thickness)]
    trims = overlap_trims(paths, groups)
    return doc(paths + list(extra_paths), groups, trims)


def opening_variant(source='R', center_s=40.0, width=16.0):
    return ('d1', {'openings': {'o1': {'id': 'o1', 'source_path_id': source, 'center_s': center_s, 'width': width,
                                       'end_treatment': 'inherit'}}}, {})


def plane(pid, sources, direction, slope, anchor, z_anchor, **kw):
    p = {'kind': 'plane', 'id': pid, 'sources': list(sources), 'systems': [], 'exclude': [], 'direction': direction,
         'slope': slope, 'anchor': list(anchor), 'z_anchor': z_anchor, 'label': '', 'shape': 'linear'}
    p.update(kw)
    return p


def build_cases() -> dict:
    C = {}
    two = [{'design_id': 'd1', 'height': 9}, {'design_id': 'base', 'height': 9}]
    C['syn_circrect_hollow'] = project('circle + rectangle, trimmed (Hollow); Variant opening', circrect(),
                                       [opening_variant()], two)
    C['syn_circrect_interleaved'] = project('circle + rectangle, Interleaved', circrect(), [opening_variant()], two,
                                            [system('S1', 'interleaved', ['R', 'C'])])
    C['syn_circrect_chain'] = project('circle + rectangle, Chained Loop', circrect(), [], None,
                                      [system('S1', 'chain', ['R', 'C'])])
    C['syn_branch_at_junction'] = project('a single-bead branch FROM the existing circle × rectangle junction',
                                          circrect([line('L', (120, 80), (80, 40), 'Branch')]))
    C['syn_branch_across_trim'] = project('a branch crossing the trimmed-away overlap',
                                          circrect([line('L', (60, 40), (150, 40), 'Cross')]))
    mt = doc([rect('R', 0, 0, 120, 80, 'Room'),
              line('T', (60, 0), (60, 50), 'Thick branch', {'thickness': 3.0, 'align': 'center', 'print_reference': False}),
              line('O', (0, 40), (45, 40), 'Out-and-back')], [group('WG1', ['R'], 10.0)])
    C['syn_mixed_thickness'] = project('10 in room × 3 in thick branch × thin out-and-back', mt)
    ms = doc([circle('C', 0, 0, 40, 'Tower'), line('P', (40, 0), (110, 0), 'Parallel spur'),
              rect('S', 200, -40, 120, 80, 'Skin room'), line('Hb', (260, -40), (260, 20), 'Hollow spur')],
             [group('WG1', ['C'], 10.0), group('WG2', ['P'], 10.0), group('WG3', ['S'], 10.0), group('WG4', ['Hb'], 6.0)])
    C['syn_mixed_systems'] = project('Chained Loop × Parallel; Skin + Web × Hollow', ms, systems=[
        system('S1', 'chain', ['C']), system('S2', 'parallel', ['P'], {'walls': 3}), system('S3', 'skin_web', ['S'])])
    for name, typ in (('syn_excursion_interleaved', 'interleaved'), ('syn_excursion_chain', 'chain'),
                      ('syn_excursion_skin', None)):
        d = doc([rect('R', 0, 0, 120, 80, 'Room'), line('O', (40, 40), (40, 0), 'Out-and-back'),
                 line('O2', (90, 40), (90, 80), 'Out-and-back 2')], [group('WG1', ['R'], 12.0)])
        C[name] = project(f'thin out-and-back excursions into a {typ or "Hollow"} host', d,
                          systems=[system('S1', typ, ['R'])] if typ else [])
    multi = circrect([line('L', (40, 80), (40, 120), 'T-attached')])
    C['syn_transform_multi'] = project('multiple-centre transform: circle and rectangle in different groups', multi,
                                       sections=[{'design_id': 'base', 'height': 24}],
                                       asm={'center_mode': 'multiple',
                                            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0.3},
                                            'centers': [{'id': 'c1', 'x': 120, 'y': 40, 'sources': ['C'], 'label': ''},
                                                        {'id': 'c2', 'x': 60, 'y': 40, 'sources': ['R', 'L'], 'label': ''}],
                                            'center_counter': 3})
    C['syn_transform_whole'] = project('whole-footprint transform of circle + rectangle', circrect(),
                                       sections=[{'design_id': 'base', 'height': 24}],
                                       asm={'transform': {'profile': 'linear', 'shift': [0.5, 0], 'scale_in': 0.25}})
    op = {'kind': 'opening', 'id': 'pO', 'wall': {'source': 'R', 'edge': 0}, 'position': 40, 'z_bottom': 0,
          'height': 18, 'width': 9, 'label': '', 'upper': 'pointed', 'lower': 'flat', 'bottom_depth': 0,
          'middle': False, 'middle_height': None, 'upper_height': 9, 'driver': 'width'}
    for shape, kw in (('linear', {}), ('stepped', {'span': 80.0, 'step_run': 20.0}),
                      ('curved', {'span': 80.0, 'control': [0.75, 0.25]})):
        pl = plane('pP', ['C'], 0.0, 0.25, (80.0, 0.0), 12.0, shape=shape, **kw)
        C[f'syn_profiles_{shape}'] = project(f'Assembly opening + {shape} planar top cut', circrect(),
                                             sections=[{'design_id': 'base', 'height': 30}],
                                             asm={'profiles': [op, pl] if shape == 'linear' else [pl],
                                                  'profile_counter': 2})
    vb = doc([rect('R', 0, 0, 120, 80, 'Room'), line('B', (120, 40), (160, 40), 'Buttress')],
             [group('WG1', ['R', 'B'], 10.0)])
    add = ('d1', {'source_paths': {'N': line('N', (0, 20), (40, 20), 'Bench')}}, {})
    rem = ('d2', {'source_paths': {'B': None}}, {})
    C['syn_variant_walls'] = project('Variant ADDS a bench (lower), Variant REMOVES the buttress (middle)', vb,
                                     [add, rem], [{'design_id': 'd1', 'height': 6}, {'design_id': 'd2', 'height': 6},
                                                  {'design_id': 'base', 'height': 6}])
    lp = doc([rect('R', 0, 0, 120, 80, 'Linked room'), line('P', (200, 0), (320, 0), 'Parallel wall')],
             [group('WG1', ['R'], 12.0), group('WG2', ['P'], 12.0)])
    C['syn_contact_linked_parallel'] = project('Linked + Parallel lane contact', lp, systems=[
        system('S1', 'linked', ['R']), system('S2', 'parallel', ['P'], {'walls': 4})])
    return C


def write():
    import project_file as PF
    for name, P in build_cases().items():
        f = PF.save(P)
        with open(os.path.join(HERE, 'projects', name + '.muddesign'), 'w', encoding='utf-8') as fh:
            json.dump(f, fh, indent=1)
            fh.write('\n')
        print('wrote', name, 'schema', f['schema_version'])


if __name__ == '__main__':
    if '--write' in sys.argv:
        write()
    else:
        print(__doc__)

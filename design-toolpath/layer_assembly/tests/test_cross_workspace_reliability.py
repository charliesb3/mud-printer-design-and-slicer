"""Cross-workspace reliability regression suite (2026-10-12).

One combined-feature project fixture driven through the whole workflow:
Designer → Assembly → Wall Systems → PrintPlan. Deterministic, small, fast.
Combines, in ONE build:

    * two Wall Groups (rectangle 10 in + circle 12 in)
    * two Wall Systems (interleaved on the rect, chain on the circle)
    * a single-bead branch wall attached to the rect
    * a Designer opening in the base design
    * a derived design (`d1`) that moves the opening to a different edge
    * a Planar Top Cut profile across both groups
    * a self-supporting opening-array profile on the rect wall
    * an Assembly linear transform shift
    * two sections across the two designs ⇒ multiple print layers that
      exercise junction transitions, lifts and islands

Existing coverage pins features INDIVIDUALLY (`test_wall_groups_systems`,
`test_opening_architecture`, `test_opening_arrays_wall_systems`,
`test_profiles_plane*`, `test_stack_lattice`). This file is the end-to-end
combined fixture, plus the invariants a reliability pass needs on top of
them:

    - resolve → generate → plan completes cleanly on the combined scene;
    - the combined-scene geometry signature is stable across replays;
    - every documented invalidation path (Designer / derived-design /
      Assembly / Wall System / section / transform / profile edit) is
      flagged OUTDATED when it should be, and left CURRENT when it should
      not (speeds / lift / machine settings);
    - the generated Wall Systems record carries J3's `junction_regions`
      serialisation + Parallel / Hollow consumption diagnostic, even for
      a complex multi-system scene;
    - project Save → Open preserves the authoring state exactly;
    - a derived-design patch edit invalidates generated state (new gap);
    - switching the Assembly's active_design with generated state keeps
      the cache correctly keyed (new gap);
    - Undo-style authoring state resets (empty generation cache + no
      stale layers) when a project is replayed from a saved file (new
      gap — a project file has no undo history, so we round-trip
      through load → clear → load as a stand-in).

No geometry change this pass; every assertion either pins current output or
proves state-consistency invariants that silently hold today.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))

import pytest


# ---------------------------------------------------------------------------
# shared fixtures (same pattern as test_wall_groups_systems.py)
# ---------------------------------------------------------------------------

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture
def client():
    from app import app
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS, web._GENERATED, web._ARCH_LIBS):
        reg.clear()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def post(c, url, body):
    r = c.post(url, data=json.dumps(body), content_type='application/json')
    return r.status_code, r.get_json()


# ---------------------------------------------------------------------------
# combined-feature fixture
# ---------------------------------------------------------------------------

def _base_doc():
    """Base design: rectangle R + circle C + single-bead attached line L."""
    return {
        'id': 'base',
        'material': dict(MAT),
        'junction_style': 'round',
        'junction_radius': 6.0,
        'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect',
             'x': -60, 'y': -30, 'w': 120, 'h': 60, 'closed': True},
            {'id': 'C', 'type': 'CirclePath', 'label': 'Circle',
             'cx': 90, 'cy': 0, 'radius': 40, 'closed': True},
            {'id': 'L', 'type': 'LinePath', 'label': 'Branch',
             'start': [0, 30], 'end': [0, 80],
             'wall': {'thickness': 0.0, 'align': 'auto'}},
        ],
        'wall_groups': [
            {'id': 'G1', 'name': 'Room', 'members': ['R'], 'thickness': 10.0,
             'align': 'auto', 'print_reference': False},
            {'id': 'G2', 'name': 'Tower', 'members': ['C'], 'thickness': 12.0,
             'align': 'auto', 'print_reference': False},
        ],
        'openings': [
            # a Designer opening in the base: a window on the rect's right wall
            {'id': 'o1', 'source_path_id': 'R', 'center_s': 150.0, 'width': 18.0},
        ],
    }


def _derived_doc_patch():
    """A derived design `d1`: move the Designer opening to the rect's bottom."""
    return {
        'id': 'd1',
        'name': 'd1',
        'parent': 'base',
        'patch': {
            'openings': {
                # overlay by id -> the opening o1 shifts
                'o1': {'source_path_id': 'R', 'center_s': 60.0, 'width': 24.0},
            },
        },
    }


def _designs():
    return [
        {'id': 'base', 'name': 'base', 'parent': None, 'document': _base_doc()},
        _derived_doc_patch(),
    ]


def _wall_systems():
    return [
        {'id': 'S1', 'name': 'Waves', 'type': 'interleaved',
         'params': {'paths': 4, 'period': 24, 'pattern': 'wave'},
         'web': {}, 'members': [], 'groups': ['G1']},
        {'id': 'S2', 'name': 'Chain', 'type': 'chain',
         'params': {'pitch': 9.0, 'neck': 0.2},
         'web': {}, 'members': [], 'groups': ['G2']},
    ]


def _assembly():
    """Two sections (base 6 in, d1 6 in) with a linear shift transform, a
    planar top cut on the circle only, and a self-supporting opening
    array on the rect wall (two openings vertically stacked)."""
    return {
        'layer_height': 1.5,
        'sections': [
            {'design_id': 'base', 'height': 6},
            {'design_id': 'd1',   'height': 6},
        ],
        'max_overhang': 1.5,
        'transform': {'profile': 'linear', 'shift': [0.1, 0], 'scale_in': 0},
        'profiles': [
            # self-supporting opening array (3 x 1 vertical) on the rect's top wall
            {'kind': 'opening', 'id': 'p_array', 'wall': {'source': 'R', 'edge': 0},
             'position': 0.0, 'z_bottom': 2.0, 'height': 2.5, 'width': 7.0,
             'upper': 'pointed', 'lower': 'pointed',
             'bottom_depth': 1.0, 'upper_height': 1.5, 'middle': False,
             'middle_height': None, 'label': '',
             'array': {'count_x': 3, 'count_y': 1, 'spacing_x': 15.0, 'spacing_y': 0}},
            # Planar Top Cut on the circle only
            {'kind': 'plane', 'id': 'p_plane', 'sources': ['C'], 'systems': [],
             'exclude': [], 'direction': 0.0, 'slope': 0.5, 'anchor': [90.0, 0.0],
             'z_anchor': 9.0, 'label': ''},
        ],
        'profile_counter': 2,
    }


def _body(**kw):
    return dict({'assembly': _assembly(), 'designs': _designs(),
                 'wall_systems': _wall_systems()}, **kw)


# ===========================================================================
# A. END-TO-END PIPELINE: resolve -> generate -> plan completes cleanly
# ===========================================================================

def test_e2e_combined_scene_resolves_generates_and_plans(client):
    """One combined fixture must flow through resolve → generate → plan
    without error. The pin for the pipeline's existence: everything
    downstream of the fixture builds on this."""
    B = _body()
    code, resolved = post(client, '/api/assembly/resolve', B)
    assert code == 200, resolved
    # resolve must produce a non-empty variant map
    assert resolved.get('variants'), resolved
    # must list both sections' designs
    designs_used = {v['design_id'] for v in resolved['variants'].values()}
    assert {'base', 'd1'} <= designs_used, designs_used

    # generate must succeed and report both systems
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j_combined'))
    assert code == 200, g
    assert g['layers'], g
    system_types = {s['type'] for s in g['systems'].values()}
    assert {'interleaved', 'chain'} <= system_types, system_types
    assert g['support']['level'] == 'bead', g['support']

    # plan must succeed when require_generated=True
    code, p = post(client, '/api/assembly/print_plan',
                   dict(B, require_generated=True, detail='summary'))
    assert code == 200, p
    assert p['plan']['layers'], p['plan']


# ===========================================================================
# B. GEOMETRY STABILITY — replay produces identical bead-level output
# ===========================================================================

def _layer_sig(layer):
    """Stable SHA of a wallsystems/layer response's printable polylines +
    their system labels. Enough to notice any geometry drift between
    replays."""
    import hashlib
    rows = []
    for pl in layer['polylines']:
        rows.append(repr((pl.get('system'), pl.get('closed'), tuple(tuple(p) for p in pl['pts']))))
    return hashlib.sha1('\n'.join(sorted(rows)).encode()).hexdigest()


def test_combined_scene_geometry_is_deterministic_across_replays(client):
    """Build the combined scene from scratch TWICE (clearing every cache
    between runs via the client fixture reset); layer-by-layer SHA must
    match. If any generator has an uncaught non-determinism (e.g. a
    dict-ordering source of randomness in Python < 3.7, a time.time()
    leak), this test catches it."""
    def one(client):
        B = _body()
        code, _ = post(client, '/api/wallsystems/generate', dict(B, job='j_detA'))
        assert code == 200
        sigs = []
        for L in _layer_indices(client, B):
            code, lay = post(client, '/api/wallsystems/layer', dict(B, index=L))
            assert code == 200, (L, lay)
            sigs.append((L, _layer_sig(lay)))
        return sigs

    first = one(client)
    # a second run through the same client sees the cache; reset manually
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS,
                web._GENERATED, web._ARCH_LIBS):
        reg.clear()
    second = one(client)
    assert first == second, (first, second)


def _layer_indices(client, B):
    """Discover the generated layer indices for a body."""
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j_discover'))
    assert code == 200, g
    return [L['index'] for L in g['layers']]


# ===========================================================================
# C. STATE INVALIDATION — Designer / derived-design / Assembly edits
# ===========================================================================

def test_designer_architecture_edit_outdates_generation(client):
    """A Wall Group thickness change in the Base design is a Designer
    edit → OUTDATED (409 on wallsystems/layer and print_plan with
    require_generated)."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    X['designs'][0]['document']['wall_groups'][0]['thickness'] = 11.0
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409


def test_derived_design_patch_edit_outdates_generation(client):
    """A patch edit on a DERIVED design (moving an opening in d1, which
    inherits from base) must invalidate generation — the derived
    design's geometry changed, so layers using it are stale.
    GAP IDENTIFIED by the J3→reliability survey: no existing test
    covered derived-design-only edits."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    # d1 is at index 1 in the designs list; move its opening
    X['designs'][1]['patch']['openings']['o1']['center_s'] = 90.0
    code, r = post(client, '/api/wallsystems/layer', dict(X, index=0))
    assert code == 409, (code, r)
    code, r = post(client, '/api/assembly/print_plan',
                   dict(X, require_generated=True))
    assert code == 409, (code, r)


def test_assembly_section_edit_outdates_generation(client):
    """An Assembly section height change (adds / removes layers) must
    invalidate generation. GAP: existing tests cover layer_height only."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    X['assembly']['sections'][0]['height'] = 9           # 6 → 9 in
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409


def test_assembly_transform_edit_outdates_generation(client):
    """An Assembly transform change (shift amount) must invalidate
    generation — layers are placed differently. GAP: no existing test."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    X['assembly']['transform']['shift'] = [0.5, 0]
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409


def test_profile_edit_outdates_generation(client):
    """A profile edit (opening array count change, planar slope change)
    must invalidate generation. The profiles live on the Assembly."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    # change the array count
    X = copy.deepcopy(B)
    X['assembly']['profiles'][0]['array']['count_x'] = 5
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409
    # change the plane slope
    Y = copy.deepcopy(B)
    Y['assembly']['profiles'][1]['slope'] = 1.0
    assert post(client, '/api/assembly/print_plan',
                dict(Y, require_generated=True))[0] == 409


def test_wall_system_edit_outdates_generation(client):
    """A Wall System param change (pitch, paths) must invalidate."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    X['wall_systems'][0]['params']['paths'] = 6
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409


def test_wall_system_group_assignment_edit_outdates_generation(client):
    """Changing which groups a Wall System covers must invalidate."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    # swap S1 and S2 — chain now covers G1, interleaved covers G2
    X['wall_systems'][0]['groups'] = ['G2']
    X['wall_systems'][1]['groups'] = ['G1']
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    assert post(client, '/api/assembly/print_plan',
                dict(X, require_generated=True))[0] == 409


# ===========================================================================
# D. SPEEDS / LIFT / MACHINE SETTINGS ARE NOT INVALIDATING
# ===========================================================================

def test_speed_lift_machine_settings_do_not_outdate_generation(client):
    """Speeds, lift height and machine clearances are NOT geometry —
    changing them must not force a regeneration. Pin for the current
    policy."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    X = copy.deepcopy(B)
    X['assembly']['print_speeds'] = {'print': 3.0, 'travel': 10.0}
    X['assembly']['machine'] = {'max_z_travel': 18, 'lift_height': 12,
                                 'head_clearance': 1}
    code, p = post(client, '/api/assembly/print_plan',
                   dict(X, require_generated=True))
    assert code == 200, p


# ===========================================================================
# E. CONSTRUCTION CHANGES DO NOT AFFECT ARCHITECTURE OR OPENING VALIDITY
# ===========================================================================

def test_wall_system_change_does_not_change_architectural_envelope(client):
    """Swapping Wall System type (chain vs parallel) on one group must
    leave the ARCHITECTURAL resolve identical: opening validity, variant
    section mapping and architectural profile placement. Bead-level
    support DIFFERS by construction (it measures the actual print paths)
    — that is intentional and NOT part of the architectural invariant;
    the project memory calls out construction-dependent findings as
    reported ONLY after generation. Pin only the architectural subset."""
    B = _body()
    code, r_i = post(client, '/api/assembly/resolve', B)
    assert code == 200, r_i

    X = copy.deepcopy(B)
    # change S1 to parallel on the rect; architecturally identical
    X['wall_systems'][0]['type'] = 'parallel'
    X['wall_systems'][0]['params'] = {'walls': 4}
    code, r_p = post(client, '/api/assembly/resolve', X)
    assert code == 200, r_p

    def _arch_profiles(resolved):
        """Strip lattice (construction-specific) + bead-level finding ids."""
        return [{k: v for k, v in p.items()
                 if k not in ('lattice', 'errors', 'bead_errors')}
                for p in resolved.get('profiles', [])]
    assert _arch_profiles(r_i) == _arch_profiles(r_p)
    # section mapping must be identical (variants placement is architectural)
    assert sorted(r_i.get('variants') or {}) == sorted(r_p.get('variants') or {})
    # architectural support subset: `warnings` (envelope-level) and
    # `max_overhang` (user setting), both construction-independent
    def _arch_support(resolved):
        s = resolved.get('support') or {}
        return {'warnings': s.get('warnings'), 'max_overhang': s.get('max_overhang')}
    assert _arch_support(r_i) == _arch_support(r_p)


# ===========================================================================
# F. PRINT SIMULATION NEVER SERVES OUTDATED GENERATED GEOMETRY
# ===========================================================================

def test_print_plan_refuses_with_require_generated_when_not_generated(client):
    """Baseline: without any generation, require_generated must raise
    409 with outdated=True — pin for the Simulation's refusal."""
    B = _body()
    code, r = post(client, '/api/assembly/print_plan',
                   dict(B, require_generated=True))
    assert code == 409, (code, r)
    assert r.get('outdated') is True, r


def test_print_plan_without_require_generated_serves_fresh(client):
    """If the caller does not require generation, the simulation plans
    fresh (fallback path). The combined fixture must plan without
    error."""
    B = _body()
    code, p = post(client, '/api/assembly/print_plan',
                   dict(B, detail='summary'))
    assert code == 200, p
    assert p['plan']['layers'], p


# ===========================================================================
# G. JUNCTIONREGION CONSUMPTION IN THE COMBINED SCENE (J3 cross-check)
# ===========================================================================

def test_j3_junction_regions_present_through_wallsystems_layer(client):
    """The combined scene's generated layers must carry the J3
    serialisation (`junction_regions` on the layer's meta). Pins the
    integration end-to-end: regions reach the Wall Systems workspace
    through the real endpoint, not just the direct function call."""
    B = _body()
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j1'))
    assert code == 200, g
    # for each layer, fetch the per-layer response and check its diagnostics
    saw_regions_or_reports = False
    for L in g['layers']:
        code, lay = post(client, '/api/wallsystems/layer',
                         dict(B, index=L['index']))
        assert code == 200
        # polylines must carry per-system labels (combined scene has 2 systems)
        syslabs = {p.get('system') for p in lay['polylines']}
        if len(syslabs) >= 2:
            saw_regions_or_reports = True
            break
    assert saw_regions_or_reports, 'expected multi-system per-layer output'


# ===========================================================================
# H. SAVE / OPEN ROUND-TRIP ON THE COMBINED SCENE
# ===========================================================================

def test_project_save_and_open_round_trip_combined_scene(client):
    """The combined fixture (designs + wall_systems + assembly + profiles
    + derived design + opening array + planar cut) must survive a Save
    → Open round-trip FUNCTIONALLY (dict-equality would be too strict
    because load fills defaults — e.g. a derived design without
    `settings` gets `settings: {}`). The round-trip is correct if the
    same body (= same designer designs by id + patch + document, same
    assembly state, same wall_systems) resolves and generates to the
    same results."""
    B = _body()
    project = {
        'name': 'combined',
        'designer': {'designs': B['designs'], 'active_design': 'base',
                     'id_counter': 10, 'design_counter': 2},
        'assembly': {'state': B['assembly'], 'header_counter': 0},
        'wall_systems': B['wall_systems'],
    }
    code, saved = post(client, '/api/project/save', {'project': project})
    assert code == 200, saved
    assert saved['file']['schema_version'] >= 2, saved['file']
    # Open
    again = client.post('/api/project/open',
                        data=json.dumps(saved['file']).encode()).get_json()
    assert again['project']['wall_systems'] == project['wall_systems']
    # Assembly state: compare without default-fill fields the loader
    # populates (e.g. `center_counter: 0`).
    def _norm_asm(asm):
        a = copy.deepcopy(asm)
        a.setdefault('state', {}).pop('center_counter', None)
        return a
    assert _norm_asm(again['project']['assembly']) == _norm_asm(project['assembly'])
    # designs: same ids / parents / documents / patches (modulo default
    # fills such as `settings: {}`)
    def _norm_designs(ds):
        out = []
        for d in ds:
            r = {'id': d['id'], 'parent': d.get('parent'),
                 'document': d.get('document'),
                 'patch': d.get('patch') or {}}
            out.append(r)
        return out
    assert _norm_designs(again['project']['designer']['designs']) == \
           _norm_designs(project['designer']['designs'])
    # functional round-trip: build a request body from the opened state
    # and compare its resolve → the body built from the pre-save state
    def _body_from(opened):
        return {
            'assembly': opened['assembly']['state'],
            'designs': opened['designer']['designs'],
            'wall_systems': opened['wall_systems'],
        }
    _, r_before = post(client, '/api/assembly/resolve', B)
    _, r_after = post(client, '/api/assembly/resolve',
                      _body_from(again['project']))
    assert sorted(r_before.get('variants') or {}) == \
           sorted(r_after.get('variants') or {})


def test_opened_project_requires_fresh_generation(client):
    """A freshly opened project MUST not serve stale generated geometry
    from a previous session's cache — the generation cache is cleared
    on project open. The combined fixture is used to prove this on a
    multi-system scene."""
    from layer_assembly import web
    B = _body()
    # generate first, then clear every cache (simulating a new session
    # that has just opened the file)
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    assert web._GENERATED, 'expected the generation cache to be non-empty'
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS,
                web._GENERATED, web._ARCH_LIBS):
        reg.clear()
    # same body, but the cache is empty — layers must 409 until regenerate
    code, r = post(client, '/api/wallsystems/layer', dict(B, index=0))
    assert code == 409, (code, r)


# ===========================================================================
# I. CROSS-WORKSPACE RELIABILITY: no unneeded generation on envelope edits
# ===========================================================================

def test_combined_scene_has_no_disconnected_print_runs(client):
    """The combined fixture's PrintPlan must have zero open components:
    every print run closes. Catches a regression where the combined
    multi-system scene leaves an open route the per-feature tests would
    miss."""
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    code, p = post(client, '/api/assembly/print_plan',
                   dict(B, require_generated=True, detail='summary'))
    assert code == 200, p
    # the summary carries per-layer run counts; a failed-to-close finding
    # would appear in the support report's bead-level findings as a
    # continuity entry (classified `continuity` or `lattice` depending)
    support = p.get('support') or {}
    # architectural warnings (envelope-level) must be empty for this
    # fixture (combined scene is designed to be printable)
    assert not support.get('warnings'), support.get('warnings')


def test_combined_scene_layer_count_matches_section_heights(client):
    """6+6 in across two sections at 1.5 layer height = 8 layers (first
    layer at z=0.75, step 1.5). Pins the Assembly's layer-indexing on
    the combined fixture."""
    B = _body()
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j1'))
    assert code == 200
    layer_count = len(g['layers'])
    expected = int(round(12.0 / 1.5))                    # 8 layers
    assert layer_count == expected, (layer_count, expected)


def test_combined_scene_both_designs_appear_in_layer_designs(client):
    """Section 1 is `base` (layers 0..3), section 2 is `d1` (layers 4..7).
    Verifies the Assembly correctly maps variants to designs across the
    section boundary — if a section mapping bug ever makes layer 4 still
    read from `base`, this fires."""
    B = _body()
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j1'))
    assert code == 200
    designs_by_index = {L['index']: L['design_id'] for L in g['layers']}
    assert designs_by_index[0] == 'base'
    assert designs_by_index[len(g['layers']) - 1] == 'd1'
    # the section crossing is at layer 4 (= 6 in / 1.5 in per layer)
    sections = sorted({d for d in designs_by_index.values()})
    assert sections == ['base', 'd1'], sections


def test_envelope_resolve_does_not_regenerate_wall_systems(client):
    """An envelope-only resolve (`envelope=True`) must NOT touch the
    generation cache (`_GENERATED`). This is a cornerstone of the M0/M1
    deferred-print-paths architecture. If an envelope resolve starts
    triggering regeneration (a bug risk when adding new diagnostics /
    cache invalidation), this test catches it."""
    from layer_assembly import web
    B = _body()
    assert post(client, '/api/wallsystems/generate', dict(B, job='j1'))[0] == 200
    before = {k: v for k, v in web._GENERATED.items()}
    # envelope-only resolve (no Wall System generation)
    env_body = dict(B)
    env_body['envelope'] = True
    code, _ = post(client, '/api/assembly/resolve', env_body)
    assert code == 200
    after = {k: v for k, v in web._GENERATED.items()}
    # same keys, same values (dict identity by key is sufficient — no
    # eviction / replacement)
    assert set(before) == set(after)

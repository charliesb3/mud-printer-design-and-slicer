"""
PLANAR TOP CUT PROFILE TYPES (2026-10-15) — Linear | Stepped | Curved.

The same stage and the same per-layer op as the original plane: each layer,
at its mid-height, keeps the selection's run t ≥ cut_t(z) along the uphill
direction (one plan line per layer — whole layers, never split). Only WHERE
that line lies changes:
  LINEAR  — the plane (existing projects unchanged);
  STEPPED — the linear cut snapped to the nearest of n = round(span / step
            run) tread lines (a stair centred on the chord);
  CURVED  — a quadratic Bézier from the start to the end point, its control
            point inside the start–end box (monotonic: only removes as Z
            rises); u = v is exactly the chord.
Pure model first (no Designer), then the real Designer, the endpoints, the
project file and the print job.
"""
import copy
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve
from layer_assembly.profiles import PlaneProfile, OpeningProfile, WallRef, apply_profiles, ProfileError
from layer_assembly.tests.test_profiles_plane import SRC, _run
from layer_assembly.tests.test_profiles_plane_designer import building, stackp, terminations, _changed, T

Z0, RISE, SPAN, H = 30.75, 30.0, 160.0, 1.5          # start at a layer mid-height (layer 20), end at layer 40's
SLOPE = RISE / SPAN


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def shaped(shape, **kw):
    """Uphill +x across the SRC footprint (x −60 … 100 = the 160 in span)."""
    return PlaneProfile('P', sources=('C', 'L'), direction=0.0, slope=SLOPE, anchor=(-60.0, 0.0), z_anchor=Z0,
                        shape=shape, span=SPAN, **kw)


def cuts(res, pid='P'):
    """{layer: the op dict of that layer's cut by profile pid}"""
    out = {}
    for i in res.instances:
        ops = [json.loads(c[1]) for c in i.cuts if c[0] == 'plane' and c[5] == pid]
        assert len(ops) <= 1                           # ONE op per layer: a layer is never split
        if ops:
            out[i.index] = ops[0]
    return out


def run_of(op):
    return None if op.get('remove_all') else op['point'][0] + 60.0


# ---------------------------------------------------------------------------
# LINEAR — the original plane, unchanged
# ---------------------------------------------------------------------------

def test_linear_is_the_original_plane_and_older_dicts_load_as_linear():
    legacy = {'kind': 'plane', 'id': 'P', 'sources': ['C', 'L'], 'systems': [], 'exclude': [], 'direction': 0.0,
              'slope': SLOPE, 'anchor': [-60.0, 0.0], 'z_anchor': Z0, 'label': ''}
    p = PlaneProfile.from_dict(legacy)
    assert p.shape == 'linear' and p.to_dict() == dict(legacy, shape='linear')      # no stepped / curved keys
    ref, _ = _run(p)
    # the fields of the other types are IGNORED by a linear cut
    q = PlaneProfile.from_dict(dict(legacy, shape='linear', span=33.0, step_run=5.0, control=[0.9, 0.1]))
    other, _ = _run(q)
    assert [i.cuts for i in other.instances] == [i.cuts for i in ref.instances]
    for k, op in cuts(ref).items():
        z = 1.5 * k + 0.75
        if not op.get('remove_all'):
            assert op['point'][0] == pytest.approx(-60 + (z - Z0) / SLOPE, abs=1e-9)


def test_invalid_types_are_refused():
    base = shaped('linear').to_dict()
    with pytest.raises(ProfileError, match='unknown'):
        PlaneProfile.from_dict(dict(base, shape='wavy'))
    with pytest.raises(ProfileError, match='span'):
        PlaneProfile.from_dict(dict(base, shape='stepped', span=0))
    with pytest.raises(ProfileError, match='step run'):
        PlaneProfile.from_dict(dict(base, shape='stepped', span=SPAN, step_run=0))
    with pytest.raises(ProfileError, match='control'):
        PlaneProfile.from_dict(dict(base, shape='curved', span=SPAN, control=[0.5, 1.2]))


# ---------------------------------------------------------------------------
# STEPPED
# ---------------------------------------------------------------------------

def test_stepped_uses_whole_layers_and_whole_treads():
    p = shaped('stepped', step_run=40.0)
    n, tread, riser = p.steps
    assert (n, tread, riser) == (3, 40.0, 10.0)                        # 4 treads (2 landings) + 3 risers
    res, rep = _run(p)
    assert rep[0].valid
    C = cuts(res)
    runs = [(k, run_of(op)) for k, op in sorted(C.items())]
    assert runs
    for k, t in runs:
        if t is not None:
            assert abs(t / tread - round(t / tread)) < 1e-9, (k, t)      # every cut on a riser line
            assert t == pytest.approx(p.cut_t(1.5 * k + 0.75), abs=1e-9)  # the layer's own mid-height decides
    ts = [t if t is not None else math.inf for _, t in runs]
    assert ts == sorted(ts)                                             # only removes as Z rises
    # risers: runs of consecutive layers with the same cut — whole layers, 10 in = 6 or 7 layers
    groups = []
    for k, t in runs:
        if groups and groups[-1][0] == t:
            groups[-1][1] += 1
        else:
            groups.append([t, 1])
    assert [t for t, _ in groups][:3] == [40.0, 80.0, 120.0]
    assert [c for _, c in groups[:3]] == [6, 7, 7] and sum(c for _, c in groups[:3]) == 20   # = the 30 in rise
    # layers of one riser carry the identical op → ONE shared cut variant
    for t, _ in groups:
        keys = {res.instances[k].geometry_key for k, tt in runs if tt == t}
        assert len(keys) == 1


def test_stepped_has_real_landings_spanning_the_requested_run():
    """Regression (review 2026-10-15): the centred stair spent half a riser at
    each end — the first tread sat half a riser ABOVE the start and the last
    half a riser below the end. Now: a real lower landing AT the start
    elevation and a real upper landing AT the end elevation, the stair over
    exactly the requested run."""
    p = shaped('stepped', step_run=40.0)
    n, tread, riser = p.steps
    # the top of the kept material at run t: the highest layer mid-height z whose cut keeps t
    def top_at(t):
        zs = [1.5 * k + 0.75 for k in range(80)]
        kept = [z for z in zs if not isinstance(p.cut_t(z), str) and p.cut_t(z) <= t + 1e-9]
        return max(kept) if kept else None
    # LOWER LANDING: across the whole first tread the top is the start elevation (a layer mid there)
    assert top_at(0.0) == top_at(tread - 0.01) == Z0
    # UPPER LANDING: across the whole last tread the top is the end elevation
    assert top_at(SPAN - tread) == top_at(SPAN - 0.01) == Z0 + RISE
    # the risers between them, each one riser higher, the treads equal: the stair spans the run
    assert [top_at(j * tread + 1) for j in range(n + 1)] == pytest.approx([Z0 + j * riser for j in range(n + 1)], abs=1.5)   # (whole layers)
    assert (n + 1) * tread == pytest.approx(SPAN)
    # the exact cut runs at the two ends
    assert p.cut_t(Z0) == 0.0 and p.cut_t(Z0 + RISE) == SPAN - tread and p.cut_t(Z0 + RISE + 0.01) > SPAN
    assert p.cut_t(Z0 + 0.01) == tread                     # just above the start: the lower landing's top is cut
    # Linear and Curved are untouched by this rule
    assert shaped('linear').cut_t(Z0 + 15.0) == pytest.approx(80.0)
    assert shaped('curved', control=(0.5, 0.5)).cut_t(Z0 + 15.0) == pytest.approx(80.0, abs=1e-9)


def test_stepped_deterministic_rule():
    # requested runs round to a whole number of treads over the span (≥ 2: the two landings)
    assert shaped('stepped', step_run=37.0).steps == (3, 40.0, 10.0)
    assert shaped('stepped', step_run=30.0).steps == (4, 32.0, 7.5)
    assert shaped('stepped', step_run=500.0).steps == (1, 80.0, 30.0)
    a, _ = _run(shaped('stepped', step_run=30.0))
    b, _ = _run(shaped('stepped', step_run=30.0))
    assert [i.cuts for i in a.instances] == [i.cuts for i in b.instances]
    # 7.5 in risers at 1.5 in layers = 5 whole layers each
    from collections import Counter
    per = Counter(run_of(op) for op in cuts(a).values() if run_of(op) is not None)
    assert all(per[32.0 * j] == 5 for j in range(1, 5)), per


# ---------------------------------------------------------------------------
# CURVED
# ---------------------------------------------------------------------------

def test_curved_endpoints_are_exact_and_the_chord_control_is_linear():
    for ctl in ((0.5, 0.5), (0.8, 0.2), (0.2, 0.8), (0.0, 1.0), (1.0, 0.0), (0.3, 0.3)):
        p = shaped('curved', control=ctl)
        assert p.cut_t(Z0) == 0.0 and p.cut_t(Z0 + RISE) == SPAN, ctl
    lin, cur = shaped('linear'), shaped('curved', control=(0.3, 0.3))
    for k in range(80):
        z = 1.5 * k + 0.75
        a, b = lin.cut_t(z), cur.cut_t(z)
        assert a == pytest.approx(b, abs=1e-9) if 0 <= (z - Z0) / RISE <= 1 else True


def test_curved_concave_and_convex():
    lin = shaped('linear')
    gentle_first = shaped('curved', control=(0.8, 0.2))     # below the chord: rises slowly, then steepens
    steep_first = shaped('curved', control=(0.2, 0.8))      # above the chord: rises fast, then flattens
    mids = [1.5 * k + 0.75 for k in range(21, 40)]          # strictly between the start and end layers
    for z in mids:
        tl = lin.cut_t(z)
        assert gentle_first.cut_t(z) > tl + 1e-6 and steep_first.cut_t(z) < tl - 1e-6, z
    # at half the rise: well apart from the chord (a real bow, not a rounding effect)
    zm = Z0 + RISE / 2
    assert gentle_first.cut_t(zm) - lin.cut_t(zm) > 20 and lin.cut_t(zm) - steep_first.cut_t(zm) > 20
    for p in (gentle_first, steep_first):
        res, rep = _run(p)
        assert rep[0].valid
        runs = [run_of(op) for _, op in sorted(cuts(res).items())]
        ts = [t if t is not None else math.inf for t in runs]
        assert ts == sorted(ts)                               # monotonic: only removes as Z rises
        for k, op in cuts(res).items():                       # whole layers: each layer's cut is its mid-height's
            if not op.get('remove_all'):
                assert run_of(op) == pytest.approx(p.cut_t(1.5 * k + 0.75), abs=1e-9)


def test_beyond_the_endpoints_the_curve_follows_its_end_tangents():
    p = shaped('curved', control=(0.8, 0.2))
    # below the start: the start tangent (run / rise = u / v × span / rise)
    assert p.cut_t(Z0 - 3.0) == pytest.approx(SPAN * (0.8 / 0.2) * (-3.0 / RISE))
    # above the end: the end tangent
    assert p.cut_t(Z0 + RISE + 3.0) == pytest.approx(SPAN * (1 + (0.2 / 0.8) * (3.0 / RISE)))
    flat_start = shaped('curved', control=(1.0, 0.0))        # horizontal at the start: nothing below it is cut
    assert flat_start.cut_t(Z0 - 1.0) == 'all'
    flat_end = shaped('curved', control=(0.0, 1.0))          # horizontal at the end: everything above it goes
    assert flat_end.cut_t(Z0 + RISE + 1.0) == 'none'


# ---------------------------------------------------------------------------
# locality, selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('shape,kw', [('stepped', {'step_run': 40.0}), ('curved', {'control': (0.8, 0.2)})])
def test_only_the_selection_is_cut(shape, kw):
    res, rep = _run(shaped(shape, **kw))
    for op in cuts(res).values():
        assert op['sources'] == ['C', 'L'] and op['systems'] == [] and op['exclude'] == []
    assert not any('U' in json.dumps(c) for i in res.instances for c in i.cuts if c[0] == 'plane')


# ---------------------------------------------------------------------------
# the REAL Designer: terminations on each layer's cut line, unselected walls
# unchanged, Wall Systems, a section transform + an opening
# ---------------------------------------------------------------------------

def _line_dist(p, inst, w):
    (px, py), (nx, ny) = p.line_at(0.5 * (inst.z_bottom + inst.z_top))
    return (w[0] - px) * nx + (w[1] - py) * ny


@pytest.mark.parametrize('shape,kw,kind', [('stepped', {'step_run': 30.0}, 'hollow'),
                                           ('curved', {'control': (0.8, 0.2)}, 'hollow'),
                                           ('curved', {'control': (0.2, 0.8)}, 'interleaved'),
                                           ('stepped', {'step_run': 24.0}, 'skin_web')])
def test_real_designer_terminations_on_the_layer_cut(shape, kw, kind):
    from layer_assembly.designer_source import DesignerSource
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building(kind)}])
    # uphill toward −x from the rectangle's far side (x 200) across the circle (x −60): 260 in
    p = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=40.0 / 260.0, anchor=(200.0, 0.0),
                     z_anchor=24.0, shape=shape, span=260.0, **kw)
    a = Assembly(1.5, [Section('A', 90)], profiles=[p])
    res, rep, geo = stackp(src, a)
    assert rep[0].valid and len(rep[0].layers) > 20
    canon = geo[res.instances[0].geometry_key][0]
    unsel = [q for q in canon if all(x < -100 for x, _ in q['pts'])]
    assert unsel
    for L in rep[0].layers:
        inst = res.instances[L['layer']]
        polys = geo[inst.geometry_key][0]
        if not L['removed_all']:
            ends = terminations(src, inst)
            assert all(abs(_line_dist(p, inst, w)) < 1e-6 for w in ends), [(_line_dist(p, inst, w)) for w in ends][:4]
        # the unselected wall prints unchanged on every layer
        assert not _changed(unsel, [pl for pl in polys if all(x < -100 for x, _ in pl['pts'])])
    if shape == 'stepped':
        # whole-layer risers: the number of distinct cut variants equals the number of treads reached
        ks = {res.instances[L['layer']].geometry_key for L in rep[0].layers if not L['removed_all']}
        assert len(ks) <= p.steps[0] + 1


def test_coexists_with_a_section_transform_and_an_opening():
    from layer_assembly.designer_source import DesignerSource
    from layer_assembly.source import bbox_of
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('hollow')}])
    fps = {'A': Footprint.of_bbox(*bbox_of(src.geometry('A').polylines))}
    p = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.15, anchor=(205.0, 0.0), z_anchor=18.0,
                     shape='curved', span=265.0, control=(0.25, 0.75))
    o = OpeningProfile('O', WallRef('U'), 60, 12, 48, 24)
    tf = SectionTransform('linear', (0.25, 0.0), 0.0)
    a = Assembly(1.5, [Section('A', 96, '', tf)], profiles=[p, o])
    res, rep, geo = stackp(src, a, fps=fps)
    assert all(r.valid for r in rep)
    both = [i for i in res.instances if any(c[0] == 'plane' for c in i.cuts) and any(c[0] == 'opening' for c in i.cuts)]
    assert both
    for inst in both[::3]:
        assert inst.transform.tx != 0
        assert all(abs(_line_dist(p, inst, w)) < 1e-6 for w in terminations(src, inst))


# ---------------------------------------------------------------------------
# the endpoints, Save / Open, the print job
# ---------------------------------------------------------------------------

def _body(profile):
    return {'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 60}], 'profiles': [profile]},
            'designs': [{'id': 'A', 'name': 'A', 'document': building('hollow')}]}


def test_print_plan_and_job_consume_the_resolved_geometry():
    from app import app
    from layer_assembly import web as W
    c = app.test_client()
    W._PLANS.clear()
    lin = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=24.0 / 260.0, anchor=(200.0, 0.0),
                       z_anchor=24.0).to_dict()
    stp = dict(lin, shape='stepped', span=260.0, step_run=52.0)
    out = {}
    for name, prof in (('linear', lin), ('stepped', stp)):
        r = c.post('/api/assembly/print_plan', json=_body(prof))
        assert r.status_code == 200, r.get_json()
        job = c.post('/api/assembly/print_job', json=dict(_body(prof), name=name))
        assert job.status_code == 200 and job.get_json()['job']['lifts'], job.get_json()
        out[name] = r.get_json()
    a, b = out['linear']['plan'], out['stepped']['plan']
    assert len(a['layers']) == len(b['layers'])
    assert json.dumps(a['layers'], sort_keys=True) != json.dumps(b['layers'], sort_keys=True)  # a different top


def test_project_file_migrates_planes_as_linear_and_round_trips_types():
    import project_file as PF
    plane = PlaneProfile('P', systems=('WS1',), direction=0.0, slope=0.3, anchor=(-60.0, 0.0), z_anchor=20.0).to_dict()
    plane.pop('shape')                                                 # as saved before 2026-10-15
    proj = {'name': 'm', 'designer': {'designs': [{'id': 'A', 'name': 'A', 'parent': None,
                                                   'document': building('hollow')}],
                                      'active_design': 'A', 'id_counter': 2, 'design_counter': 1},
            'assembly': {'state': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 60}],
                                   'profiles': [plane]}, 'header_counter': 1},
            'wall_systems': []}
    v5 = {'format': PF.FORMAT, 'schema_version': 5, 'project': copy.deepcopy(proj)}
    v5['project']['machine_profile'] = None
    v5['project'].pop('machine_profile')
    out = PF.load(json.dumps(v5))
    got = out['project']['assembly']['state']['profiles'][0]
    # (schema 7: a plane selects WALLS — the in-document record WS1 is converted, its member C becomes a source)
    assert out['migrated_from'] == 5 and got['shape'] == 'linear'
    assert {k: v for k, v in got.items() if k != 'shape'} == dict(plane, sources=['C'], systems=[])
    # identical geometry: the migrated plane resolves to exactly the old ops
    a0 = Assembly.from_dict(proj['assembly']['state'])
    a1 = Assembly.from_dict(out['project']['assembly']['state'])
    srcs = {'A': [{'source': 'C', 'label': 'C', 'closed': True, 'system': 'WS1', 'system_label': 'WS1',
                   'points': [[60 * math.cos(k / 8 * math.pi), 60 * math.sin(k / 8 * math.pi)] for k in range(16)]}]}
    r0, _ = apply_profiles(resolve(a0, ['A']), a0.profiles, {'A': {}}, 3.0, 1.5, srcs)
    r1, _ = apply_profiles(resolve(a1, ['A']), a1.profiles, {'A': {}}, 3.0, 1.5, srcs)
    def _ops(inst):                     # (the migrated plane spells its selection as explicit sources)
        out = []
        for c in inst.cuts:
            spec = json.loads(c[1])
            spec.pop('sources', None), spec.pop('systems', None)
            out.append((c[0], json.dumps(spec, sort_keys=True)) + tuple(c[2:]))
        return out
    assert [_ops(i) for i in r0.instances] == [_ops(i) for i in r1.instances]
    # stepped / curved round trip through Save / Open
    for extra in ({'shape': 'stepped', 'span': 160.0, 'step_run': 24.0},
                  {'shape': 'curved', 'span': 160.0, 'control': [0.7, 0.25]}):
        P = copy.deepcopy(proj)
        P['assembly']['state']['profiles'] = [dict(plane, **extra)]
        back = PF.load(json.dumps(PF.save(P)))['project']['assembly']['state']['profiles'][0]
        assert all(back[k] == v for k, v in extra.items())
        assert PlaneProfile.from_dict(back).shape == extra['shape']

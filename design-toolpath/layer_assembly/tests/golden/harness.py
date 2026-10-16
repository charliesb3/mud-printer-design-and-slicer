"""
GOLDEN REGRESSION HARNESS (M0, 2026-10-15 — WALL_ARCHITECTURE.md §10 / §11).

Freezes the meaningful architectural and printable outputs of real and
synthetic projects so the ownership / junction refactor (M1…) cannot change
geometry unnoticed. A case is a `.muddesign` FILE (real projects copied from
the user's models; synthetic ones written by `cases.py` in the schema they
were authored in) — loaded through the real project loader every run, so every
schema migration is exercised.

Per case, the same requests the browser makes:
  designer.<design>      /api/effective_paths of each Layer Design (full build:
                         printable centrelines, regions, network junctions,
                         trim status) — plus the ENVELOPE build (envelope.<design>)
  assembly               /api/assembly/resolve (instances, placements, cut
                         specs, profile reports, support findings)
  plan                   /api/assembly/print_plan detail 'full'
  mudjob                 /api/assembly/print_job (`generated_at` removed) —
                         byte-identical canonical text

Every component is stored as TWO SHA-256 hashes of canonical JSON (floats
rounded to 1e-6) plus a small readable SUMMARY (counts, bounds, lengths) so a
change says WHERE it happened:
  'geom'  — with LABEL fields removed (internal build-record / strand / derived
            path ids, names, labels: renaming a record never moves a bead —
            verified 2026-10-15: the G-code is identical) — ASSERTED;
  'hash'  — everything — asserted only with GOLDEN_STRICT=1 (a reviewed list
            of label-only changes, e.g. M2's build-record ids).
The `.mudjob` component holds no ids: its full hash is always asserted.
Snapshots change only with GOLDEN_UPDATE=1 (never automatically);
GOLDEN_DUMP=<dir> writes the full canonical JSON per component for diffing.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
for p in (os.path.join(ROOT, 'design_proto'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

PROJECTS = os.path.join(HERE, 'projects')
SNAPSHOTS = os.path.join(HERE, 'snapshots')

# fields that are not geometry / behaviour (timing, cache diagnostics)
VOLATILE = {'generated_at', 'timing', 'timings', 'elapsed', 'ms', 'seconds', 'cache', 'cached', 'perf',
            'stage_ms', 'durations', 'worker', 'workers'}


def _round(o):
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return str(o)
        r = round(o, 6)
        return 0.0 if r == 0 else r
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items() if k not in VOLATILE}
    if isinstance(o, (list, tuple)):
        return [_round(v) for v in o]
    return o


# identifiers / labels — not geometry (see the module doc)
LABEL_KEYS = {'id', 'source_id', 'treatment_id', 'label', 'name', 'system', 'system_id', 'systems', 'strand', 'owner',
              'lineage_infill', 'lineage_owner'}


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in LABEL_KEYS}
    if isinstance(o, (list, tuple)):
        return [_strip(v) for v in o]
    return o


def canonical(o) -> str:
    return json.dumps(_round(o), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(o) -> str:
    return hashlib.sha256(canonical(o).encode('utf-8')).hexdigest()


def _poly_summary(polys):
    n, pts, length = 0, 0, 0.0
    xs, ys = [], []
    for p in polys or []:
        q = p.get('pts') or p.get('points') or []
        n += 1
        pts += len(q)
        for a, b in zip(q, q[1:]):
            length += math.hypot(b[0] - a[0], b[1] - a[1])
        for x, y in q:
            xs.append(x)
            ys.append(y)
    bb = [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)] if xs else None
    return {'polylines': n, 'points': pts, 'length': round(length, 3), 'bbox': bb}


def _client():
    from app import app
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS, web._GENERATED, web._ARCH_LIBS):
        reg.clear()
    app.config['TESTING'] = True
    return app.test_client()


def _post(c, url, body):
    r = c.post(url, data=json.dumps(body), content_type='application/json')
    out = r.get_json(silent=True)
    if r.status_code != 200:
        raise AssertionError(f'{url} → {r.status_code}: {json.dumps(out)[:800]}')
    return out


def load_project(path):
    """(project dict migrated to the current schema, migration report)."""
    import project_file as PF
    with open(path, encoding='utf-8') as f:
        res = PF.load(f.read())
    return res['project'], {'migrated_from': res.get('migrated_from'), 'warnings': res.get('warnings') or []}


def snapshot(path) -> dict:
    """{component: {'hash', 'summary'}} of one project file (see module doc)."""
    os.environ['LAYER_ASSEMBLY_WORKERS'] = '0'
    P, mig = load_project(path)
    c = _client()
    designs = P['designer']['designs']
    ws = P.get('wall_systems') or []
    st = (P.get('assembly') or {}).get('state') or {}
    out, full = {}, {}

    def put(name, value, summary):
        out[name] = {'hash': digest(value), 'geom': digest(value if name == 'mudjob' else _strip(value)),
                     'summary': summary}
        full[name] = value

    # ---- the Designer: every Layer Design, full and envelope builds ----
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs, ws)
    for d in designs:
        did = d['id']
        doc = lib.raw_document(did)
        lin = {'lineage': {'designs': designs, 'id': did}} if len(designs) > 1 else {}
        for mode in ('designer', 'envelope'):
            body = dict(doc, **lin)
            if mode == 'envelope':
                body['envelope'] = True
            else:
                body['project_wall_systems'] = ws
            r = _post(c, '/api/effective_paths', body)
            net = r.get('network') or {}
            v = {'printable': r.get('printable'), 'paths': r.get('paths'),
                 'junctions': net.get('junctions'), 'trims': net.get('trims'),
                 'trim_sections': [{k: q.get(k) for k in ('source', 'start', 'end', 'trimmed_by', 'a', 'b')}
                                   for q in net.get('trim_sections') or []],
                 'regions': net.get('regions'), 'wall_systems': net.get('wall_systems')}
            put(f'{mode}.{did}', v, dict(_poly_summary(r.get('printable')),
                                         junctions=len(net.get('junctions') or []),
                                         trims={k: t.get('status') for k, t in (net.get('trims') or {}).items()}))
    # ---- the Assembly / the print ----
    if st.get('sections'):
        ids = [d['id'] for d in designs]
        body = {'assembly': st, 'designs': designs, 'design_ids': ids, 'wall_systems': ws}
        r = _post(c, '/api/assembly/resolve', body)
        sup = r.get('support') or {}
        put('assembly', {k: r.get(k) for k in ('instances', 'profiles', 'support', 'warnings', 'groups', 'sections')},
            {'instances': len(r.get('instances') or []), 'profiles': [(p.get('id'), p.get('valid')) for p in r.get('profiles') or []],
             'findings': len(sup.get('findings') or [])})
        pbody = {'assembly': st, 'designs': designs, 'wall_systems': ws}
        plan = _post(c, '/api/assembly/print_plan', dict(pbody, detail='full'))
        layers = (plan.get('plan') or {}).get('layers') or []
        put('plan', plan.get('plan'), {'layers': len(layers), 'estimate': _round(plan.get('estimate'))})
        job = _post(c, '/api/assembly/print_job', dict(pbody, name='golden'))['job']
        job = {k: v for k, v in job.items() if k != 'generated_at'}
        gcode = [line for L in job.get('lifts') or [] for line in L.get('gcode') or []]
        put('mudjob', job, {'lifts': len(job.get('lifts') or []), 'gcode_lines': len(gcode),
                            'gcode_sha': hashlib.sha256('\n'.join(gcode).encode()).hexdigest()[:16]})
    out['_migration'] = {'hash': digest(mig), 'summary': mig}
    dump = os.environ.get('GOLDEN_DUMP')
    if dump:
        os.makedirs(dump, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        for k, v in full.items():
            with open(os.path.join(dump, f'{base}.{k}.json'), 'w') as f:
                f.write(canonical(v))
    return out


def snapshot_path(case_file) -> str:
    return os.path.join(SNAPSHOTS, os.path.splitext(os.path.basename(case_file))[0] + '.json')


def compare(case_file) -> list:
    """[(component, expected summary, got summary)] of every changed component;
    with GOLDEN_UPDATE=1 the snapshot is (re)written instead."""
    got = snapshot(case_file)
    sp = snapshot_path(case_file)
    if os.environ.get('GOLDEN_UPDATE') == '1' or not os.path.exists(sp):
        if os.environ.get('GOLDEN_UPDATE') != '1':
            raise AssertionError(f'no golden snapshot for {os.path.basename(case_file)} '
                                 f'(create it deliberately with GOLDEN_UPDATE=1)')
        with open(sp, 'w') as f:
            json.dump(got, f, indent=1, sort_keys=True)
            f.write('\n')
        return []
    with open(sp) as f:
        exp = json.load(f)
    diffs = []
    strict = os.environ.get('GOLDEN_STRICT') == '1'
    for k in sorted(set(exp) | set(got)):
        if k == '_migration':
            continue                        # (reported by the migration tests, not geometry)
        e, g = exp.get(k) or {}, got.get(k) or {}
        field = 'hash' if strict or k == 'mudjob' else 'geom'
        if e.get(field) != g.get(field):
            diffs.append((k if field == 'geom' else f'{k} [{field}]', e.get('summary'), g.get('summary')))
    return diffs

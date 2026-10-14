"""PROJECT FILE — whole-project Save / Open (2026-10-09).

A project file is ONE human-readable JSON document holding the complete
AUTHORING state of the Design + Assembly application:

    {
      "format": "mud-printer-project",        # identifies the file kind
      "schema_version": 3,                    # the layout below (migrations key on it)
      "saved_at": "2026-10-09T12:00:00Z",     # informational
      "generator": "design_proto",            # informational
      "project": {
        "name": "Courtyard house",
        "designer": {
          "designs": [LayerDesign.to_dict(), …],   # the Layer Design exchange form
          "active_design": "base",                 # the design open in the Designer
          "id_counter": 42,                        # next Designer record id (p / o …)
          "design_counter": 3                      # next derived design id (d …)
        },
        "assembly": {
          "state": {…},                            # the Assembly state (Assembly.from_dict
                                                   # accepts it; + the UI's id counters)
          "header_counter": 2
        },
        "wall_systems": [{id, name, type, params, web,        # schema 2: the printable
                          members, groups, designs}]          # constructions (wall_groups.py);
      }                                                       # the designs hold Wall GROUPS
    }

What is stored are the AUTHORITATIVE EDITABLE INPUTS, in the forms the
application already exchanges (`layer_design.LayerDesign.to_dict`: Base as a
full Designer document, derived designs as their live delta; the Assembly
state as posted to /api/assembly/*). Everything derived — resolved geometry,
networks, lattices, toolpaths, support findings, previews, caches, undo
histories, selections, view — and the Designer's cached SAMPLE POINTS of
parametric paths are regenerated after loading. (Some records
inside a Designer document look derived but carry intent and are kept as
the document holds them: an InsetPath's `points` are its frozen shape once
its parent is deleted; Wall-System-owned web records carry the lineage's
infill identity and lattice definition; route origins are user-placed.)

MIGRATION: `load` reads any schema version ≤ SCHEMA_VERSION and upgrades it
step by step through MIGRATIONS (version n → n + 1), the ONE place that
knows about old layouts. Add a step (and bump SCHEMA_VERSION) whenever the
layout changes; content-level legacy data inside documents (pre-Wall-System
walls, legacy Assembly profiles) keeps being handled by the model's own
readers, as for any document. A NEWER file than this software understands
is refused with a clear message — never partially loaded.

Validation is complete before anything is returned: a file that fails any
check raises ProjectFileError (a user-facing message); the client replaces
its project only with a fully validated result.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

FORMAT = 'mud-printer-project'
SCHEMA_VERSION = 4
EXTENSION = '.muddesign'
GENERATOR = 'design_proto'
MAX_BYTES = 200 * 1024 * 1024          # (a sanity bound, far above real projects)


class ProjectFileError(ValueError):
    """A project file that cannot be opened / saved; the message is shown to the user."""


# ---------------------------------------------------------------------------
# migrations: version n → n + 1 (the only place that knows old layouts)
# ---------------------------------------------------------------------------
# {from_version: fn(file_dict) -> file_dict at from_version + 1}. Each step
# receives a deep copy and returns the upgraded dict; `migrate` stamps the
# new schema_version. Version 1 is the first released layout (2026-10-09):
# there is no older project file yet.


def _v1_to_v2(data: dict) -> dict:
    """Schema 2 (2026-10-11): WALL GROUPS (architecture, in each Layer Design)
    are separate from the project's WALL SYSTEMS (printable construction,
    the Wall Systems workspace). Each design's Wall Systems are split
    geometry-preservingly (wall_groups.split_designs): one Wall Group per old
    system (members, thickness, alignment, print reference; the old id kept,
    so Assembly references stay valid), one project Wall System per distinct
    construction (old id kept; scoped to designs only where they differed);
    derived designs are re-expressed as deltas of their split parents."""
    from wall_groups import split_designs
    proj = data.get('project') or {}
    D = proj.get('designer') or {}
    designs = D.get('designs')
    if isinstance(designs, list) and designs:
        new, systems = split_designs(designs)
        D['designs'] = new
        proj['wall_systems'] = systems
    else:
        proj.setdefault('wall_systems', [])
    return data


def _v2_to_v3(data: dict) -> dict:
    """Schema 3 (2026-10-11): the DEFAULT Maximum Overhang became 1 in (was
    1.5 in). A project that stored no value was designed and checked with
    1.5 in: that value is made explicit, so it opens exactly as before
    (explicitly saved values are untouched)."""
    st = ((data.get('project') or {}).get('assembly') or {}).get('state')
    if isinstance(st, dict) and st.get('max_overhang') is None:
        st['max_overhang'] = 1.5
    return data


def _v3_to_v4(data: dict) -> dict:
    """Schema 4 (2026-10-14): the DEFAULT Opening Alignment became Vertical
    / Plumb (was Follow Wall, stored only when 'plumb'). A project that stored
    no value — or the short-lived per-opening form — gets its EFFECTIVE
    alignment written explicitly (the same rule as layer_assembly.model.
    _alignment before the change), so it opens exactly as before."""
    st = ((data.get('project') or {}).get('assembly') or {}).get('state')
    if isinstance(st, dict):
        v = st.get('opening_alignment')
        if v is None or isinstance(v, dict):
            ops = [p for p in st.get('profiles') or []
                   if (p.get('kind') or 'opening') == 'opening' and not p.get('source_opening')]
            any_plumb = any(a == 'plumb' for a in (v or {}).values()) or any(p.get('alignment') == 'plumb' for p in ops)
            all_plumb = all(p.get('alignment') == 'plumb' for p in ops)
            st['opening_alignment'] = 'plumb' if any_plumb and all_plumb else 'follow'
    return data


MIGRATIONS: dict = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4}


def migrate(data: dict) -> tuple[dict, int]:
    """(data upgraded to SCHEMA_VERSION, the version it was saved with)."""
    v = data.get('schema_version')
    if isinstance(v, bool) or not isinstance(v, int):
        raise ProjectFileError('This project file has no valid schema version.')
    if v > SCHEMA_VERSION:
        raise ProjectFileError(
            f'This project was saved by a newer version of the software (project schema {v}); '
            f'this version opens project schema {SCHEMA_VERSION} and older. '
            f'Update the software to open it. Nothing was changed.')
    if v < 1:
        raise ProjectFileError(f'Unknown project schema version {v}.')
    out = copy.deepcopy(data)
    while v < SCHEMA_VERSION:
        step = MIGRATIONS.get(v)
        if step is None:
            raise ProjectFileError(f'No upgrade from project schema {v} is available.')
        out = step(out)
        v += 1
        out['schema_version'] = v
    return out, data['schema_version']


# ---------------------------------------------------------------------------
# validation (current schema)
# ---------------------------------------------------------------------------

def _need(cond, msg):
    if not cond:
        raise ProjectFileError(msg)


def _num_suffix_max(ids, prefix):
    n = 0
    for i in ids:
        m = re.fullmatch(re.escape(prefix) + r'(\d+)', str(i))
        if m:
            n = max(n, int(m.group(1)))
    return n


def _record_ids(obj, out):
    """Every `id` value inside a document / patch (patch keys are record ids)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'id' and isinstance(v, str):
                out.append(v)
            _record_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _record_ids(v, out)


def _patch_ids(patch, out):
    for changes in (patch or {}).values():
        if isinstance(changes, dict):
            out.extend(str(k) for k in changes)


def _counter(v, name):
    if v is None:
        return 0
    _need(isinstance(v, int) and not isinstance(v, bool) and v >= 0,
          f'The project file is damaged: {name} must be a whole number ≥ 0.')
    return v


def validate(project, check_document=None) -> tuple[dict, dict, list]:
    """Validate a CURRENT-schema `project` payload. Returns
    (normalised project, resolved documents {design id: document}, warnings).
    `check_document(doc)` (the Designer's deserialiser) must raise on a
    document it cannot build from."""
    from layer_design import DesignLibrary, LayerDesignError
    _need(isinstance(project, dict), 'The project file is damaged: "project" must be an object.')
    warnings: list = []
    P = copy.deepcopy(project)
    name = P.get('name', '')
    _need(isinstance(name, str), 'The project file is damaged: the project name must be text.')

    # ---- Designer: the Layer Designs ---------------------------------------------
    D = P.get('designer')
    _need(isinstance(D, dict), 'The project file has no Designer section.')
    designs = D.get('designs')
    _need(isinstance(designs, list) and designs, 'The project file contains no Layer Designs.')
    ids = []
    for d in designs:
        _need(isinstance(d, dict), 'The project file is damaged: a Layer Design is not an object.')
        did = d.get('id')
        _need(isinstance(did, str) and did, 'The project file is damaged: a Layer Design has no id.')
        _need(isinstance(d.get('name', ''), str), f'Layer Design {did!r}: its name must be text.')
        par = d.get('parent')
        _need(par is None or isinstance(par, str), f'Layer Design {did!r}: invalid parent.')
        if par is None:
            _need(isinstance(d.get('document'), dict), f'Base design {did!r} has no document.')
        else:
            _need(isinstance(d.get('patch', {}), dict) and isinstance(d.get('settings', {}), dict),
                  f'Layer Design {did!r}: its differences (patch / settings) are damaged.')
        ids.append(did)
    _need(len(set(ids)) == len(ids), 'The project file is damaged: two Layer Designs share an id.')
    roots = [d for d in designs if d.get('parent') is None]
    _need(len(roots) == 1, f'A project has exactly one base Layer Design (this file has {len(roots)}).')
    try:
        docs = {did: DesignLibrary(designs).document(did) for did in ids}
        designs = _without_derived_points(designs, docs)
        lib = DesignLibrary(designs)
        docs = {did: lib.document(did) for did in ids}
    except (LayerDesignError, KeyError, TypeError, ValueError, AttributeError) as e:
        raise ProjectFileError(f'The Layer Designs cannot be resolved: {e}')
    # ---- the project's WALL SYSTEMS (printable construction; schema 2) ------------
    from wall_groups import TYPES, compose
    systems = P.get('wall_systems', [])
    _need(isinstance(systems, list), 'The project file is damaged: "wall_systems" must be a list.')
    seen = set()
    for w in systems:
        _need(isinstance(w, dict) and isinstance(w.get('id'), str) and w['id'],
              'The project file is damaged: a Wall System has no id.')
        _need(w['id'] not in seen, f'The project file is damaged: two Wall Systems share the id {w["id"]!r}.')
        seen.add(w['id'])
        _need(w.get('type') in TYPES, f'Wall System {w["id"]!r}: unknown construction {w.get("type")!r}.')
        for f in ('members', 'groups'):
            _need(isinstance(w.get(f, []), list), f'Wall System {w["id"]!r}: its {f} are damaged.')
        _need(w.get('designs') is None or isinstance(w.get('designs'), list),
              f'Wall System {w["id"]!r}: its design scope is damaged.')
        _need(isinstance(w.get('params', {}), dict) and isinstance(w.get('web', {}), dict),
              f'Wall System {w["id"]!r}: its parameters are damaged.')
    P['wall_systems'] = systems
    for did, doc in docs.items():
        _need(isinstance(doc.get('source_paths', []), list),
              f'Layer Design {did!r}: its paths are damaged.')
        _need(isinstance(doc.get('wall_groups', []), list),
              f'Layer Design {did!r}: its Wall Groups are damaged.')
        if check_document is not None:
            try:
                check_document(compose(doc, did, systems))
            except Exception as e:                      # (any failure of the Designer's reader)
                raise ProjectFileError(f'Layer Design {_label(designs, did)} cannot be read: '
                                       f'{type(e).__name__}: {e}')
    active = D.get('active_design', roots[0]['id'])
    if active not in ids:
        warnings.append(f'The design that was open ({active!r}) is missing — opening '
                        f'{roots[0].get("name") or roots[0]["id"]} instead.')
        active = roots[0]['id']
    # id counters: never below what the content already uses (a hand-edited
    # file must not make the next new path collide with an existing one)
    rec = []
    for d in designs:
        _record_ids(d.get('document'), rec)
        _patch_ids(d.get('patch'), rec)
    id_counter = max(_counter(D.get('id_counter'), 'id_counter'),
                     _num_suffix_max(rec, 'p') + 1, _num_suffix_max(rec, 'o') + 1, 1)
    design_counter = max(_counter(D.get('design_counter'), 'design_counter'),
                         _num_suffix_max(ids, 'd') + 1, 1)
    P['designer'] = {'designs': [_design_out(d) for d in designs], 'active_design': active,
                     'id_counter': id_counter, 'design_counter': design_counter}

    # ---- Assembly -----------------------------------------------------------------
    A = P.get('assembly')
    _need(isinstance(A, dict), 'The project file has no Assembly section.')
    st = A.get('state')
    _need(isinstance(st, dict), 'The project file has no Assembly state.')
    try:
        from layer_assembly.model import Assembly
        asm = Assembly.from_dict(st)
    except Exception as e:
        raise ProjectFileError(f'The Assembly cannot be read: {type(e).__name__}: {e}')
    _need(asm.layer_height > 0, 'The Assembly layer height must be greater than 0.')
    for i, s in enumerate(asm.sections):
        _need(s.design_id in ids,
              f'Assembly section {i + 1} uses Layer Design {s.design_id!r}, which is not in the project.')
    st = dict(st)
    st['center_counter'] = max(_counter(st.get('center_counter'), 'center_counter'),
                               _num_suffix_max([c.id for c in asm.centers], 'c'))
    st['profile_counter'] = max(_counter(st.get('profile_counter'), 'profile_counter'),
                                _num_suffix_max([p.id for p in asm.profiles], 'p'))
    header_counter = max(_counter(A.get('header_counter'), 'header_counter'),
                         _num_suffix_max([getattr(o, 'id', '') for o in asm.objects], 'h'))
    P['assembly'] = {'state': st, 'header_counter': header_counter}
    P['name'] = name
    return P, docs, warnings


# The Designer caches each path's SAMPLED POINTS on its record (`points`,
# app.js _computePrimitivePoints) for drawing and hit tests. For parametric
# shapes — and drawn paths that keep their `control_points` — they are
# derived (the backend never reads them) and recomputed by the Designer on
# load; they are not stored. Kept: an ExplicitPath without control points
# (its points ARE the shape) and an InsetPath (its frozen shape).
PARAMETRIC = ('LinePath', 'CirclePath', 'EllipsePath', 'RectanglePath', 'QuadBezierPath')


def _derived_points(rec, resolved=None) -> bool:
    t = (rec.get('type') if 'type' in rec else (resolved or {}).get('type')) or 'ExplicitPath'
    if t in PARAMETRIC:
        return True
    cps = rec['control_points'] if 'control_points' in rec else (resolved or {}).get('control_points')
    return t == 'ExplicitPath' and bool(cps)


def _without_derived_points(designs, docs):
    out = []
    for d in designs:
        d = copy.deepcopy(d)
        if d.get('parent') is None:
            for p in d['document'].get('source_paths') or []:
                if isinstance(p, dict) and 'points' in p and _derived_points(p):
                    del p['points']
        else:
            changes = (d.get('patch') or {}).get('source_paths')
            if isinstance(changes, dict):
                by_id = {p.get('id'): p for p in docs[d['id']].get('source_paths') or [] if isinstance(p, dict)}
                for rid in list(changes):
                    ch = changes[rid]
                    if isinstance(ch, dict) and 'points' in ch and _derived_points(ch, by_id.get(rid)):
                        del ch['points']
                        if not ch:
                            del changes[rid]               # (only the cached points had changed)
                if not changes:
                    d['patch'].pop('source_paths')
        out.append(d)
    return out


def _label(designs, did):
    d = next((x for x in designs if x.get('id') == did), {})
    return repr(d.get('name') or did)


def _design_out(d):
    """A Layer Design as the project stores it (LayerDesign.to_dict + its
    stored fields only — nothing derived)."""
    from layer_design import LayerDesign
    return LayerDesign.from_dict(d).to_dict()


# ---------------------------------------------------------------------------
# reading / writing
# ---------------------------------------------------------------------------

def parse(text) -> dict:
    """The JSON object of a project file (raises ProjectFileError)."""
    if isinstance(text, (bytes, bytearray)):
        _need(len(text) <= MAX_BYTES, 'The project file is too large.')
        try:
            text = text.decode('utf-8')
        except UnicodeDecodeError:
            raise ProjectFileError('This is not a project file (not UTF-8 text).')
    _need(isinstance(text, str), 'This is not a project file.')
    _need(len(text) <= MAX_BYTES, 'The project file is too large.')
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProjectFileError(f'This is not a valid project file (JSON error at line {e.lineno}, '
                               f'column {e.colno}).')
    _need(isinstance(data, dict), 'This is not a project file.')
    _need(data.get('format') == FORMAT,
          f'This is not a Mud Printer project file (expected format "{FORMAT}").')
    return data


def load(text, check_document=None) -> dict:
    """Open a project file: parse → migrate → validate. Returns
    {project, documents, active_document, schema_version, migrated_from, warnings}."""
    data = parse(text)
    data, saved_v = migrate(data)
    _need('project' in data, 'The project file is damaged: it has no project.')
    project, docs, warnings = validate(data['project'], check_document)
    if saved_v < SCHEMA_VERSION:
        warnings.insert(0, f'Opened a project saved with project schema {saved_v}; '
                           f'it will be saved as schema {SCHEMA_VERSION}.')
    active = project['designer']['active_design']
    return {'project': project, 'active_document': docs[active], 'schema_version': SCHEMA_VERSION,
            'migrated_from': saved_v, 'warnings': warnings}


def save(project, check_document=None, now=None) -> dict:
    """The project FILE object (validated) for `project` (current schema)."""
    project, _docs, _w = validate(project, check_document)
    t = now or datetime.now(timezone.utc)
    return {'format': FORMAT, 'schema_version': SCHEMA_VERSION,
            'saved_at': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'generator': GENERATOR,
            'project': project}


HEADER_KEYS = ('format', 'schema_version', 'saved_at', 'generator')


def dumps(file_obj: dict) -> str:
    """The file text: human-inspectable — the identifier and version first,
    then the project."""
    head = {k: file_obj[k] for k in HEADER_KEYS if k in file_obj}
    rest = {k: v for k, v in file_obj.items() if k not in head}
    return json.dumps({**head, **rest}, indent=1, ensure_ascii=False) + '\n'


def filename(name: str) -> str:
    base = re.sub(r'[^\w\- ]+', '', (name or '').strip()).strip() or 'project'
    return base[:80] + EXTENSION

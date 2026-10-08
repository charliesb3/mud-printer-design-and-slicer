"""
Design Canvas Prototype — Flask application.

Serves the design canvas UI and exposes a stateless API:
  GET  /api/generators          list available lattice generators + params
  POST /api/route               route a PrintLayer from JSON body
  POST /api/effective_paths     compute effective paths from a PrintLayer JSON
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import traceback
from flask import Flask, jsonify, request, send_from_directory

from model import (
    PrintLayer, ExplicitPath, LinePath, CirclePath, EllipsePath, RectanglePath,
    QuadBezierPath,
    OffsetTreatment, LatticeInstance, TraversalConstraints, Opening, Trim,
    RegionOverride, RegionInfill, JunctionSetting, WallSpec, NetworkWall, WallSystem,
    InsetPath, WallRelation,
    GENERATORS, Vec2
)
from material import MaterialSpec
import infill as infill_mod

app = Flask(__name__, static_folder='static', static_url_path='/static')


# ---------------------------------------------------------------------------
# Static / index
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _wall(d):
    """A path's OWN wall. Absent → none (it inherits its network's wall, if
    any). An explicit thickness 0 is the SINGLE-BEAD override: the path prints
    as a single bead (physical rules: its return-lane solution) even inside a
    thick-walled network — never a fake epsilon wall, never deleted."""
    w = d.get('wall')
    if not isinstance(w, dict) or 'thickness' not in w:
        return None
    t = float(w.get('thickness', 0) or 0)
    sysd = w.get('system')
    return WallSpec(max(0.0, t), w.get('align', 'auto') or 'auto',
                    bool(w.get('print_reference', False)),
                    dict(sysd) if isinstance(sysd, dict) else None)


def _deserialise_path(d: dict):
    p = _deserialise_path_geom(d)
    p.wall = _wall(d)
    return p


def _deserialise_path_geom(d: dict):
    kind = d.get('type', 'ExplicitPath')
    kwargs = dict(
        id=d.get('id'),
        label=d.get('label', ''),
        closed=d.get('closed', False),
        role=d.get('role', 'free'),
        visible=d.get('visible', True),
        corner_radius=(None if d.get('corner_radius') is None
                       else float(d['corner_radius'])),
    )
    if kind == 'LinePath':
        s, e = d['start'], d['end']
        return LinePath(Vec2(*s), Vec2(*e), **kwargs)
    if kind == 'CirclePath':
        return CirclePath(d['cx'], d['cy'], d['radius'], **kwargs)
    if kind == 'EllipsePath':
        return EllipsePath(d['cx'], d['cy'], d['rx'], d['ry'],
                           d.get('rotation', 0.0), **kwargs)
    if kind == 'RectanglePath':
        return RectanglePath(d['x'], d['y'], d['w'], d['h'],
                             float(d.get('rotation', 0.0) or 0.0), **kwargs)
    if kind == 'InsetPath':
        pts = [Vec2(p[0], p[1]) for p in d.get('points', [])]
        kwargs.pop('closed', None)
        return InsetPath(d['parent_id'], float(d.get('distance', 10.0)),
                         d.get('mode', 'inset'), pts, **kwargs)
    if kind == 'QuadBezierPath':
        s, e, c = d['start'], d['end'], d['control']
        return QuadBezierPath(Vec2(*s), Vec2(*e), Vec2(*c), **kwargs)
    # Default: ExplicitPath
    pts = [Vec2(p[0], p[1]) for p in d.get('control_points', d.get('points', []))]
    return ExplicitPath(pts, **kwargs)


def _deserialise_layer(data: dict) -> PrintLayer:
    layer = PrintLayer(
        id=data.get('id', 'layer'),
        label=data.get('label', ''),
    )
    for pd in data.get('source_paths', []):
        layer.source_paths.append(_deserialise_path(pd))

    for od in data.get('offset_treatments', []):
        dist = od['distance']
        if 'direction' in od:
            # EDITOR form (Layer Design documents are stored as the canvas
            # edits them): direction + positive distance → signed distance,
            # exactly as static/app.js buildPayload() converts it
            dist = (1 if (od.get('direction') or 'inside') in ('inside', 'left') else -1) * \
                abs(dist if dist is not None else 10)
        layer.offset_treatments.append(OffsetTreatment(
            id=od['id'],
            source_path_id=od['source_path_id'],
            distance=dist,
            role=od.get('role', 'inner'),
            label=od.get('label', ''),
        ))

    for ld in data.get('lattice_instances', []):
        layer.lattice_instances.append(LatticeInstance(
            id=ld['id'],
            generator_name=ld['generator'],
            path_a_id=ld['path_a_id'],
            path_b_id=ld['path_b_id'],
            params=ld.get('params', {}),
            variation_index=ld.get('variation_index', 0),
            label=ld.get('label', ''),
        ))

    cd = data.get('constraints') or {}
    layer.constraints = TraversalConstraints(
        start_path_id=cd.get('start_path_id'),
        start_t=cd.get('start_t'),
        reverse_direction=cd.get('reverse_direction', False),
        component_order=cd.get('component_order'),
    )

    layer.corner_radius = float(data.get('corner_radius', 0.0))
    layer.cap_style = data.get('cap_style', 'flat')
    layer.cap_corner_radius = float(data.get('cap_corner_radius', 0.0))

    # Openings (absent in older payloads → none)
    for od in data.get('openings', []) or []:
        layer.openings.append(Opening(
            id=od['id'],
            source_path_id=od['source_path_id'],
            center_s=float(od.get('center_s', 0.0)),
            width=float(od.get('width', 12.0)),
            end_treatment=od.get('end_treatment', 'inherit'),
            z_min=od.get('z_min'),
            z_max=od.get('z_max'),
            label=od.get('label', ''),
        ))

    # Trims: suppressed sections of source paths (absent → none)
    for td in data.get('trims', []) or []:
        layer.trims.append(Trim(
            id=td['id'], source_path_id=td['source_path_id'],
            start=[str(x) for x in td.get('start') or []],
            end=[str(x) for x in td.get('end') or []],
            inside={str(k): bool(v) for k, v in (td.get('inside') or {}).items()},
            u_mid=float(td.get('u_mid', 0.5) or 0.0)))

    # Wall-network region overrides (paint bucket); absent → none
    for rd in data.get('region_overrides', []) or []:
        layer.region_overrides.append(RegionOverride(
            id=rd.get('id', ''),
            path_id=rd['path_id'],
            s=float(rd.get('s', 0.0)),
            offset=float(rd.get('offset', 0.0)),
            kind=rd.get('kind', 'wall'),
        ))

    # Region infill (wall-region lattice) and junction-corner treatment
    for fd in data.get('infills', []) or []:
        layer.infills.append(RegionInfill(
            id=fd['id'], path_id=fd['path_id'],
            pattern=fd.get('pattern', 'zigzag'),
            params=dict(fd.get('params') or {}),
            variation_index=int(fd.get('variation_index', 0) or 0),
            kind=fd.get('kind', 'wall') or 'wall',
            owner=fd.get('owner') or None,
        ))
    layer.junction_style = data.get('junction_style', 'miter') or 'miter'
    layer.junction_radius = float(data.get('junction_radius', 0.0) or 0.0)
    for rd in data.get('wall_relations', []) or []:
        layer.wall_relations.append(WallRelation(
            id=rd.get('id') or 'wr', outer_id=rd['outer_id'], inner_id=rd['inner_id'],
            thickness=float(rd.get('thickness', 10.0)), driver=rd.get('driver', 'outer')))
    for wd in data.get('network_walls', []) or []:
        layer.network_walls.append(NetworkWall(
            id=wd['id'], path_id=wd['path_id'],
            thickness=float(wd.get('thickness', 0) or 0),
            align=wd.get('align', 'auto') or 'auto',
            print_reference=bool(wd.get('print_reference', False)),
            system=dict(wd['system']) if isinstance(wd.get('system'), dict) else None))
    for sd in data.get('wall_systems', []) or []:
        if not isinstance(sd, dict):
            continue
        th = sd.get('thickness')
        layer.wall_systems.append(WallSystem(
            id=str(sd.get('id') or f'WS{len(layer.wall_systems) + 1}'),
            type=str(sd.get('type') or 'skin_web'),
            params=dict(sd.get('params') or {}),
            members=[str(x) for x in sd.get('members', []) or []],
            name=str(sd.get('name') or ''),
            thickness=None if th is None else float(th or 0),
            align=sd.get('align', 'auto') or 'auto',
            print_reference=bool(sd.get('print_reference', False)),
            web=dict(sd.get('web') or {})))
    layer.material = MaterialSpec.from_dict(data.get('material'))
    layer.route_origins = [dict({'strand': str(o.get('strand')), 'u': float(o.get('u', 0.0) or 0.0)},
                                **({'pos': [float(o['pos'][0]), float(o['pos'][1])]}
                                   if isinstance(o.get('pos'), (list, tuple)) and len(o['pos']) == 2 else {}))
                           for o in (data.get('route_origins') or []) if o.get('strand')]
    layer.return_paths = bool(data.get('return_paths', True))
    layer.prefer_closed = bool(data.get('prefer_closed', True))
    for jd in data.get('junction_overrides', []) or []:
        layer.junction_overrides.append(JunctionSetting(
            key=jd['key'], treatment=jd.get('treatment', 'miter'),
            radius=float(jd.get('radius', 0.0) or 0.0)))

    return layer


# ---------------------------------------------------------------------------
# API — generators
# ---------------------------------------------------------------------------

@app.route('/api/generators')
def api_generators():
    result = []
    for name, gen in GENERATORS.items():
        result.append({
            'name': name,
            'parameters': [
                {
                    'name': p.name,
                    'label': p.label,
                    'default': p.default,
                    'min': p.min,
                    'max': p.max,
                    'step': p.step,
                }
                for p in gen.parameters()
            ],
        })
    return jsonify(result)


@app.route('/api/infill_patterns')
def api_infill_patterns():
    import solid as solid_mod
    return jsonify([{'name': k, 'description': v, 'kind': 'wall',
                     'parameters': infill_mod.parameters_for(k)}
                    for k, v in infill_mod.PATTERNS.items()] +
                   [{'name': k, 'description': v, 'kind': 'solid',
                     'parameters': solid_mod.PARAMETERS}
                    for k, v in solid_mod.PATTERNS.items()])


# ---------------------------------------------------------------------------
# API — effective paths (geometry only, no routing)
# ---------------------------------------------------------------------------

def _apply_lineage(layer, data):
    """The Designer view of a design that belongs to a project with Layer
    Designs (payload `lineage`: {designs, id, document}) resolves its wall
    lattice through the SAME library as the Layer Assembly — the lineage's
    shared scaffold — so Designer and Assembly show identical geometry."""
    lin = data.get('lineage') or {}
    designs = lin.get('designs') or []
    if len(designs) < 2 or lin.get('id') is None:
        return
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs)
    active = lib.get(lin['id'])
    doc = lin.get('document')
    if doc is not None:                      # the live (unsaved) edit of the active design:
        lib.set_document(active.id, doc)     # lattice-definition edits go to the lineage
    layer.lattice_reference = lib.lattice_reference(active.id) or None
    layer.lattice_lineage = lib.lattice_lineage(active.id) or None


@app.route('/api/effective_paths', methods=['POST'])
def api_effective_paths():
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)
        _apply_lineage(layer, data)
        paths, meta = layer._build_effective()
        return jsonify({
            'paths': [p.to_dict() for p in paths],
            'network': meta['network'],
            'printable': layer.printable_centerlines(paths, meta),
            'material': layer.material.to_dict(),
        })
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


# ---------------------------------------------------------------------------
# API — route (geometry + toolpath)
# ---------------------------------------------------------------------------

@app.route('/api/migrate_wall_systems', methods=['POST'])
def api_migrate_wall_systems():
    """Legacy walls (path / Network Wall / wall infills) → explicit Wall
    Systems (model.PrintLayer.migrate_wall_systems)."""
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)
        _apply_lineage(layer, data)
        return jsonify(layer.migrate_wall_systems())
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


@app.route('/api/route', methods=['POST'])
def api_route():
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)
        _apply_lineage(layer, data)

        # Build routing layer (effective geometry computed once)
        paths, meta = layer._build_effective()
        routing_layer = layer.to_routing_layer(paths, meta)

        # Import routing engine from toolpath_proto
        proto_dir = os.path.join(os.path.dirname(__file__),
                                 '..', 'toolpath_proto')
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)

        from graph import (route_layer, compute_metrics, graph_info,
                           label_passes, route_ends, build_graph, closure_report)

        # Apply traversal constraints
        c = layer.constraints
        start = None
        if c.start_path_id and c.start_t is not None:
            src = next((p for p in layer.source_paths
                        if p.id == c.start_path_id), None)
            if src:
                pt = src.point_at(c.start_t)
                from geometry import Vec2 as RVec2
                start = RVec2(pt.x, pt.y)

        physical = layer.material.physical
        from model import resolve_route_origins
        origin_pts, origin_rep = resolve_route_origins(routing_layer.strands,
                                                       layer.route_origins)
        moves = route_layer(
            routing_layer,
            start=start,
            component_order=c.component_order,
            allow_retrace=not physical,     # physical beads: never retrace
            origins=origin_pts,
        )

        # Reverse direction: reverse move order and swap each move's start/end
        if c.reverse_direction and moves:
            from geometry import PrintMove as _PM
            moves = [
                _PM(kind=m.kind, strand_id=m.strand_id, seg_idx=m.seg_idx,
                    start=m.end, end=m.start)
                for m in reversed(moves)
            ]
            moves = label_passes(moves)   # first pass prints, later passes retrace

        metrics = compute_metrics(moves)
        ends = route_ends(moves)
        ginfo = graph_info(routing_layer)

        return jsonify({
            'layer': {
                'id': layer.id,
                'label': layer.label,
                'paths': [p.to_dict() for p in paths],
            },
            'network': meta['network'],
            'printable': [{'id': s.id, 'pts': [[q.x, q.y] for q in s.points],
                           'closed': bool(s.closed)} for s in routing_layer.strands],
            'material': layer.material.to_dict(),
            'moves': [m.to_dict() for m in moves],
            'metrics': metrics,
            # physical rules: can every connected component print as ONE
            # closed extrusion (start = end, no travel, no retrace)?
            'closure': dict(closure_report(build_graph(routing_layer)), physical=physical),
            'origins': origin_rep,
            'graph': ginfo,
            # For a future layer planner: closed → next layer starts here;
            # open → next layer can run this route reversed (end → start).
            'route_ends': ends.to_dict() if ends else None,
        })
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


# ---------------------------------------------------------------------------
# Layer Designs (Designer side): resolve a design's document / derive the
# delta a design edited in the Designer stores (layer_design.py)
# ---------------------------------------------------------------------------

@app.route('/api/layer_designs/document', methods=['POST'])
def api_layer_design_document():
    try:
        from layer_design import DesignLibrary
        data = request.get_json(force=True)
        lib = DesignLibrary(data['designs'])
        return jsonify({'document': lib.document(data['id']), 'lineage': lib.lineage(data['id'])})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


def _project_bead_width(lib, d):
    """BEAD WIDTH is ONE project-wide MATERIAL value (2026-10-06), owned by
    the lineage ROOT's document (the project's Base). A derived design never
    stores its own: a bead width edited while viewing it moves to the root
    (like a lattice-definition edit). Returns the root id it moved to."""
    mat = (d.settings or {}).get('material')
    if not isinstance(mat, dict) or 'bead_width' not in mat:
        return None
    w = mat.pop('bead_width')
    if not mat:
        d.settings.pop('material')
    root = d
    while root.parent is not None:
        root = lib.get(root.parent)
    root.document['material'] = {**(root.document.get('material') or {}), 'bead_width': w}
    return root.id


@app.route('/api/layer_designs/delta', methods=['POST'])
def api_layer_design_delta():
    try:
        from layer_design import DesignLibrary, derive_delta
        data = request.get_json(force=True)
        lib = DesignLibrary(data['designs'])
        if data.get('id') is None:
            patch, settings = derive_delta(lib.document(data['parent']), data['document'])
            return jsonify({'patch': patch, 'settings': settings})
        # the design's delta; LATTICE DEFINITION edits of inherited infills
        # are moved to their owner (the lineage) — `designs` is the updated set
        moved = lib.set_document(data['id'], data['document'])
        d = lib.get(data['id'])
        material_moved = _project_bead_width(lib, d)
        return jsonify({'patch': d.patch, 'settings': d.settings, 'lattice_moved': moved,
                        'material_moved': material_moved,
                        'designs': [x.to_dict() for x in lib.designs.values()]})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# The LAYER ASSEMBLY workspace: a separate subsystem (design-toolpath/
# layer_assembly) mounted here; it reaches the Designer only through its
# LayerSource adapter.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from layer_assembly.web import bp as assembly_bp   # noqa: E402
app.register_blueprint(assembly_bp)


if __name__ == '__main__':
    app.run(debug=True, port=5051)

"""LAYER ROUTE — the Designer's route of ONE resolved layer, reusable away
from the build (2026-10-09).

The Designer view routes its document in /api/route: effective paths →
routing strands (`PrintLayer.to_routing_layer`) → ROUTE ORIGINS inserted
(`resolve_route_origins`) → `toolpath_proto.graph.route_layer` (start
constraint, component order, no retrace with physical beads) → optional
reversal. A resolved Layer Design (`layer_design.BuiltLayer`, also every
Assembly variant — transformed, cut, derived) keeps only its printable
centrelines; `describe` records, next to them, everything that route needs
(per-strand routing attributes, the document's route origins / start /
order / reverse, physical rules) plus each strand's SOURCE IDENTITY (source
path, Wall System). `prepare` + `route` then reproduce the Designer's route
of that exact geometry — without rebuilding it — and accept a START POINT
(where the nozzle is: the Assembly print planner passes the previous layer's
end) used only where the design itself sets none (manual choices win).
"""
from __future__ import annotations

import os
import sys

_TP = os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto')
if _TP not in sys.path:
    sys.path.insert(0, _TP)


def _systems_of(doc) -> dict:
    """source path id → {'id', 'name', 'type'} of the Wall System it belongs to."""
    out = {}
    for w in doc.get('wall_systems') or []:
        info = {'id': w.get('id'), 'name': w.get('name') or w.get('id'), 'type': w.get('type')}
        for m in w.get('members') or []:
            out.setdefault(m, info)
    return out


def describe(layer, paths, meta, doc, strand_sources) -> tuple[list, dict]:
    """(printable centrelines, routing description) of a built layer.
    printable = `PrintLayer.printable_centerlines` (the router's strands)."""
    rl = layer.to_routing_layer(paths, meta)
    printable = [{'id': s.id, 'pts': [[q.x, q.y] for q in s.points], 'closed': bool(s.closed)} for s in rl.strands]
    by_id = {p.id: p for p in paths}
    systems = _systems_of(doc)
    infill_of = {f.get('id'): f for f in doc.get('infills') or []}
    strands = []
    for s in rl.strands:
        p = by_id.get(s.id)
        src = strand_sources.get(s.id) or getattr(p, 'source_id', None)
        system = systems.get(src)
        f = infill_of.get(src)
        if f is not None:                         # a lattice / web strand: its wall and owner
            src = f.get('path_id') or src
            system = systems.get(src) or ({'id': f.get('owner'), 'name': f.get('owner'), 'type': 'skin_web'}
                                          if f.get('owner') else None)
        strands.append({'role': s.role, 'kind': s.kind, 'retrace_cost': s.retrace_cost,
                        'travel_pairing': bool(s.travel_pairing), 'join_group': s.join_group,
                        'join_points': [[float(q[0]), float(q[1])] for q in s.join_points] if s.join_points else None,
                        'source': src, 'system': system})
    c = layer.constraints
    start = None
    if c.start_path_id and c.start_t is not None:
        sp = next((q for q in layer.source_paths if q.id == c.start_path_id), None)
        if sp is not None:
            pt = sp.point_at(c.start_t)
            start = [pt.x, pt.y]
    routing = {'strands': strands, 'origins': [dict(o) for o in (layer.route_origins or [])],
               'start': start, 'reverse': bool(c.reverse_direction),
               'component_order': list(c.component_order) if c.component_order else None,
               'physical': bool(layer.material.physical)}
    return printable, routing


class Prepared:
    """A layer ready to route: strands (route origins inserted), its graph,
    the origin points / report. Reusable for any number of routes (the graph
    is never modified by routing)."""

    def __init__(self, printable, routing):
        from geometry import Strand, Layer, Vec2
        from graph import build_graph
        from model import resolve_route_origins
        attrs = routing.get('strands') or [{}] * len(printable)
        strands = []
        for p, a in zip(printable, attrs):
            if len(p['pts']) < 2:
                continue
            strands.append(Strand(id=p['id'], role=a.get('role', 'free'), points=[Vec2(*q) for q in p['pts']],
                                  closed=bool(p['closed']), retrace_cost=a.get('retrace_cost', 1.0),
                                  kind=a.get('kind', 'face'), travel_pairing=a.get('travel_pairing', False),
                                  join_group=a.get('join_group'),
                                  join_points=[tuple(q) for q in a['join_points']] if a.get('join_points') else None))
        self.layer = Layer(0.0, strands)
        self.origins, self.origin_report = resolve_route_origins(self.layer.strands, routing.get('origins') or [])
        self.G = build_graph(self.layer)
        self.routing = routing
        self.identity = {p['id']: (a.get('source'), a.get('system')) for p, a in zip(printable, attrs)}


def route(prep: Prepared, start=None) -> tuple[list, dict]:
    """(moves, info) of the layer: the Designer's route of this geometry.
    `start` (x, y): where the nozzle is — used when the design sets no start
    of its own. info: {'manual_start', 'reversed', 'origins'}. The moves are
    toolpath_proto PrintMoves (a leading travel from `start` included)."""
    from geometry import Vec2
    from graph import route_layer, label_passes
    from geometry import PrintMove
    R = prep.routing
    manual = R.get('start')
    st = Vec2(*manual) if manual else (Vec2(*start) if start is not None else None)
    moves = route_layer(prep.layer, start=st, component_order=R.get('component_order'),
                        allow_retrace=not R.get('physical', True), origins=prep.origins, G=prep.G)
    if R.get('reverse') and moves:
        moves = label_passes([PrintMove(m.kind, m.strand_id, m.seg_idx, m.end, m.start) for m in reversed(moves)])
    return moves, {'manual_start': bool(manual), 'reversed': bool(R.get('reverse')),
                   'origins': prep.origin_report}

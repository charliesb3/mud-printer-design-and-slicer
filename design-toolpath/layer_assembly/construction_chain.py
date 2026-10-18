"""
CONSTRUCTION CHAIN — the Assembly side of Construction through Z
(WALL_ARCHITECTURE.md §Z.0; milestone 1, the simple Skin + Web stacking gate).
The Assembly owns layer order, so it runs the chain: for every layer, bottom
up, the landings its webs print = the landings DEPOSITED on the layer below,
carried onto this layer's architecture (design_proto/construction_state
.advance). Three passes:

  1. TOPOLOGY, parallel (DesignerSource.prefetch_topology → DesignLibrary
     .topology): every supported run's face rails, segments, forced motif
     faces and Pass A stations — the layer's architecture, stateless;
  2. the CHAIN, sequential and cheap (projections on polylines);
  3. GEOMETRY, parallel — each layer a variant keyed by its landings; the
     planner draws exactly those landings.

The Designer still receives no Z: the state is one more input of a build. A
LayerSource without `topology` (StaticSource) gets no chain.
"""
from __future__ import annotations

from dataclasses import replace


def run(src, res, cancelled=None, max_overhang=None):
    """The resolved assembly with every instance carrying its construction
    state (`LayerInstance.state` = {'landings': …}); unchanged when the source
    has no topology (a test double) or no layer has a supported run."""
    if not hasattr(src, 'topology'):
        return res
    import sys
    import os
    dp = os.path.join(os.path.dirname(__file__), '..', 'design_proto')
    if dp not in sys.path:
        sys.path.insert(0, dp)
    import construction_state as CS
    from .source import variant_request
    from .model import DEFAULT_MAX_OVERHANG
    h = float(max_overhang if max_overhang is not None else DEFAULT_MAX_OVERHANG)
    insts = list(res.instances)
    reqs = []
    for i in insts:
        d, tr, cu = variant_request(i)[:3]
        reqs.append((d, tr or None, cu or None))
    if hasattr(src, 'prefetch_topology'):
        src.prefetch_topology(reqs, cancelled=cancelled)
    out, prev_state, prev_topo, prev_place = [], None, None, None
    any_state = False
    for i, (d, tr, cu) in zip(insts, reqs):
        if cancelled is not None and cancelled():
            return res
        topo = (src.topology(d, tr, cu) or {}).get('topology') or {}
        place = i.transform.to_dict() if i.transform is not None else None
        state = CS.advance(prev_state, prev_topo, topo, prev_place, place, h)
        if state is None:
            out.append(i)                         # no supported web on this layer: nothing to carry
            prev_state, prev_topo, prev_place = None, None, None
            continue
        any_state = True
        out.append(replace(i, state=state))
        prev_state, prev_topo, prev_place = state, topo, place
    if not any_state:
        return res
    return replace(res, instances=out)

"""
SHADOW JUNCTIONS through the Assembly (M4, 2026-10-15; WALL_ARCHITECTURE.md
§4, §5, §F): which persistent junction records (design_proto/junctions.py)
exist on every RESOLVED layer of the stack — the same per-layer documents
the Assembly's envelope resolve builds (semantic transforms, architectural
cuts). Report only: production geometry does not read it.

Topology may change through Z (§F1): a junction CEASES on the layers where
its transformed walls stop intersecting and APPEARS where they start to —
valid architecture, reported as information; never stretched to persist.
There is no continuation-distance limit (§F2): `max_move` is information.
"""
from __future__ import annotations


def layer_documents(designs, wall_systems, assembly_state) -> list:
    """[(layer index, design id, resolved build-format document)] — every
    layer of the resolved stack (variants: transformed / cut)."""
    from .web import _resolve_stack
    from .source import variant_request
    from .designer_source import architectural_cuts
    data = {'assembly': assembly_state, 'designs': designs,
            'design_ids': [d['id'] for d in designs], 'wall_systems': wall_systems or []}
    st = _resolve_stack(data, envelope=True)
    src, res = st['src'], st['res']
    lib = getattr(src, 'env_lib', None) or src.lib
    out = []
    for i in res.instances:
        d, tr, cu = variant_request(i)[:3]
        doc, _rep = lib.transformed(d, tr or None, architectural_cuts(cu) or None)
        out.append((i.index, d, doc))
    return out


def presence(project, registry=None) -> dict:
    """{'registry', 'layers': junctions.layer_presence(…)} of a (current
    schema) project dict; `registry`: its stored one (else built)."""
    import junctions as J
    designs = project['designer']['designs']
    ws = project.get('wall_systems') or []
    reg = registry or project.get('junctions') or J.build_registry(designs, ws)[0]
    st = (project.get('assembly') or {}).get('state') or {}
    if not st.get('sections'):
        return {'registry': reg, 'layers': {'records': {}, 'events': []}}
    return {'registry': reg, 'layers': J.layer_presence(reg['records'], layer_documents(designs, ws, st))}

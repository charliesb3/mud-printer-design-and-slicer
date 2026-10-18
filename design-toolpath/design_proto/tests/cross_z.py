"""
CROSS-Z HARNESS (construction through Z, Steps 5–9; 2026-10-18).

Measures the PRODUCT on DEPOSITED geometry (WALL_ARCHITECTURE §Z.0), never a
mechanism's internal state:
  * stack()        — the layers of a stack, each the Designer's resolution of
                     the design under that layer's semantic transforms / cuts
                     (exactly what the Assembly asks for);
  * findings()     — the AUTHORITATIVE bead-level support checker
                     (layer_assembly.support) over the stack, at a Maximum
                     Overhang;
  * route()        — required printable topology: open route ends and
                     connected pieces of a layer (the router's own graph);
  * web() / web_points() — the deposited web (Skin + Web lattice) for
                     registration measurements against the host's motion.
"""
import math
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.append(os.path.join(HERE, '..', '..', 'toolpath_proto'))

from layer_design import DesignLibrary          # noqa: E402

BEAD, CONTACT_OVERLAP = 3.0, 0.75
MAT = {'bead_width': BEAD, 'contact_overlap': CONTACT_OVERLAP, 'return_overlap': 0.75, 'physical': True}
MAX_OVERHANG = 1.0                              # in: the Assembly default


def line(sid, a, b):
    return {'id': sid, 'type': 'LinePath', 'label': sid, 'start': list(a), 'end': list(b)}


def rect(sid, x, y, w, h):
    return {'id': sid, 'type': 'RectanglePath', 'label': sid, 'x': x, 'y': y, 'w': w, 'h': h, 'closed': True}


def circle(sid, cx, cy, r):
    return {'id': sid, 'type': 'CirclePath', 'label': sid, 'cx': cx, 'cy': cy, 'radius': r, 'closed': True}


def skin_web_doc(paths, T=10.0, pattern='zigzag', angle=20.0, cap='flat', systems=None, extra=None):
    """A design whose walls are Skin + Web (one Wall System per path, or the
    given groups: systems = [[path ids]…]) with the web's Target Web Angle."""
    doc = {'id': 'b', 'material': dict(MAT), 'cap_style': cap, 'source_paths': list(paths),
           'wall_systems': [], 'infills': []}
    for members in systems or [[p['id']] for p in paths]:
        sid = 'WS' + members[0]
        doc['wall_systems'].append({'id': sid, 'name': sid, 'type': 'skin_web', 'members': list(members),
                                    'thickness': T, 'params': {},
                                    'web': {'pattern': pattern, 'params': {'web_angle': angle},
                                            'variation_index': 0}})
        for m in members:
            doc['infills'].append({'id': 'WEB' + m, 'path_id': m, 'pattern': pattern,
                                   'params': {'web_angle': angle}, 'kind': 'wall', 'variation_index': 0,
                                   'owner': sid})
    doc.update(extra or {})
    return doc


def library(doc):
    return DesignLibrary([{'id': 'b', 'name': 'Base', 'document': doc}])


def about(k, c, dx=0.0, dy=0.0):
    """The semantic transform scaling by k about c, then moving by (dx, dy)."""
    return (k, c[0] * (1 - k) + dx, c[1] * (1 - k) + dy)


def stack(lib, layers, design='b'):
    """[BuiltLayer] — layers: [{source id: (k, tx, ty)} | {'transforms': …, 'cuts': …}]."""
    out = []
    for spec in layers:
        if spec and ('transforms' in spec or 'cuts' in spec):
            tf, cuts = spec.get('transforms'), spec.get('cuts')
        else:
            tf, cuts = spec, None
        out.append(lib.build(design, transforms=tf or None, cuts=cuts or None))
    return out


def web(b):
    """The deposited web: [[(x, y)…]] (every wall lattice polyline)."""
    return [[(q.x, q.y) for q in pl] for v in b.lattice.values() for pl in v]


def web_points(b):
    return [p for pl in web(b) for p in pl]


def findings(layers, max_overhang=MAX_OVERHANG):
    """The bead-level support findings of the stack (layer i on i − 1)."""
    from layer_assembly.model import Assembly, resolve
    from layer_assembly.support import analyse_support
    ids = [f'L{i}' for i in range(len(layers))]
    a = Assembly.from_dict({'layer_height': 1.0, 'max_overhang': max_overhang,
                            'sections': [{'design_id': d, 'height': 1.0} for d in ids]})
    res = resolve(a, ids)
    geo = {d: (b.printable, BEAD) for d, b in zip(ids, layers)}
    return [f.to_dict() for f in analyse_support(res, geo, a).findings]


def route(b):
    """(open route ends, connected pieces) of a layer under the physical rules."""
    from layer_route import Prepared
    from graph import closure_report
    rep = closure_report(Prepared(b.printable, b.routing).G)
    return [q for o in rep.get('open') or [] for q in o.get('at', [])], rep.get('components')


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def near(p, polys, tol):
    return any(seg_dist(p, a, b) <= tol for pl in polys for a, b in zip(pl, pl[1:]))

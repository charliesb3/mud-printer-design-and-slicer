"""Deterministic junction fixtures.

Each builder returns a Designer document (dict) ready to feed
`app._deserialise_layer`. Keep the geometry minimal: ONE junction (or a
small set) so the checks and the SVG stay inspectable. Do NOT change
production code to make a fixture pass — fixtures are evidence, not the
spec.

Convention:
  * All lengths in inches.
  * `material` uses the project defaults (bead 3 in, physical rules on).
  * The Wall System (if any) covers every source path unless noted.
  * Legacy document shape (no `wall_groups`, no project-level
    `wall_systems`): the Designer builds this directly.

Multi-layer fixtures are tuples `(docs, assembly_description)`; the
runner stacks them through DesignLibrary.
"""
from __future__ import annotations
import copy
import math

MATERIAL = {'bead_width': 3.0, 'contact_overlap': 0.75,
            'return_overlap': 0.75, 'physical': True}


def _base_doc(junction_style: str = 'miter', junction_radius: float = 6.0,
              extra: dict | None = None) -> dict:
    doc = {
        'id': 'fx',
        'material': dict(MATERIAL),
        'source_paths': [],
        'wall_systems': [],
        'infills': [],
        'openings': [],
        'network_walls': [],
        'junction_style': junction_style,
        'junction_radius': junction_radius if junction_style == 'round' else 0.0,
    }
    if extra:
        doc.update(extra)
    return doc


_SYSTEM_PARAMS: dict = {
    'single':      {'params': {}, 'web': {}},
    'hollow':      {'params': {}, 'web': {}},
    'skin_web':    {'params': {}, 'web': {'pattern': 'zigzag',
                                            'params': {'spacing': 20},
                                            'variation_index': 0}},
    'parallel':    {'params': {'walls': 4}, 'web': {}},
    'interleaved': {'params': {'pattern': 'wave', 'paths': 4, 'period': 24}, 'web': {}},
    'linked':      {'params': {'pattern': 'wave', 'paths': 4, 'period': 24}, 'web': {}},
    'chain':       {'params': {'pitch': 9.0, 'neck': 0.2}, 'web': {}},
}


def _apply_system(doc: dict, system: str | None, members: list,
                  thickness: float) -> None:
    """Attach a Wall System covering `members`. system=None → default
    Skin + Web via the Designer's own rules (each wall its own thick bead)."""
    if system is None:
        for sid in members:
            for p in doc['source_paths']:
                if p['id'] == sid:
                    p.setdefault('wall', {'thickness': thickness, 'align': 'auto'})
        return
    spec = _SYSTEM_PARAMS[system]
    doc['wall_systems'].append({
        'id': 'WS1', 'name': 'Wall', 'type': system,
        'thickness': thickness, 'align': 'auto', 'print_reference': False,
        'params': dict(spec['params']),
        'members': list(members), 'web': dict(spec['web']),
    })


# ---------------------------------------------------------------------------
# single-junction fixtures (one layer, one feature)
# ---------------------------------------------------------------------------

def corner_90(style: str = 'miter', radius: float = 6.0,
              system: str | None = 'interleaved', thick: float = 10.0) -> dict:
    """A closed rectangle = four 90° convex corners of one Wall System."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room',
         'x': 0, 'y': 0, 'w': 120, 'h': 80, 'closed': True},
    ]
    _apply_system(doc, system, ['R'], thick)
    return doc


def tee(style: str = 'miter', radius: float = 6.0,
        system: str | None = 'interleaved', thick: float = 10.0) -> dict:
    """A T junction: a horizontal line + a vertical line meeting at its mid."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'A', 'type': 'LinePath', 'label': 'A',
         'start': [0.0, 60.0], 'end': [120.0, 60.0]},
        {'id': 'B', 'type': 'LinePath', 'label': 'B',
         'start': [60.0, 60.0], 'end': [60.0, 120.0]},
    ]
    _apply_system(doc, system, ['A', 'B'], thick)
    return doc


def wye(style: str = 'miter', radius: float = 6.0,
        system: str | None = 'interleaved', thick: float = 10.0) -> dict:
    """A Y junction: three lines meeting at a hub, 120° apart."""
    cx, cy, L = 60.0, 60.0, 60.0
    paths = []
    for i, ang in enumerate((90.0, 210.0, 330.0)):
        a = math.radians(ang)
        paths.append({'id': f'L{i}', 'type': 'LinePath', 'label': f'L{i}',
                      'start': [cx, cy],
                      'end': [cx + L * math.cos(a), cy + L * math.sin(a)]})
    doc = _base_doc(style, radius)
    doc['source_paths'] = paths
    _apply_system(doc, system, [p['id'] for p in paths], thick)
    return doc


def cross(style: str = 'miter', radius: float = 6.0,
          system: str | None = 'interleaved', thick: float = 10.0) -> dict:
    """An X junction: two lines crossing at the centre."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'A', 'type': 'LinePath', 'label': 'A',
         'start': [0.0, 60.0], 'end': [120.0, 60.0]},
        {'id': 'B', 'type': 'LinePath', 'label': 'B',
         'start': [60.0, 0.0], 'end': [60.0, 120.0]},
    ]
    _apply_system(doc, system, ['A', 'B'], thick)
    return doc


def single_to_thick(style: str = 'round', radius: float = 6.0,
                     host_system: str | None = 'parallel',
                     thick: float = 10.0) -> dict:
    """A single-bead wall meeting a thick-wall rectangle.
    REPRODUCES the manually reported "single-bead wall creating an
    exterior stub". The line has `wall.thickness = 0` so it prints as
    one bead; it attaches at the midpoint of the rectangle's top edge."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room',
         'x': 0.0, 'y': 0.0, 'w': 160.0, 'h': 100.0, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Branch',
         'start': [80.0, 100.0], 'end': [80.0, 160.0],
         'wall': {'thickness': 0.0, 'align': 'auto'}},
    ]
    _apply_system(doc, host_system, ['R'], thick)
    return doc


def circle_rect(style: str = 'round', radius: float = 6.0,
                system: str | None = 'parallel', thick: float = 10.0) -> dict:
    """A circle grazing a rectangle.
    REPRODUCES the manually reported "circle/rectangle junction with
    distorted parallel paths" — the circle intersects the rectangle's
    right wall, two thick walls intersect at a tangent."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room',
         'x': 0.0, 'y': 0.0, 'w': 160.0, 'h': 120.0, 'closed': True},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Round',
         'cx': 180.0, 'cy': 60.0, 'radius': 50.0, 'closed': True},
    ]
    _apply_system(doc, system, ['R', 'C'], thick)
    return doc


def single_bead_stub(style: str = 'round', radius: float = 6.0,
                     host_system: str | None = 'parallel',
                     thick: float = 10.0) -> dict:
    """A specific reproducer for the "single-bead wall creating an
    exterior stub" symptom: a single-bead line attaches to the host
    rectangle NEAR a corner, where the host's own junction mitres the
    corner. The attachment is 1/4 of the thickness from the corner, so
    the Wall System's lane continuation past the attachment can bend or
    gap. The thick-wall host has thickness `thick`, the branch is
    single-bead (thickness 0). Set `host_system=None` for the Designer's
    default Skin + Web wall for a cleaner reproduction of the stub
    geometry."""
    doc = _base_doc(style, radius)
    doc['source_paths'] = [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room',
         'x': 0.0, 'y': 0.0, 'w': 120.0, 'h': 80.0, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Branch',
         # attaches near the top-right CORNER: 15 in from (120, 80), the
         # thick-wall mitre meets the single-bead stub awkwardly
         'start': [105.0, 80.0], 'end': [105.0, 130.0],
         'wall': {'thickness': 0.0, 'align': 'auto'}},
    ]
    _apply_system(doc, host_system, ['R'], thick)
    return doc


# ---------------------------------------------------------------------------
# multi-layer vertical-coherence fixtures
# ---------------------------------------------------------------------------
#
# Each returns a tuple (library_designs, section_plan). `library_designs` is
# a list of {id, name, parent?, document|patch} that the runner feeds to
# `DesignLibrary`. `section_plan` is a simple list of (design_id, layer_count)
# tuples the runner uses to resolve each layer in order.
#
# No Assembly / layer_assembly dependency — this harness runs on the Designer
# library directly, so it stays independent of the Simulation.
#
# ---------------------------------------------------------------------------

def opening_closes_stack(style: str = 'miter', radius: float = 6.0,
                         system: str | None = 'interleaved',
                         thick: float = 10.0, layers_per_section: int = 2
                         ) -> tuple:
    """Three sections: base → opening on a wall of the rectangle → base
    again. REPRODUCES the manually reported "distorted after an opening
    closes at higher layers" symptom pattern: the top section should
    match the bottom section away from the opening."""
    base_doc = corner_90(style, radius, system, thick)
    designs = [
        {'id': 'base', 'name': 'Base', 'parent': None,
         'document': copy.deepcopy(base_doc)},
        {'id': 'open', 'name': 'Opening', 'parent': 'base', 'patch': {
            'openings': {'o1': {'source_path_id': 'R',
                                'center_s': 60.0, 'width': 24.0}}}},
    ]
    section_plan = [('base', layers_per_section),
                    ('open', layers_per_section),
                    ('base', layers_per_section)]
    return designs, section_plan


def tee_opening_closes_stack(style: str = 'miter', radius: float = 6.0,
                              system: str | None = 'interleaved',
                              thick: float = 10.0,
                              layers_per_section: int = 2) -> tuple:
    """Same as `opening_closes_stack` but on a T junction fixture — a
    stronger stress for lattice coherence across changing junction
    territory (the opening is on the host arm, close to the T centre)."""
    base_doc = tee(style, radius, system, thick)
    designs = [
        {'id': 'base', 'name': 'Base', 'parent': None,
         'document': copy.deepcopy(base_doc)},
        {'id': 'open', 'name': 'Opening', 'parent': 'base', 'patch': {
            'openings': {'o1': {'source_path_id': 'A',
                                'center_s': 20.0, 'width': 24.0}}}},
    ]
    section_plan = [('base', layers_per_section),
                    ('open', layers_per_section),
                    ('base', layers_per_section)]
    return designs, section_plan


# ---------------------------------------------------------------------------
# registry (what `__main__ --list` prints, what pytest sweeps)
# ---------------------------------------------------------------------------

SINGLE_LAYER: dict = {
    # fixture name → (builder, default kwargs)
    'corner_90':         (corner_90,         {}),
    'tee':               (tee,               {}),
    'wye':               (wye,               {}),
    'cross':             (cross,             {}),
    'single_to_thick':   (single_to_thick,   {}),
    'single_bead_stub':  (single_bead_stub,  {}),
    'circle_rect':       (circle_rect,       {}),
}

MULTI_LAYER: dict = {
    'opening_closes_stack':    (opening_closes_stack,    {}),
    'tee_opening_closes_stack':(tee_opening_closes_stack, {}),
}


def catalogue() -> list[tuple]:
    """[(category, name)] of every registered fixture, for CLI listing."""
    return [('single', n) for n in sorted(SINGLE_LAYER)] + \
           [('multi',  n) for n in sorted(MULTI_LAYER)]

"""
Layer Designs (Designer side of the Layer Assembly boundary): id-keyed
inheritance resolved live from the parent, and lattice continuity — a
derived design keeps its parent's wall lattice, clipped, instead of
regenerating it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from layer_design import DesignLibrary, LayerDesign, LayerDesignError, apply_derivation
from model import Vec2, _clip_to_rings


def base_doc(w=200):
    return {'id': 'l', 'source_paths': [
        {'id': 'W', 'type': 'RectanglePath', 'label': 'Wall', 'x': 100, 'y': 140, 'w': w, 'h': 120,
         'closed': True, 'wall': {'thickness': 10}}],
        'offset_treatments': [], 'lattice_instances': [],
        'infills': [{'id': 'I', 'path_id': 'W', 'pattern': 'wave', 'params': {'spacing': 20},
                     'kind': 'wall', 'variation_index': 0}],
        'junction_style': 'miter', 'material': {'bead_width': 3.0, 'physical': True}}


def lib(**extra):
    designs = [{'id': 'base', 'name': 'Base', 'document': base_doc()},
               {'id': 'door', 'name': 'Door Gap', 'parent': 'base',
                'patch': {'openings': {'o1': {'source_path_id': 'W', 'center_s': 100, 'width': 30}}}}]
    designs += extra.get('more', [])
    return DesignLibrary(designs)


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------

def test_apply_derivation_semantics():
    doc = apply_derivation(base_doc(), {
        'source_paths': {'W': {'w': 300}, 'L': {'type': 'LinePath', 'start': [0, 0], 'end': [10, 0]}},
        'infills': {'I': None}},
        {'junction_style': 'round', 'material': {'contact_overlap': 1.0}})
    W = doc['source_paths'][0]
    assert W['w'] == 300 and W['h'] == 120 and W['wall'] == {'thickness': 10}   # field merge
    assert [p['id'] for p in doc['source_paths']] == ['W', 'L']                    # order + add
    assert doc['infills'] == []                                                    # remove
    assert doc['junction_style'] == 'round'
    assert doc['material'] == {'bead_width': 3.0, 'physical': True, 'contact_overlap': 1.0}
    with pytest.raises(LayerDesignError):
        apply_derivation(base_doc(), {'doors': {}}, {})
    with pytest.raises(LayerDesignError):
        apply_derivation(base_doc(), {}, {'openings': []})


def test_parent_edits_propagate_live_and_overrides_stay():
    L = lib(more=[{'id': 'tall', 'name': 'Taller', 'parent': 'base',
                   'patch': {'source_paths': {'W': {'h': 200}}}}])
    L.edit('base', document=base_doc(w=260))                   # change Base afterwards
    door = L.document('door')
    assert door['source_paths'][0]['w'] == 260                 # inherited change
    assert [o['id'] for o in door['openings']] == ['o1']       # its own difference stays
    tall = L.document('tall')
    assert tall['source_paths'][0]['w'] == 260 and tall['source_paths'][0]['h'] == 200
    assert L.document('base').get('openings') is None          # the parent is untouched


def test_multi_level_derivation_and_identity():
    L = lib(more=[{'id': 'door2', 'name': 'Door + Window', 'parent': 'door',
                   'patch': {'openings': {'o2': {'source_path_id': 'W', 'center_s': 420, 'width': 20}}}}])
    d = L.document('door2')
    assert [o['id'] for o in d['openings']] == ['o1', 'o2']
    assert L.lineage('door2') == ['door2', 'door', 'base']
    assert d['source_paths'][0]['id'] == 'W' and d['infills'][0]['id'] == 'I'   # stable ids


def test_invalid_libraries():
    with pytest.raises(LayerDesignError):
        DesignLibrary([{'id': 'x', 'name': 'X', 'parent': 'nope', 'patch': {}}])
    with pytest.raises(LayerDesignError):
        DesignLibrary([{'id': 'a', 'name': 'A', 'parent': 'b'}, {'id': 'b', 'name': 'B', 'parent': 'a'}])
    with pytest.raises(LayerDesignError):
        DesignLibrary([{'id': 'a', 'name': 'A'}])                # base without a document
    L = lib()
    assert LayerDesign.from_dict(L.get('door').to_dict()).to_dict() == L.get('door').to_dict()


# ---------------------------------------------------------------------------
# Lattice continuity
# ---------------------------------------------------------------------------

def _pts(polys):
    return {(round(q.x, 6), round(q.y, 6)) for p in polys for q in p}


def test_a_design_alone_is_the_normal_designer_build():
    """A lattice family of ONE design is planned normally. (Superseded
    2026-10-06: Base WITH variants now prints the lineage scaffold — its
    braces at the variants' end walls — see test_lineage_scaffold.py.)"""
    from app import _deserialise_layer
    built = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base_doc()}]).build('base')
    paths, meta = _deserialise_layer(base_doc())._build_effective()
    assert built.printable == _deserialise_layer(base_doc()).printable_centerlines(paths, meta)


def test_derived_design_keeps_the_parent_lattice_away_from_the_change():
    L = lib()
    base, door = L.build('base'), L.build('door')
    regen = L.build('door', inherit_lattice=False)
    bp, dp, rp = _pts(base.lattice['I']), _pts(door.lattice['I']), _pts(regen.lattice['I'])
    assert len(dp & bp) >= 0.95 * len(dp)          # inherited: the same stitches
    assert len(rp & bp) <= 0.1 * len(rp)           # regenerated: a different layout
    assert door.network['lattice']['I'].get('inherited') is True
    # nothing inside the doorway (centre of the 30 in opening on the bottom wall)
    assert all(not (185 < x < 215 and 138 < y < 152) for x, y in dp)


def test_a_lattice_override_in_a_derived_patch_is_inert():
    """Superseded 2026-10-06 ("a changed infill is regenerated"): the
    lattice DEFINITION belongs to the lineage, so a spacing stored in a
    derived patch is ignored — the design prints the lineage's shared
    lattice (an edit made in the Designer is moved to the owner, see
    test_lineage_scaffold.py)."""
    L = lib(more=[{'id': 'dense', 'name': 'Dense', 'parent': 'base',
                   'patch': {'infills': {'I': {'params': {'spacing': 12}}}}}])
    assert L.document('dense')['infills'][0]['params'] == L.document('base')['infills'][0]['params']
    assert L.build('dense').network['lattice']['I'].get('inherited') is True


def test_clip_to_rings_splits_exactly_at_the_boundary():
    ring = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
    poly = [Vec2(-5, 5), Vec2(5, 5), Vec2(15, 5)]
    out = _clip_to_rings([poly], [ring])
    assert len(out) == 1 and [(q.x, q.y) for q in out[0]] == [(0.0, 5.0), (5, 5), (10.0, 5.0)]
    assert out[0][1] is poly[1]                    # interior vertices kept unchanged


# ---------------------------------------------------------------------------
# Editing a derived design in the Designer: its delta (derive_delta)
# ---------------------------------------------------------------------------

def _canon(doc):
    from layer_design import KEYED
    out = dict(doc)
    for coll, key in KEYED.items():
        if coll in out:
            out[coll] = sorted(out[coll], key=lambda r: str(r.get(key)))
    return out


def test_derive_delta_round_trips_and_stores_only_differences():
    from layer_design import derive_delta
    parent = base_doc()
    edited = apply_derivation(parent, {
        'source_paths': {'W': {'w': 260}, 'L': {'type': 'LinePath', 'start': [0, 0], 'end': [10, 0]}},
        'openings': {'o1': {'source_path_id': 'W', 'center_s': 100, 'width': 30}}},
        {'junction_style': 'round', 'material': {'contact_overlap': 1.0}})
    patch, settings = derive_delta(parent, edited)
    assert patch['source_paths']['W'] == {'w': 260}             # only the changed field
    assert patch['openings']['o1']['width'] == 30 and 'infills' not in patch
    assert settings == {'junction_style': 'round', 'material': {'contact_overlap': 1.0}}
    assert _canon(apply_derivation(parent, patch, settings)) == _canon(edited)
    # removal
    gone = dict(parent, infills=[])
    p2, s2 = derive_delta(parent, gone)
    assert p2 == {'infills': {'I': None}} and s2 == {}
    assert derive_delta(parent, parent) == ({}, {})


def test_an_edited_derived_design_keeps_following_its_parent():
    """The delta of a derived design edited in the Designer is stored, then
    the Base changes: the derived design picks up the change."""
    from layer_design import derive_delta
    L = lib()
    edited = L.document('door')
    edited['source_paths'][0]['h'] = 140                    # the door design's own change
    patch, settings = derive_delta(L.document('base'), edited)
    L.edit('door', patch=patch, settings=settings)
    L.edit('base', document=base_doc(w=300))                 # then Base changes
    d = L.document('door')
    assert d['source_paths'][0]['w'] == 300 and d['source_paths'][0]['h'] == 140


def test_layer_design_endpoints():
    from app import app
    app.config['TESTING'] = True
    designs = [d.to_dict() for d in lib().designs.values()]
    with app.test_client() as c:
        r = c.post('/api/layer_designs/document', json={'designs': designs, 'id': 'door'}).get_json()
        assert r['lineage'] == ['door', 'base'] and r['document']['openings'][0]['id'] == 'o1'
        doc = r['document']
        doc['openings'][0]['width'] = 40
        r2 = c.post('/api/layer_designs/delta', json={'designs': designs, 'parent': 'base', 'document': doc}).get_json()
        assert r2['patch'] == {'openings': {'o1': {'source_path_id': 'W', 'center_s': 100, 'width': 40, 'id': 'o1'}}}
        bad = c.post('/api/layer_designs/document', json={'designs': designs, 'id': 'nope'})
        assert bad.status_code == 400


def test_inherited_lattice_reference_is_the_generated_original_not_a_clipped_copy():
    """A design derived from a gap design that closes the gap gets the
    ORIGINAL lattice stitches back across the gap (in phase with the base),
    not the parent's clipped copy with a hole in it."""
    L = DesignLibrary([d.to_dict() for d in lib().designs.values()] +
                      [{'id': 'closed', 'name': 'Closed', 'parent': 'door', 'patch': {'openings': {'o1': None}}}])
    base, door, closed = L.build('base'), L.build('door'), L.build('closed')
    key = lambda pls: sorted(tuple(round(c, 6) for q in pl for c in (q.x, q.y)) for pl in pls)
    assert key(closed.lattice['I']) == key(base.lattice['I'])
    # (2026-10-06: one SHARED scaffold for the whole family, not a parent copy)
    assert door.lattice_source['I'] is base.lattice_source['I'] is closed.lattice_source['I']

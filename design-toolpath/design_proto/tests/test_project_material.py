"""
BEAD WIDTH IS ONE PROJECT MATERIAL VALUE (2026-10-06): it lives in the
lineage root's (Base's) document; a derived Layer Design never stores its
own. A bead width edited while viewing a derived design is moved to the root
by /api/layer_designs/delta (material_moved) — the Designer's Material panel
and the Assembly's Physical support edit the same value.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app as flask_app


def designs():
    base = {'id': 'l', 'source_paths': [], 'offset_treatments': [], 'lattice_instances': [],
            'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base},
            {'id': 'd1', 'name': 'Gaps', 'parent': 'base', 'patch': {}, 'settings': {}},
            {'id': 'd2', 'name': 'Gaps 2', 'parent': 'd1', 'patch': {}, 'settings': {}}]


def delta(doc_mat, did='d2'):
    c = flask_app.test_client()
    ds = designs()
    doc = dict(ds[0]['document'], material={**ds[0]['document']['material'], **doc_mat})
    r = c.post('/api/layer_designs/delta', json={'designs': ds, 'parent': 'd1', 'id': did, 'document': doc})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_a_bead_width_edited_on_a_derived_design_moves_to_the_project_root():
    r = delta({'bead_width': 3.5, 'contact_overlap': 1.0})
    assert r['material_moved'] == 'base'
    assert r['settings'] == {'material': {'contact_overlap': 1.0}}       # other material fields stay per design
    out = {d['id']: d for d in r['designs']}
    assert out['base']['document']['material']['bead_width'] == 3.5
    assert all('bead_width' not in (d.get('settings') or {}).get('material', {}) for d in r['designs'] if d['parent'])


def test_no_bead_width_change_moves_nothing():
    r = delta({})
    assert r['material_moved'] is None and 'material' not in r['settings']

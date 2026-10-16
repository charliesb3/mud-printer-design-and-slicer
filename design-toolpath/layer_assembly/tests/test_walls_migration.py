"""
M2 (2026-10-15 — WALL_ARCHITECTURE.md): Wall Groups removed — the per-wall
architecture (design_proto/walls.py) and project file schema 7.

  * per-wall composition: walls of one Wall System and one envelope form one
    build record (continuous generation, as before); an unassigned thick wall
    is its own Hollow record; an unassigned thin wall is no record; the old
    `designs` scope is ignored (construction never depends on the layout
    state); an older GROUPED document is normalised (members get the group's
    envelope);
  * the migration: Base groups → member envelopes; a wall a Variant ADDED and
    grouped gets that envelope; every Variant-stored persistent property is
    dropped AND reported; Wall System group assignment → explicit members,
    `designs` removed (reported); planar cuts on groups → explicit sources;
    idempotent; the real `10xtest1` opens as schema 7 with its one divergence
    reported, and saves without any Wall Group anywhere.
Geometry identity of the whole pipeline is the golden suite's job
(test_golden.py: identical for every case but 10xtest1's reported divergence).
"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..', 'design_proto')))

import walls as W                                                   # noqa: E402

GOLDEN = os.path.join(HERE, 'golden', 'projects')


def path(pid, **kw):
    return dict({'id': pid, 'type': 'LinePath', 'label': pid, 'start': [0, 0], 'end': [10, 0]}, **kw)


ENV10 = {'thickness': 10.0, 'align': 'center', 'print_reference': False}


class TestCompose:
    def test_records_per_system_and_envelope(self):
        doc = {'source_paths': [path('A', envelope=ENV10), path('B', envelope=ENV10),
                                path('C', envelope=dict(ENV10, thickness=6.0)), path('D', envelope=ENV10),
                                path('T')]}                        # T: thin, unassigned
        systems = [{'id': 'S1', 'name': 'Waves', 'type': 'interleaved', 'params': {'paths': 3}, 'members': ['A', 'B', 'C'],
                    'designs': ['other']}]                         # (a stale scope — ignored)
        out = W.compose(doc, 'base', systems)
        R = {r['id']: r for r in out['wall_systems']}
        assert R['S1']['members'] == ['A', 'B'] and R['S1']['type'] == 'interleaved'      # one system, one envelope
        assert R['S1~2']['members'] == ['C'] and R['S1~2']['thickness'] == 6.0            # same system, other envelope
        assert R['wall:D']['type'] == 'hollow' and R['wall:D']['members'] == ['D']        # unassigned thick wall
        assert not any('T' in r['members'] for r in out['wall_systems'])                  # thin unassigned: no record
        assert 'wall_groups' not in out and W.compose(out, 'base', systems) is out        # composed → unchanged

    def test_thin_wall_listed_by_a_system_is_a_record(self):
        out = W.compose({'source_paths': [path('T')]}, None, [{'id': 'S', 'type': 'single', 'members': ['T']}])
        assert out['wall_systems'][0]['members'] == ['T'] and out['wall_systems'][0]['thickness'] == 0.0

    def test_architecture_only(self):
        out = W.compose({'source_paths': [path('A', envelope=ENV10), path('T')]}, None, None, arch=True)
        assert [(r['id'], r['type']) for r in out['wall_systems']] == [('wall:A', W.ARCH_TYPE)]

    def test_grouped_documents_are_normalised(self):
        doc = {'source_paths': [path('A'), path('B', envelope=dict(ENV10, thickness=3.0))],
               'wall_groups': [{'id': 'G', 'members': ['A', 'B'], 'thickness': 12.0, 'align': 'auto'}]}
        out = W.compose(doc, None, [])
        env = {p['id']: p.get('envelope') for p in out['source_paths']}
        assert env['A'] == env['B'] == {'thickness': 12.0, 'align': 'auto', 'print_reference': False}
        assert 'wall_groups' not in out

    def test_a_build_document_is_not_composed_again(self):
        doc = {'source_paths': [path('A')], 'wall_systems': []}
        assert W.compose(doc, None, []) is doc


class TestPreWallSystemDocuments:
    """Documents older than the Wall Systems (per-path `wall`, `network_walls`
    inheriting a thickness over a source network, the pairwise lattice,
    in-document build records) are converted ONCE at the migration boundary;
    the geometry pipeline reads only per-wall envelopes."""

    def _project(self, doc, systems=()):
        return {'designer': {'designs': [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}]},
                'wall_systems': list(systems), 'assembly': {'state': {}}}

    def test_own_path_walls_become_envelopes(self):
        doc = {'source_paths': [path('A', wall={'thickness': 10, 'align': 'inside'}),
                                path('T', wall={'thickness': 0}),            # the explicit single-bead override
                                path('L', wall={'thickness': 8, 'align': 'auto',
                                                'system': {'type': 'linked', 'paths': 4}})]}
        P = self._project(doc)
        rep = W.migrate_project(P)
        env = {p['id']: p.get('envelope') for p in doc['source_paths']}
        assert env['A'] == {'thickness': 10.0, 'align': 'inside', 'print_reference': False}
        assert env['T'] is None and env['L']['thickness'] == 8.0
        assert not any('wall' in p for p in doc['source_paths'])
        (sysr,) = P['wall_systems']
        assert sysr['type'] == 'linked' and sysr['params'] == {'paths': 4} and sysr['members'] == ['L']
        assert '_legacy_key' not in sysr and any('own wall 10' in r for r in rep)
        assert W.migrate_project(P) == []                                   # idempotent

    def test_network_walls_are_inherited_over_the_source_network(self):
        import reference_network as RN
        d = RN.document('zigzag')
        for p in d['source_paths']:
            p.pop('envelope', None)
        d['network_walls'] = [{'id': 'NW1', 'path_id': 'R1', 'thickness': 10, 'align': 'auto'}]
        far = {'id': 'X', 'type': 'LinePath', 'start': [900, 900], 'end': [980, 900]}      # touches nothing
        own = {'id': 'Y', 'type': 'LinePath', 'start': [100, 100], 'end': [100, 40], 'wall': {'thickness': 0}}
        d['source_paths'] += [far, own]
        P = self._project(d)
        W.migrate_project(P)
        env = {p['id']: (p.get('envelope') or {}).get('thickness') for p in d['source_paths']}
        assert env == {'C1': 10.0, 'R1': 10.0, 'R2': 10.0, 'L1': 10.0, 'X': None, 'Y': None}
        assert 'network_walls' not in d
        # the converted document prints exactly like the per-wall reference fixture
        from layer_design import DesignLibrary
        a = DesignLibrary([{'id': 'b', 'name': 'B', 'document': d}]).build('b').printable
        ref = RN.document('zigzag')
        ref['source_paths'] += [dict(far), {k: v for k, v in own.items() if k != 'wall'}]
        b = DesignLibrary([{'id': 'b', 'name': 'B', 'document': ref}]).build('b').printable
        assert a == b

    def test_pairwise_lattice_instances_are_dropped_and_reported(self):
        doc = {'source_paths': [path('A', envelope=ENV10)],
               'lattice_instances': [{'id': 'li', 'generator': 'zigzag', 'path_a_id': 'A', 'path_b_id': 'A',
                                      'params': {}}]}
        P = self._project(doc)
        rep = W.migrate_project(P)
        assert 'lattice_instances' not in doc and any('pairwise lattice' in r for r in rep)

    def test_in_document_build_records_split_into_envelopes_and_project_systems(self):
        doc = {'source_paths': [path('A'), path('B')],
               'wall_systems': [{'id': 'WS1', 'name': 'Rooms', 'type': 'interleaved', 'thickness': 9,
                                 'align': 'inside', 'print_reference': False, 'params': {'paths': 4},
                                 'members': ['A'], 'web': {}}]}
        P = self._project(doc)
        rep = W.migrate_project(P)
        base = P['designer']['designs'][0]['document']
        env = {p['id']: p.get('envelope') for p in base['source_paths']}
        assert env['A'] == {'thickness': 9.0, 'align': 'inside', 'print_reference': False} and env['B'] is None
        assert 'wall_systems' not in base and 'wall_groups' not in base
        (sysr,) = P['wall_systems']
        assert sysr['id'] == 'WS1' and sysr['type'] == 'interleaved' and sysr['members'] == ['A']
        assert any('In-document Wall System records' in r for r in rep)

    def test_empty_legacy_collections_are_not_written(self):
        doc = {'source_paths': [path('A', envelope=ENV10)], 'offset_treatments': [], 'lattice_instances': [],
               'network_walls': [], 'wall_groups': [], 'wall_relations': [], 'junction_overrides': []}
        P = self._project(doc)
        W.migrate_project(P)
        assert not any(k in doc for k in ('offset_treatments', 'lattice_instances', 'network_walls', 'wall_groups'))
        assert 'wall_relations' in doc and 'junction_overrides' in doc            # current features stay
        kept = {'source_paths': [path('A')], 'offset_treatments': [{'id': 'o', 'source_path_id': 'A', 'distance': 5}]}
        W.migrate_project(self._project(kept))
        assert kept['offset_treatments']                                            # a non-empty one is kept


def project(base_doc, variants=(), systems=(), profiles=()):
    designs = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base_doc}]
    designs += [dict({'id': vid, 'name': vid, 'parent': 'base'}, **v) for vid, v in variants]
    return {'designer': {'designs': designs}, 'wall_systems': list(systems),
            'assembly': {'state': {'profiles': list(profiles)}}}


class TestMigration:
    def base(self):
        return {'source_paths': [path('A'), path('B'), path('C')], 'junction_style': 'miter',
                'wall_groups': [{'id': 'G1', 'name': 'Outer', 'members': ['A', 'B'], 'thickness': 10.0, 'align': 'center',
                                 'print_reference': False}]}

    def test_groups_become_wall_envelopes(self):
        P = project(self.base())
        assert W.migrate_project(P) == []
        sp = {p['id']: p for p in P['designer']['designs'][0]['document']['source_paths']}
        assert sp['A']['envelope'] == sp['B']['envelope'] == ENV10 and 'envelope' not in sp['C']
        assert 'wall_groups' not in P['designer']['designs'][0]['document']

    def test_variant_added_walls_keep_their_envelope_divergences_dropped_and_reported(self):
        v = {'patch': {'source_paths': {'N': path('N'),                          # a wall the Variant ADDS …
                                        'A': {'corner_radius': 4, 'end': [12, 0]},   # owned field + geometry of an inherited wall
                                        'B': None},                              # a removed wall (layout state)
                       'wall_groups': {'G1': {'thickness': 6.0, 'members': ['A', 'B', 'N', 'C']}},   # … grouped; C added too
                       'junction_overrides': {'k#0': {'treatment': 'round'}},
                       'openings': {'o1': {'id': 'o1'}}},
             'settings': {'junction_style': 'round', 'junction_radius': 7, 'material': {'contact_overlap': 0.5},
                          'constraints': {'start_path_id': 'A'}}}
        P = project(self.base(), [('d1', v)])
        rep = W.migrate_project(P)
        d1 = P['designer']['designs'][1]
        sp = d1['patch']['source_paths']
        assert sp['N']['envelope'] == ENV10                     # the Variant's own wall: Base's group envelope
        assert sp['A'] == {'end': [12, 0]} and sp['B'] is None   # geometry / removal kept (layout state)
        assert 'wall_groups' not in d1['patch'] and 'junction_overrides' not in d1['patch']
        assert d1['patch']['openings'] == {'o1': {'id': 'o1'}}
        assert d1['settings'] == {'constraints': {'start_path_id': 'A'}}
        text = '\n'.join(rep)
        for frag in ('changed Wall Group G1 (thickness)', 'inherited wall C in Wall Group G1', "junction_style = 'round'",
                     'junction_radius = 7', "material = {'contact_overlap': 0.5}", 'junction override',
                     'corner_radius of inherited wall A'):
            assert frag in text, (frag, rep)

    def test_wall_systems_and_planes(self):
        systems = [{'id': 'S1', 'type': 'interleaved', 'members': ['C'], 'groups': ['G1'], 'designs': ['d1']}]
        planes = [{'kind': 'plane', 'id': 'p', 'sources': ['C'], 'systems': ['G1'], 'exclude': ['B']}]
        P = project(self.base(), systems=systems, profiles=planes)
        rep = W.migrate_project(P)
        assert P['wall_systems'] == [{'id': 'S1', 'type': 'interleaved', 'members': ['C', 'A', 'B']}]
        assert P['assembly']['state']['profiles'][0] == {'kind': 'plane', 'id': 'p', 'sources': ['C', 'A', 'B'],
                                                         'systems': [], 'exclude': ['B']}
        assert any('applied only to d1' in r for r in rep)

    def test_plane_keeps_selectors_that_are_not_wall_groups(self):
        """(regression) a planar cut on a LEGACY document's own Wall System
        record (`systems: ['WS1']`) is still a valid selector — only Wall Group
        ids become explicit walls."""
        planes = [{'kind': 'plane', 'id': 'p', 'sources': [], 'systems': ['WS1', 'G1'], 'exclude': []}]
        P = project(self.base(), profiles=planes)
        W.migrate_project(P)
        p = P['assembly']['state']['profiles'][0]
        assert p['systems'] == ['WS1'] and p['sources'] == ['A', 'B']

    def test_idempotent(self):
        P = project(self.base(), [('d1', {'patch': {}, 'settings': {'cap_style': 'flat'}})])
        assert W.migrate_project(P)
        once = copy.deepcopy(P)
        assert W.migrate_project(P) == [] and P == once


class TestProjectFile:
    def test_real_10xtest1_opens_as_schema_7_and_saves_without_groups(self):
        import project_file as PF
        with open(os.path.join(GOLDEN, 'real_10xtest1.muddesign')) as f:
            r = PF.load(f.read())
        assert r['schema_version'] == 7 and r['migrated_from'] == 6
        assert any("junction_style = 'round'" in w for w in r['warnings'])
        P = r['project']
        saved = PF.save(P)
        text = json.dumps(saved)
        assert '"wall_groups"' not in text and '"groups"' not in text
        assert all('designs' not in w and 'groups' not in w for w in saved['project']['wall_systems'])
        base = P['designer']['designs'][0]['document']
        assert {p['id']: (p.get('envelope') or {}).get('thickness') for p in base['source_paths']} == \
            {'p1': 6.0, 'p2': 6.0, 'p5': None}
        assert P['wall_systems'][0]['members'] == ['p1', 'p2']
        again = PF.load(PF.dumps(PF.save(P)))
        assert not [w for w in again['warnings'] if 'dropped' in w]      # clean once migrated

    def test_every_golden_project_migrates_cleanly(self):
        import glob
        import project_file as PF
        for f in glob.glob(os.path.join(GOLDEN, '*.muddesign')):
            with open(f) as fh:
                P = PF.load(fh.read())['project']
            text = json.dumps(P)
            assert '"wall_groups"' not in text and '"groups":' not in text, f

"""
Assembly core: physical-height intent → layer instances. Pure: no Designer
import (sources are faked). These tests are the Assembly's own; they run in
well under a second and never need the Designer suites.
"""
import pytest

from layer_assembly.model import Assembly, Section, resolve, AssemblyError


def proto(lh):
    return Assembly(lh, [Section('A', 36, 'Base'), Section('B', 24, 'Door'),
                         Section('A', 12, 'Base'), Section('C', 36, 'Window')])


def test_prototype_at_1_5():
    r = resolve(proto(1.5), {'A', 'B', 'C'})
    assert [s.layers for s in r.sections] == [24, 16, 8, 24]
    assert [s.actual_height for s in r.sections] == [36, 24, 12, 36]
    assert r.total_layers == 72 and r.total_height == 108 and r.warnings == []


def test_changing_layer_height_reresolves_the_same_intent():
    a = proto(1.5)
    r2 = resolve(Assembly(2.0, a.sections))
    assert [s.layers for s in r2.sections] == [18, 12, 6, 18]
    assert r2.total_layers == 54 and r2.total_height == 108
    assert [s.desired_height for s in r2.sections] == [36, 24, 12, 36]     # intent kept


def test_inexact_heights_round_at_boundaries_and_report_the_error():
    r = resolve(proto(1.75))
    assert [s.layers for s in r.sections] == [21, 13, 7, 21]
    assert [round(s.error, 6) for s in r.sections] == [0.75, -1.25, 0.25, 0.75]
    # every boundary within half a layer of its intended Z; never accumulates
    cum = 0
    for s in r.sections:
        cum += s.desired_height
        assert abs(s.z_top - cum) <= 1.75 / 2 + 1e-9
    assert abs(r.total_height - 108) <= 1.75 / 2


def test_instances_reference_designs_and_stack_without_gaps():
    r = resolve(proto(1.5))
    assert [i.index for i in r.instances] == list(range(72))
    assert all(a.z_top == b.z_bottom for a, b in zip(r.instances, r.instances[1:]))
    assert all(abs(i.height - 1.5) < 1e-12 for i in r.instances)
    a_layers = [i for i in r.instances if i.design_id == 'A']
    assert len(a_layers) == 32 and {i.section_index for i in a_layers} == {0, 2}   # A reused
    assert r.designs_used() == ['A', 'B', 'C']
    s1 = r.sections[1]
    assert s1.first_layer == 24 and s1.z_bottom == 36 and s1.z_top == 60


def test_whole_layer_transitions():
    r = resolve(proto(1.5))
    for s in r.sections:
        ids = {i.design_id for i in r.instances[s.first_layer:s.first_layer + s.layers]}
        assert ids == {s.design_id}


def test_a_section_that_rounds_to_zero_layers_is_reported():
    r = resolve(Assembly(2.0, [Section('A', 10), Section('B', 0.5), Section('A', 10)]))
    assert [s.layers for s in r.sections] == [5, 0, 5]
    assert r.sections[1].first_layer is None and r.warnings


def test_validation():
    with pytest.raises(AssemblyError):
        resolve(Assembly(0, [Section('A', 10)]))
    with pytest.raises(AssemblyError):
        resolve(Assembly(1.5, [Section('A', -1)]))
    with pytest.raises(AssemblyError):
        resolve(Assembly(1.5, [Section('X', 10)]), {'A'})


def test_round_trip_dict():
    a = proto(1.5)
    assert Assembly.from_dict(a.to_dict()).to_dict() == a.to_dict()

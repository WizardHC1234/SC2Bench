"""Larva hatching uses actual tech, without a fictitious living-base gate."""
import pytest
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


@pytest.mark.parametrize('target',['drone','overlord','zergling','roach'])
def test_larva_hatching_remains_eligible_after_base_loss_when_tech_remains(target):
    macro=ActOngoingMacroTasks([],race='zerg')
    macro._count_ready=lambda name: 0 if name in {'hatchery','lair','hive'} else 1
    task={'action':'train','target':target}
    assert not macro._prereq_blocked(task)


def test_larva_hatching_keeps_its_real_technology_requirement():
    macro=ActOngoingMacroTasks([],race='zerg')
    macro._count_ready=lambda _:0
    task={'action':'train','target':'roach'}
    assert macro._prereq_blocked(task)
    assert task['_waiting_for']=='prerequisite:roach_warren'


def test_queen_still_requires_a_townhall_to_train():
    macro=ActOngoingMacroTasks([],race='zerg')
    macro._count_ready=lambda _:0
    task={'action':'train','target':'queen'}
    assert macro._prereq_blocked(task)
    assert task['_waiting_for']=='prerequisite:hatchery'

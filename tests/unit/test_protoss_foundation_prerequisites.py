"""Pylon power does not replace the native Nexus requirement for Gateway/Forge."""
from types import SimpleNamespace as NS
import pytest
from sc2bench_env.catalog.registry import get_target
from sc2bench_env.runtime.observation_builder import available_targets
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


@pytest.mark.parametrize('target',['gateway','forge'])
def test_nexus_loss_removes_foundation_targets_and_blocks_execution(target):
    available=available_targets(race='protoss',building={'pylon':{'completed':1}})
    assert target not in available['build']
    assert 'pylon' in available['build'] and 'nexus' in available['build']
    macro=ActOngoingMacroTasks([],race='protoss')
    macro._count_ready=lambda name:1 if name=='pylon' else 0
    task={'action':'build','target':target}
    assert macro._prereq_blocked(task)
    assert task['_waiting_for']=='prerequisite:nexus'
    macro._count_ready=lambda name:1 if name=='nexus' else 0
    assert not macro._prereq_blocked(task)
    assert 'nexus' in get_target(target,race='protoss').prerequisites

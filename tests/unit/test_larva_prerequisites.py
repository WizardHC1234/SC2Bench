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


def test_observed_larva_keeps_hatching_options_after_all_townhalls_are_lost():
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.runtime.observation_builder import build_observation
    from sc2bench_env.runtime.task_manager import TaskManager
    snapshot = BackendSnapshot(units={"drone": 2}, buildings={"spawning_pool": 1}, info={
        "production": [{"facility": "larva", "ready_grounded": 1, "capacity": 1,
                        "occupied_slots": 0, "free_slots": 1}],
    })
    obs = build_observation(snapshot, TaskManager(race="zerg"), EpisodeConfig(race="zerg"))
    assert {"drone", "overlord", "zergling"} <= set(obs.available_targets["train"])
    assert "queen" not in obs.available_targets["train"]
    assert "roach" not in obs.available_targets["train"]


def test_no_base_and_no_larva_does_not_advertise_hatching():
    from sc2bench_env.runtime.observation_builder import available_targets
    result = available_targets(race="zerg", production=[{"facility": "larva", "ready_grounded": 0}])
    assert not {"drone", "overlord", "zergling", "queen"}.intersection(result["train"])

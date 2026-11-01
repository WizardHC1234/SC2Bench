"""Tumor existence is not the same as an available spread ability."""
from types import SimpleNamespace as NS
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.races.zerg_production import read_ability_facts
from sc2bench_env.backends.sharpy.races.zerg_execution import ability_task_state
from sc2bench_env.runtime.task import DemandState


def scene(abilities):
    tumors=[NS(tag=1,type_id=U.CREEPTUMORBURROWED,is_ready=True),
            NS(tag=2,type_id=U.CREEPTUMORBURROWED,is_ready=True)]
    return NS(structures=tumors,units=lambda _:NS(ready=[]),townhalls=NS(ready=[]),
              knowledge=NS(cooldown_manager=NS(available_dict=abilities)))


def test_spent_or_cooling_tumor_is_not_reported_as_ready():
    facts=read_ability_facts(scene({1:[A.BUILD_CREEPTUMOR_TUMOR],2:[]}),{},None)
    assert facts['spawn_creep_tumor_ready']==1


def test_missing_ability_observation_is_unknown():
    facts=read_ability_facts(scene({1:[A.BUILD_CREEPTUMOR_TUMOR]}),{},None)
    assert facts['spawn_creep_tumor_ready'] is None


def test_free_tumor_feedback_does_not_claim_a_queen_is_required():
    snapshot=BackendSnapshot(info={'spawn_creep_tumor_ready':1})
    assert ability_task_state('spawn_creep_tumor',snapshot,None)==(DemandState.IN_PROGRESS,None)

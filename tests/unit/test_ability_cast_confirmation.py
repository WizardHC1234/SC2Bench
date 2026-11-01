"""Queued/rejected/moving spell commands must not masquerade as cast effects."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.effect_id import EffectId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2bench_env.backends.sharpy.acts import ActSupplyDrop, ActSpawnCreepTumor
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.interface.platform_prompt import default_prompt_parts


def setup():
    caster = MagicMock(tag=7, energy=100)
    caster.orders = []
    target = NS(tag=8, position=Point2((10, 10)), buffs=[])
    act = ActSupplyDrop()
    act._done, act.failure_reason = False, None
    act.ai = NS(time=0, all_own_units=[caster, target], unit_tags_received_action=set(),
                state=NS(effects=[], action_errors=[]))
    return act, caster, target


@pytest.mark.parametrize("ability,buff", [
    (AbilityId.SUPPLYDROP_SUPPLYDROP, BuffId.SUPPLYDROP),
    (AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, BuffId.CHRONOBOOSTENERGYCOST),
    (AbilityId.EFFECT_INJECTLARVA, BuffId.QUEENSPAWNLARVATIMER),
])
def test_target_buff_confirms_cast_after_later_observation(ability, buff):
    act, caster, target = setup()
    assert not act._queue_cast(caster, ability, target, buff=buff)
    assert not act._done and not act._observe_cast()
    act.ai.time = 0.5
    assert act._observe_cast() is False
    target.buffs = [buff]
    assert act._observe_cast() is True and act._done
    caster.assert_called_once()


def test_walking_inject_order_stays_pending_without_duplicate_commands():
    act, caster, target = setup()
    act._queue_cast(caster, AbilityId.EFFECT_INJECTLARVA, target, buff=BuffId.QUEENSPAWNLARVATIMER)
    caster.orders = [NS(ability=NS(id=AbilityId.EFFECT_INJECTLARVA))]
    act.ai.time = 8
    assert act._observe_cast() is False and act._pending_cast is not None
    assert caster.tag in act.ai.unit_tags_received_action
    assert not act._done


def test_scan_needs_effect_and_energy_debit_not_just_energy_loss():
    act, caster, target = setup()
    act._queue_cast(caster, AbilityId.SCANNERSWEEP_SCAN, target.position, effect=EffectId.SCANNERSWEEP)
    act.ai.time = 0.5
    caster.energy = 50
    assert act._observe_cast() is False
    act.ai.state.effects = [NS(id=EffectId.SCANNERSWEEP, positions=[target.position])]
    assert act._observe_cast() is True


@pytest.mark.parametrize("unit_type", [UnitTypeId.MULE, UnitTypeId.CREEPTUMORQUEEN, UnitTypeId.CREEPTUMOR])
def test_only_new_entities_near_target_count_as_cast_effect(unit_type):
    act, caster, target = setup()
    caster.type_id = unit_type
    act._queue_cast(caster, AbilityId.BUILD_CREEPTUMOR_QUEEN, target.position, unit_types=(unit_type,))
    act.ai.time = 0.5
    assert act._observe_cast() is False  # an existing entity of this type is not a new cast
    entity = NS(tag=9, type_id=unit_type, distance_to=lambda _: 10)
    act.ai.all_own_units.append(entity)
    assert act._observe_cast() is False
    entity.distance_to = lambda _: 0
    assert act._observe_cast() is True


def test_native_rejection_clears_pending_without_false_completion():
    act, caster, target = setup()
    ability = AbilityId.SUPPLYDROP_SUPPLYDROP
    act._queue_cast(caster, ability, target, buff=BuffId.SUPPLYDROP)
    act.ai.time = 0.5
    act.ai.state.action_errors = [NS(unit_tag=caster.tag, ability_id=ability.value)]
    target.buffs = [BuffId.SUPPLYDROP]
    assert act._observe_cast() is False and act._pending_cast is None and not act._done


def test_unconfirmed_dropped_order_is_failed_without_replaying_or_claiming_a_cast():
    act, caster, target = setup()
    act._queue_cast(caster, AbilityId.SUPPLYDROP_SUPPLYDROP, target, buff=BuffId.SUPPLYDROP)
    act.ai.time = 5.5
    assert act._observe_cast() is True and act._pending_cast is None and not act._done
    assert act.failure_reason == "cast_effect_not_observed"


@pytest.mark.parametrize('ability,buff',[
    (AbilityId.EFFECT_INJECTLARVA,BuffId.QUEENSPAWNLARVATIMER),
    (AbilityId.EFFECT_CHRONOBOOSTENERGYCOST,BuffId.CHRONOBOOSTENERGYCOST),
    (AbilityId.SUPPLYDROP_SUPPLYDROP,BuffId.SUPPLYDROP)])
def test_destroyed_own_spell_target_is_reported_as_lost_before_confirmation(ability,buff):
    act,caster,target=setup()
    act._queue_cast(caster,ability,target,buff=buff)
    act.ai.time=.5
    act.ai.all_own_units=[caster]
    assert act._observe_cast() is True and act._pending_cast is None and not act._done
    assert act.failure_reason=='target_lost_before_cast_confirmation'


def test_actual_combat_interruption_is_distinguished_from_an_unexplained_drop():
    act,caster,target=setup()
    act._queue_cast(caster,AbilityId.EFFECT_INJECTLARVA,target,buff=BuffId.QUEENSPAWNLARVATIMER)
    act._pending_cast['interrupted_by_combat']=True
    act.ai.time=6
    assert act._observe_cast() is True and not act._done
    assert act.failure_reason=='cast_interrupted_by_combat'


def test_pending_cast_is_checked_even_after_energy_spent_without_claiming_payment():
    act, caster, target = setup()
    act._queue_cast(caster, AbilityId.SUPPLYDROP_SUPPLYDROP, target, buff=BuffId.SUPPLYDROP)
    macro = ActOngoingMacroTasks([])
    macro.ai = NS(minerals=0, vespene=0)
    task = {"action": "supply_drop", "_act": act}
    assert macro._resource_committed(task)
    assert not macro._should_hold_budget(task, 1)
    macro._mark_paid_if_spent(task, 0, 0)
    assert not task.get("_resource_committed")


def test_exhausted_tumor_does_not_prevent_an_available_queen_from_spreading(monkeypatch):
    import sc2bench_env.backends.sharpy.acts as acts
    tumor = NS(tag=1, position=Point2((10, 10)))
    queen = NS(tag=2, position=Point2((10, 10)), energy=25)
    act = ActSpawnCreepTumor()
    act.ai = NS(time=0, unit_tags_received_action=set(), get_available_abilities=AsyncMock(return_value=[]),
                structures=lambda _: NS(ready=[tumor]), units=lambda _: NS(ready=[queen]))
    act._ahead = AsyncMock(return_value=Point2((10,10)))
    act._queue_cast = MagicMock(return_value=False)
    monkeypatch.setattr(acts, "unit_available_for_background", lambda *a, **kw: True)
    assert asyncio.run(act.execute()) is False
    act.ai.get_available_abilities.assert_awaited_once_with(tumor)
    assert act._queue_cast.call_args.args[:2] == (queen, AbilityId.BUILD_CREEPTUMOR_QUEEN)


def test_free_tumor_spread_does_not_wait_for_a_queen_or_energy():
    tumor = NS(tag=1, position=Point2((10, 10)))
    act = ActSpawnCreepTumor()
    act.ai = NS(time=0, minerals=0, vespene=0, supply_cap=10, supply_used=10,
                unit_tags_received_action=set(), get_available_abilities=AsyncMock(return_value=[AbilityId.BUILD_CREEPTUMOR_TUMOR]),
                structures=lambda _: NS(ready=[tumor]), units=lambda _: NS(ready=[]))
    act._ahead = AsyncMock(return_value=Point2((10,10)))
    act._queue_cast = MagicMock(return_value=False)
    task = {"action": "spawn_creep_tumor", "target": "", "_act": act, "_started": True}
    macro = ActOngoingMacroTasks([task], race="zerg")
    macro.ai = act.ai
    macro._count_ready = lambda _: 0
    asyncio.run(macro.execute())
    assert act.energy_cost == 0
    act._queue_cast.assert_called_once()
    assert act._queue_cast.call_args.args[:2] == (tumor, AbilityId.BUILD_CREEPTUMOR_TUMOR)


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_common_observation_guide_explains_actual_record_ambiguities(race):
    guide = default_prompt_parts(race).observation_rules
    assert "supply reserved for units in production" in guide
    assert "assignment/saturation" in guide and "under construction" in guide

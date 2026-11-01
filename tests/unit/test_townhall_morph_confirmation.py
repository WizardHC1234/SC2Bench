"""A submitted upgrade command is not evidence that SC2 started the morph."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2bench_env.backends.sharpy.acts import ActMorphTownhall
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


ABILITY = AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND


def setup():
    unit = MagicMock(tag=7, type_id=UnitTypeId.COMMANDCENTER, is_ready=True)
    unit.orders = []
    structures = MagicMock()
    structures.find_by_tag.return_value = unit
    structures.return_value.ready.amount = 1
    ai = NS(structure_registry=NS(tag_for_id=lambda _: 7), structures=structures,
            unit_tags_received_action=set(), get_available_abilities=AsyncMock(return_value=[ABILITY]))
    act = ActMorphTownhall("townhall_0", "orbital_command")
    act.ai = ai
    return act, unit, ai


def test_issuing_a_command_does_not_complete_the_demand():
    act, unit, ai = setup()
    assert asyncio.run(act.execute()) is False
    unit.assert_called_once_with(ABILITY)
    assert not act._done and not act.has_observed_start()
    # A rejected command remains retryable instead of disappearing from the queue.
    assert asyncio.run(act.execute()) is False
    assert unit.call_count == 2


def test_native_order_confirms_start_without_reissuing_command():
    act, unit, ai = setup()
    unit.orders = [NS(ability=NS(id=ABILITY))]
    assert act.has_observed_start()
    assert asyncio.run(act.execute()) is True
    assert act._done
    unit.assert_not_called()
    ai.get_available_abilities.assert_not_awaited()


def test_busy_or_already_used_source_is_not_overwritten():
    act, unit, ai = setup()
    unit.orders = [NS(ability=NS(id=AbilityId.COMMANDCENTERTRAIN_SCV))]
    assert asyncio.run(act.execute()) is False
    unit.assert_not_called()
    unit.orders = []
    ai.unit_tags_received_action.add(7)
    assert asyncio.run(act.execute()) is False
    unit.assert_not_called()


def test_unavailable_ability_waits_and_ready_result_confirms_start():
    act, unit, ai = setup()
    ai.get_available_abilities.return_value = []
    assert asyncio.run(act.execute()) is False
    unit.assert_not_called()
    unit.type_id = UnitTypeId.ORBITALCOMMAND
    assert asyncio.run(act.execute()) is True
    unit.assert_not_called()


def test_observed_morph_bypasses_funding_check_and_does_not_reserve_twice():
    act, unit, ai = setup()
    macro = ActOngoingMacroTasks([])
    macro.ai = ai
    task = {"action": "morph_townhall", "target": "townhall_0", "to": "orbital_command", "_act": act}
    assert not macro._resource_committed(task)
    assert macro._should_hold_budget(task, 1)
    unit.orders = [NS(ability=NS(id=ABILITY))]
    assert macro._resource_committed(task)
    assert not macro._should_hold_budget(task, 1)

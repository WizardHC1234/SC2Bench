"""Execute the real mission controller; transport is not a high-level style."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units

from tests.unit.test_combat_execution import UnitStub as BaseStub, mission
from sc2bench_env.backends.sharpy.bot import BenchBot
from sc2bench_env.interface.action_catalog import COMBAT_STYLES, decision_json_schema, render_system_prompt
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.observations import render_observation_text


class UnitStub(BaseStub):
    @property
    def _proto(self):
        # python-sc2 Units.center uses protocol coordinates, not Unit.position.
        return NS(pos=NS(x=self.position.x, y=self.position.y))


def controller(style='attack', own=(), enemies=(), target=(100, 0)):
    act = mission(style, own=own, enemies=enemies)
    act.units = {}
    for unit in own:
        name = act._platform_name(unit.type_id)
        act.units[name] = act.units.get(name, 0) + 1
    act._bound = True
    act._tags = [unit.tag for unit in own]
    act._resolve_target = lambda: Point2(target)
    act._resolve_zone = lambda: (None, None)
    act.driven = []
    act.combat = NS(add_unit=lambda unit: act.driven.append(unit.tag), execute=lambda *args: None)
    return act


def frame(act, time=0):
    act.ai.time = time
    return asyncio.run(act.execute())


def carrier(tag=1, position=(0, 0), **kwargs):
    return UnitStub(tag, UnitTypeId.MEDIVAC, position=position, flying=True, **kwargs)


@pytest.mark.parametrize('style', ['attack', 'defend'])
def test_far_safe_travel_loads_without_transport_field(style):
    med, marine = carrier(), UnitStub(2)
    act = controller(style, own=[med, marine])
    assert not frame(act)
    assert act.phase == 'load'
    assert med.commands == [(AbilityId.LOAD_MEDIVAC, marine)]
    assert not act.driven  # combat micro must not overwrite loading


@pytest.mark.parametrize('old', ['harass', 'pressure', 'assault', 'contain', 'retreat'])
def test_old_styles_and_combat_retreat_are_not_public_styles(old):
    assert COMBAT_STYLES == ('attack', 'defend')
    batch = [{'action': 'combat', 'style': old, 'target': 'zone_1', 'units': {'marine': 1}}, {'action': 'wait'}]
    with pytest.raises(ActionValidationError):
        parse_decision(batch)
    combat_schemas = [entry for entry in decision_json_schema()['items']['oneOf']
                      if 'style' in entry.get('properties', {})]
    assert combat_schemas
    assert all(entry['properties']['style']['enum'] == ['attack', 'defend'] for entry in combat_schemas)


def test_transport_field_is_not_an_agent_operation():
    batch = [{'action': 'combat', 'style': 'attack', 'target': 'zone_1',
              'units': {'marine': 1}, 'transport': True}, {'action': 'wait'}]
    with pytest.raises(ActionValidationError):
        parse_decision(batch)
    assert parse_decision([{'action': 'retreat', 'group': 'group_1'}, {'action': 'wait'}]).actions[0].action == 'retreat'


def test_near_objective_does_not_load_or_prevent_normal_combat():
    med = carrier()
    act = controller(own=[med, UnitStub(2)], target=(10, 0))
    frame(act)
    assert not med.commands and act.phase == 'fight'
    assert act.driven == [1, 2]


def test_visible_weapon_contact_uses_combat_instead_of_loading():
    med = carrier()
    act = controller(own=[med, UnitStub(2)], enemies=[UnitStub(90, position=(10, 0))])
    frame(act)
    assert not med.commands and act.driven == [1, 2]


def test_fog_memory_does_not_invent_contact_and_prevent_transport():
    med = carrier()
    act = controller(own=[med, UnitStub(2)], enemies=[UnitStub(90, is_memory=True)])
    frame(act)
    assert med.commands[0][0] == AbilityId.LOAD_MEDIVAC


def test_contact_during_loading_aborts_and_cannot_reload_loop():
    med = carrier()
    act = controller(own=[med, UnitStub(2)])
    frame(act)
    med.commands.clear()
    act.ai.enemy_units = Units([UnitStub(90, position=(10, 0))], act.ai)
    frame(act, 1)
    assert act.phase == 'fight' and not med.commands
    act.ai.enemy_units = Units([], act.ai)
    frame(act, 2)
    assert act.phase == 'fight' and not med.commands


def loaded_controller(*, position=(0, 0), leftovers=(), target=(100, 0)):
    passenger = UnitStub(2)
    med = carrier(position=position, passengers=[passenger], cargo_used=1)
    act = controller(own=[med, *leftovers], target=target)
    act.units['marine'] = act.units.get('marine', 0) + 1
    act._tags.append(2)
    act.phase = 'transit'
    act._transport_attempted = True
    return act, med, passenger


def test_loaded_carrier_transits_while_ground_leftovers_keep_combat_control():
    act, med, _ = loaded_controller(leftovers=[UnitStub(3), UnitStub(4, UnitTypeId.SIEGETANK)])
    frame(act)
    assert med.commands[0][0] == 'move'
    assert act.driven == [3, 4]
    assert act.loaded_counts == {'marine': 1}
    assert act.alive_counts == {'medivac': 1, 'marine': 2, 'siege_tank': 1}


def test_contact_during_transit_unloads_in_place_instead_of_diving_to_goal():
    act, med, _ = loaded_controller()
    act.ai.enemy_units = Units([UnitStub(90, position=(10, 0), air_weapon=True)], act.ai)
    frame(act)
    assert act.phase == 'unload'
    assert med.commands == [(AbilityId.UNLOADALLAT_MEDIVAC, med.position)]


def test_transport_arrival_unloads_then_fights_without_reloading():
    act, med, passenger = loaded_controller(position=(92, 0))
    frame(act)
    assert act.phase == 'unload'
    frame(act, 1)
    assert med.commands[-1][0] == AbilityId.UNLOADALLAT_MEDIVAC
    med.passengers, med.cargo_used = [], 0
    passenger.position = med.position
    act.ai.units = Units([med, passenger], act.ai)
    frame(act, 2)
    assert act.phase == 'fight' and act.loaded_counts == {}
    assert act.drop_unloaded
    before = len(med.commands)
    frame(act, 3)
    assert len(med.commands) == before
    assert set(act.driven) == {1, 2}


def test_delayed_load_result_cannot_trap_cargo_in_support_branch():
    act, med, _ = loaded_controller()
    act.phase = 'fight'
    frame(act)
    assert act.phase == 'transit'
    assert med.commands[-1][0] == 'move'


def test_failed_loading_falls_back_without_permanent_stall():
    med = carrier()
    act = controller(own=[med, UnitStub(2)])
    frame(act)
    frame(act, 10)
    assert act.phase == 'fight'
    med.commands.clear()
    frame(act, 11)
    assert not med.commands and set(act.driven) == {1, 2}


def test_transport_death_does_not_keep_passengers_alive_or_steal_another_group():
    act, med, _ = loaded_controller()
    frame(act)
    act.ai.bench_combat_tags = {1, 2, 99}
    act.ai.units = Units([carrier(99)], act.ai)
    assert frame(act, 1)
    assert act.end_reason == 'force_destroyed'
    assert act.loaded_counts == {} and act.ai.bench_combat_tags == {99}


def test_cargo_transition_frame_does_not_double_count_or_control_passenger():
    act, med, passenger = loaded_controller()
    act.ai.units = Units([med, passenger], act.ai)
    frame(act)
    assert act.alive_counts['marine'] == 1 and act.loaded_counts['marine'] == 1
    assert passenger.tag not in act.driven
    assert set(act._tags) == {1, 2}


def test_retarget_preserves_cargo_but_changes_transport_destination():
    act, med, _ = loaded_controller()
    frame(act)
    act.update_order('defend', 'zone_2', False, 1)
    act._resolve_target = lambda: Point2((80, 80))
    med.commands.clear()
    frame(act, 1)
    assert med.commands[-1] == ('move', Point2((80, 80)))
    assert set(act._tags) == {1, 2}


def test_loaded_retarget_then_contact_unload_keeps_same_members_and_counts():
    act, med, passenger = loaded_controller(position=(30, 0))
    act.ai.bench_combat_tags = {1, 2, 99}
    frame(act)
    act.update_order('defend', 'zone_2', False, 1)
    act._resolve_target = lambda: Point2((80, 80))
    frame(act, 1)
    assert med.commands[-1] == ('move', Point2((80, 80)))
    assert act.loaded_counts == {'marine': 1}

    act.ai.enemy_units = Units([UnitStub(90, position=(35, 0), air_weapon=True)], act.ai)
    frame(act, 2)
    assert med.commands[-1] == (AbilityId.UNLOADALLAT_MEDIVAC, med.position)
    assert passenger.tag not in act.driven
    assert act.ai.bench_combat_tags == {1, 2, 99}

    # The unload result is observed, not inferred from the issued ability.
    med.passengers, med.cargo_used = [], 0
    passenger.position = med.position
    act.ai.units = Units([med, passenger], act.ai)
    frame(act, 3)
    assert act.phase == 'fight' and act.drop_unloaded
    assert act.loaded_counts == {} and act.alive_counts == {'medivac': 1, 'marine': 1}
    frame(act, 4)
    assert set(act.driven) == {1, 2}
    assert set(act._tags) == {1, 2}


def test_loaded_return_releases_only_after_unload_and_can_bind_again():
    act, med, passenger = loaded_controller(position=(30, 0))
    act.ai.bench_combat_tags = {1, 2, 99}
    act.ai.bench_group0_tags = set()
    frame(act)
    act.update_order('attack', 'zone_1', True, 1)
    assert not frame(act, 1)
    assert act.loaded_counts == {'marine': 1} and not act.ai.bench_group0_tags

    med.position = Point2((0, 0))
    passenger.position = med.position
    # Even a transitional on-map passenger must not be released while loaded.
    act.ai.units = Units([med, passenger, UnitStub(99)], act.ai)
    assert not frame(act, 2)
    assert med.commands[-1] == (AbilityId.UNLOADALLAT_MEDIVAC, med.position)
    assert act.ai.bench_combat_tags == {1, 2, 99}
    assert not act.ai.bench_group0_tags

    med.passengers, med.cargo_used = [], 0
    assert frame(act, 3)
    assert act.end_reason == 'withdrawn' and act.loaded_counts == {}
    assert act.ai.bench_group0_tags == {1, 2}
    assert act.ai.bench_combat_tags == {99}

    again = controller(own=[med, passenger, UnitStub(99)])
    again.ai = act.ai
    again.units = {'marine': 1, 'medivac': 1}
    again._bound, again._tags = False, []
    assert not frame(again, 4)
    assert set(again._tags) == {1, 2}
    assert again.ai.bench_combat_tags == {1, 2, 99}
    assert again.ai.bench_group0_tags == set()
    assert again.alive_counts == {'marine': 1, 'medivac': 1}


def test_withdraw_local_pickup_is_bounded_and_never_returns_for_distant_troops():
    med = carrier(position=(50, 0))
    near, far = UnitStub(2, position=(51, 0)), UnitStub(3, position=(80, 0))
    act = controller(own=[med, near, far])
    act.phase = 'withdrawing'
    frame(act)
    assert med.commands == [(AbilityId.LOAD_MEDIVAC, near)]
    assert act.driven == [3] and act.transport_activity == 'pickup'
    med.commands.clear()
    frame(act, 3)
    assert not med.commands and set(act.driven) == {1, 2, 3}


def test_withdraw_under_contact_only_picks_up_nearby_wounded_infantry():
    med = carrier(position=(50, 0))
    wounded = UnitStub(2, position=(51, 0), health_percentage=.2)
    healthy = UnitStub(3, position=(51, 0), health_percentage=1)
    act = controller(own=[med, wounded, healthy], enemies=[UnitStub(90, position=(52, 0))])
    act.phase = 'withdrawing'
    frame(act)
    assert med.commands == [(AbilityId.LOAD_MEDIVAC, wounded)]
    assert act.driven == [3]


def test_progress_and_text_report_current_cargo_and_controller_activity():
    act, med, _ = loaded_controller()
    frame(act)
    bot = NS(_macro_tasks=[{'action': 'combat', 'task_id': 'internal', '_act': act,
                           'style': 'attack', 'target': 'zone_1', 'units': act.units}],
             units=act.ai.units, enemy_units=[], enemy_structures=[], zone_registry=None)
    row = BenchBot._collect_combat_progress(bot)['internal']
    assert row['transport']['loaded_units'] == {'marine': 1}
    assert row['transport']['activity'] == 'transit'
    text = render_observation_text({'combat': {'group_1': row}})
    assert 'currently loaded: marine 1' in text
    assert 'Transport activity: transit' in text
    assert 'internal' not in text
    assert 'no transport field' in render_system_prompt()

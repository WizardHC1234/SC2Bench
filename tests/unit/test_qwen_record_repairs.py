"""Regressions from the Qwen3.8-27B batch of 2026-11-05."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sharpy.combat import Action, NoAction, MoveType
from sharpy.knowledges import KnowledgeBot
from sc2bench_env.backends.sharpy.bot import BenchBot
from sc2bench_env.backends.sharpy.micro import _form_change_pending
from sc2bench_env.backends.sharpy.terran_micro import MicroLiberatorSafe
from sc2bench_env.interface.actions import parse_decision, ActionValidationError
from sc2bench_env.interface.observation_text import brief_priority_lines
from tests.unit.test_combat_execution import UnitStub
from tests.unit.test_terran_micro import configure, enemy


def test_deployed_liberator_keeps_autofire_instead_of_attack_move():
    micro = configure(MicroLiberatorSafe(), [enemy(position=(5, 0))])
    unit = UnitStub(1, U.LIBERATORAG, flying=True)
    for attack in (True, False):
        assert isinstance(micro.unit_solve_combat(unit, Action(Point2((20, 0)), attack)), NoAction)


def test_deployed_liberator_releases_after_ground_contact_ends():
    micro = configure(MicroLiberatorSafe(), [enemy(flying=True)])
    result = micro.unit_solve_combat(UnitStub(1, U.LIBERATORAG), Action(Point2((20, 0)), True))
    assert result.ability == A.MORPH_LIBERATORAAMODE


def test_liberator_keeps_firing_at_a_unit_overlapping_circle_edge():
    target = enemy(position=(10.2, 0), radius=.5625)
    micro = configure(MicroLiberatorSafe(), [target])
    micro.deployed_points[1] = Point2((4.9, 0))
    assert isinstance(micro.unit_solve_combat(UnitStub(1, U.LIBERATORAG), Action(None, False)), NoAction)


def test_station_guard_preserves_liberator_autofire_even_before_station_arrival():
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules
    rules = MissionMicroRules()
    rules.boundary = lambda point: True
    rules.return_point = Point2((20, 0))
    rules.hold_position = True
    command = NoAction()
    assert rules.guard_action(UnitStub(1, U.LIBERATORAG, flying=True), command) is command


def test_liberator_deploy_point_is_inside_cast_range(monkeypatch):
    from sharpy.combat.terran import MicroLiberators
    proposed = Action(Point2((10, 0)), False, A.MORPH_LIBERATORAGMODE)
    monkeypatch.setattr(MicroLiberators, 'final_solve', lambda *args: proposed)
    micro = configure(MicroLiberatorSafe(), ready=[A.MORPH_LIBERATORAGMODE])
    unit = UnitStub(1, U.LIBERATOR, flying=True)
    result = micro.final_solve(unit, Action(Point2((20, 0)), True))
    assert result.ability == A.MORPH_LIBERATORAGMODE
    assert unit.distance_to(result.position) <= 5
    micro.cd_manager.is_ready = lambda *args: False
    command = Action(Point2((20, 0)), True)
    assert micro.final_solve(unit, command) is command


@pytest.mark.parametrize('kind,ability', [
    (U.LIBERATOR, A.MORPH_LIBERATORAGMODE),
    (U.LIBERATORAG, A.MORPH_LIBERATORAAMODE),
    (U.LIBERATOR, A.LIBERATORMORPHTOAG_LIBERATORAGMODE),
    (U.LIBERATORAG, A.LIBERATORMORPHTOAA_LIBERATORAAMODE),
    (U.WIDOWMINE, A.BURROWDOWN_WIDOWMINE),
    (U.WIDOWMINEBURROWED, A.BURROWUP_WIDOWMINE),
])
def test_form_changes_are_not_interrupted(kind, ability):
    unit = UnitStub(1, kind, orders=[NS(ability=NS(id=ability))])
    assert _form_change_pending(unit)


def bot_and_order(kind, ability, target=None, **kwargs):
    bot = object.__new__(BenchBot)
    bot.actions = []
    return bot, NS(unit=UnitStub(1, kind, **kwargs), ability=ability, target=target)


def test_final_command_boundary_drops_deployed_liberator_point_attacks():
    bot, order = bot_and_order(U.LIBERATORAG, A.ATTACK, Point2((10, 0)))
    with patch.object(KnowledgeBot, 'do') as submit:
        assert bot.do(order) is False
        submit.assert_not_called()


@pytest.mark.parametrize('ability', [A.MOVE_MOVE, A.HOLDPOSITION_HOLD])
def test_final_command_boundary_releases_before_moving_liberator(ability):
    from sc2.unit import Unit
    from unittest.mock import MagicMock
    bot = object.__new__(BenchBot)
    bot.actions = []
    unit = MagicMock(spec=Unit)
    unit.type_id, unit.orders, unit.buffs = U.LIBERATORAG, [], []
    order = NS(unit=unit, ability=ability, target=Point2((10, 0)))
    with patch.object(KnowledgeBot, 'do', return_value=True) as submit:
        bot.do(order)
        assert submit.call_args.args[0].ability == A.MORPH_LIBERATORAAMODE


@pytest.mark.parametrize('kind,flying,capability', [
    (U.MARAUDER, True, 'can_attack_air'),
    (U.REAPER, True, 'can_attack_air'),
    (U.VIKINGFIGHTER, False, 'can_attack_ground'),
])
def test_final_command_boundary_rejects_incompatible_weapon_targets(kind, flying, capability):
    bot, order = bot_and_order(kind, A.ATTACK, enemy(flying=flying), **{capability: False})
    with patch.object(KnowledgeBot, 'do') as submit:
        assert bot.do(order) is False
        submit.assert_not_called()


def test_route_error_explains_double_encoded_array_without_accepting_it():
    from agents.llm_agent.tools import tool_schemas
    schema = next(t['function']['parameters']['properties']['route']
                  for t in tool_schemas() if t['function']['name'] == 'scout')
    assert schema['type'] == ['array', 'string']
    assert len(schema['oneOf']) == 2
    calls = [{'name': 'scout', 'arguments': {'route': '["zone_15"]'}},
             {'name': 'advance', 'arguments': {'seconds': 1}}]
    with pytest.raises(ActionValidationError, match='not a quoted JSON-encoded array'):
        parse_decision(calls)
    calls[0]['arguments']['route'] = ['zone_15']
    assert parse_decision(calls).actions[0].route == ('zone_15',)
    from sc2bench_env import Environment, ToolCall
    env = Environment('fake', record_trajectory=False)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall('scout', {'route': ['zone_1']})).status == 'staged'
    finally:
        env.close()


def test_worker_travel_state_survives_resource_blocker_in_brief():
    text = '\n'.join(brief_priority_lines([dict(action='build', target='command_center',
        remaining=1, state='worker_en_route', cancellable_count=0, waiting_for='resources')]))
    assert 'worker travelling' in text and 'waiting for resource budget unavailable' in text


def test_native_errors_are_grouped_drained_and_visible_in_model_feedback():
    from sc2bench_env import Environment
    from sc2bench_env.backends.sharpy.backend import SharpyBackend, _Bridge
    from sc2bench_env.adapters.llm.observation import platform_messages
    from sc2.data import ActionResult
    backend = object.__new__(SharpyBackend)
    backend._bridge = _Bridge()
    backend._bridge.native_command_errors = {
        ('liberatorag', A.ATTACK.value, ActionResult.MustTargetUnit.value): 12}
    events = backend.collect_execution_events()
    assert events[0]['count'] == 12
    assert not backend.collect_execution_events()
    env = Environment('fake')
    try:
        obs = env.reset()
        env.task_manager.record_execution_events(events)
        feedback = {'events': env.task_manager.events_since_boundary()}
        text = platform_messages('test', obs.to_dict(), feedback)[1]['content']
        assert 'execution_warning' in text and 'MustTargetUnit' in text and '12' in text
    finally:
        env.close()


def test_environment_publishes_backend_diagnostics_once_per_boundary():
    from sc2bench_env import Environment
    from sc2bench_env.backends.fake import FakeBackend
    from tests.helpers.drive import drive

    class DiagnosticBackend(FakeBackend):
        pending = []

        def collect_execution_events(self):
            result, self.pending = self.pending, []
            return result

    backend = DiagnosticBackend()
    env = Environment(backend, record_trajectory=False)
    try:
        env.reset()
        backend.pending = [dict(type='execution_warning', reason='MustTargetUnit', count=10)]
        _, feedback, _, _ = drive(env, [{'name': 'advance', 'arguments': {'seconds': 1}}])
        assert sum(e['type'] == 'execution_warning' for e in feedback.events) == 1
        _, feedback, _, _ = drive(env, [{'name': 'advance', 'arguments': {'seconds': 1}}])
        assert not any(e['type'] == 'execution_warning' for e in feedback.events)
    finally:
        env.close()

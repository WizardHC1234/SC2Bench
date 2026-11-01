"""Unit tests for platform combat micro helpers."""
from __future__ import annotations


from types import SimpleNamespace

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units
from sharpy.combat import Action
from sharpy.combat.action import NoAction
from sharpy.combat.move_type import MoveType

from tests.unit.test_combat_execution import UnitStub
from sc2bench_env.backends.sharpy.micro import (
    MicroBanshee,
    MicroDefendLurkers,
    MicroDefendTanks,
    build_combat_micro_rules,
)


def test_build_combat_micro_rules_registers_banshee_and_sieged_tank() -> None:
    rules = build_combat_micro_rules()
    assert UnitTypeId.BANSHEE in rules.unit_micros
    assert UnitTypeId.SIEGETANKSIEGED in rules.unit_micros
    assert UnitTypeId.MEDIVAC in rules.unit_micros
    assert UnitTypeId.MARINE in rules.unit_micros
    assert UnitTypeId.SIEGETANK in rules.unit_micros


def test_micro_banshee_cloaks_with_energy() -> None:
    micro = MicroBanshee()
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: True)
    unit = SimpleNamespace(
        type_id=UnitTypeId.BANSHEE,
        is_cloaked=False,
        energy=80.0,
        tag=1,
    )
    result = micro.unit_solve_combat(unit, Action(None, False))
    assert result.ability == AbilityId.BEHAVIOR_CLOAKON_BANSHEE


def test_micro_banshee_decloaks_when_energy_low() -> None:
    micro = MicroBanshee()
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: True)
    unit = SimpleNamespace(
        type_id=UnitTypeId.BANSHEE,
        is_cloaked=True,
        energy=10.0,
        tag=2,
    )
    result = micro.unit_solve_combat(unit, Action(None, False))
    assert result.ability == AbilityId.BEHAVIOR_CLOAKOFF_BANSHEE


def test_banshee_without_cloak_research_keeps_micro_command(monkeypatch) -> None:
    from sharpy.combat import GenericMicro

    micro = MicroBanshee()
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: False)
    monkeypatch.setattr(GenericMicro, "unit_solve_combat", lambda self, unit, command: command)
    unit = SimpleNamespace(type_id=UnitTypeId.BANSHEE, is_cloaked=False, energy=80.0, tag=1)
    command = Action(None, False)
    assert micro.unit_solve_combat(unit, command) is command


def test_banshee_accepts_generic_available_cloak_alias() -> None:
    micro = MicroBanshee()
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: ability == AbilityId.BEHAVIOR_CLOAKON)
    unit = SimpleNamespace(type_id=UnitTypeId.BANSHEE, is_cloaked=False, energy=80, tag=1)
    assert micro.unit_solve_combat(unit, Action(None, False)).ability == AbilityId.BEHAVIOR_CLOAKON_BANSHEE


def test_medivac_retreat_boosts_away_from_anti_air() -> None:
    from sc2.position import Point2
    from sharpy.combat.move_type import MoveType
    from sc2bench_env.backends.sharpy.micro import MicroMedivacsSupport

    micro = MicroMedivacsSupport()
    micro.move_type = MoveType.DefensiveRetreat
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: ability == AbilityId.EFFECT_MEDIVACIGNITEAFTERBURNERS)
    threat = SimpleNamespace(can_attack_air=True, distance_to=lambda other: 4)
    micro.enemies_near_by = [threat]
    result = micro.unit_solve_combat(SimpleNamespace(tag=1), Action(Point2((10, 10)), False))
    assert result.ability == AbilityId.EFFECT_MEDIVACIGNITEAFTERBURNERS


def test_medivac_retreat_does_not_heal_or_escort() -> None:
    from sc2.position import Point2
    from sharpy.combat.move_type import MoveType
    from sc2bench_env.backends.sharpy.micro import MicroMedivacsSupport

    micro = MicroMedivacsSupport()
    micro.move_type = MoveType.DefensiveRetreat
    target = Point2((10, 10))
    result = micro.unit_solve_combat(SimpleNamespace(), Action(target, False))
    assert result.position == target
    assert result.ability is None


def test_mission_boundary_overrides_focus_fire_and_unsieges() -> None:
    from sc2.position import Point2
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules

    rules = MissionMicroRules()
    hold = Point2((10, 10))
    rules.return_point = hold
    rules.boundary = lambda position: position.distance_to(hold) <= 8
    unit = SimpleNamespace(type_id=UnitTypeId.MARINE, position=hold)
    outside = Action(Point2((30, 10)), True)
    result = rules.guard_action(unit, outside)
    assert result.position == hold
    assert not result.is_attack
    unit.type_id = UnitTypeId.SIEGETANKSIEGED
    assert rules.guard_action(unit, outside).ability == AbilityId.UNSIEGE_UNSIEGE
    inside = Action(Point2((12, 10)), True)
    assert rules.guard_action(unit, inside) is inside


def test_defend_keeps_a_spell_already_in_range_and_still_blocks_a_chase() -> None:
    from sc2.position import Point2
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules

    rules = MissionMicroRules()
    hold = Point2((0, 0))
    rules.return_point = hold
    rules.hold_position = True
    rules.boundary = lambda position: position.distance_to(hold) <= 8
    unit = SimpleNamespace(
        type_id=UnitTypeId.GHOST,
        position=hold,
        distance_to=lambda other: hold.distance_to(getattr(other, "position", other)),
    )
    nearby = Action(Point2((10, 0)), False, AbilityId.EMP_EMP)
    assert rules.guard_action(unit, nearby).ability == AbilityId.EMP_EMP
    chase = Action(Point2((40, 0)), False, AbilityId.EMP_EMP)
    assert rules.guard_action(unit, chase).ability is None


def _configure_defend_micro(micro, point: Point2) -> None:
    micro.ai = SimpleNamespace(time=0.0)
    micro.move_type = MoveType.Assault
    micro.rules = SimpleNamespace(hold_position=True, return_point=point)
    micro.enemies_near_by = Units([], micro.ai)


def test_tank_deploys_and_stays_sieged_at_defend_station() -> None:
    point = Point2((10, 10))
    micro = MicroDefendTanks()
    _configure_defend_micro(micro, point)
    tank = UnitStub(1, UnitTypeId.SIEGETANK, position=(10, 10))

    assert micro.unit_solve_combat(tank, Action(point, False)).ability == AbilityId.SIEGEMODE_SIEGEMODE
    micro.ai.time = 1.0
    assert micro.unit_solve_combat(tank, Action(point, False)).ability == AbilityId.SIEGEMODE_SIEGEMODE

    sieged = UnitStub(1, UnitTypeId.SIEGETANKSIEGED, position=(10, 10))
    assert isinstance(micro.unit_solve_combat(sieged, Action(point, False)), NoAction)


def test_sieged_tank_leaves_deployed_form_after_defend_target_moves() -> None:
    micro = MicroDefendTanks()
    _configure_defend_micro(micro, Point2((30, 30)))
    tank = UnitStub(1, UnitTypeId.SIEGETANKSIEGED, position=(10, 10))

    result = micro.unit_solve_combat(tank, Action(Point2((30, 30)), False))
    assert result.ability == AbilityId.UNSIEGE_UNSIEGE


def test_sieged_tank_retreat_overrides_reached_defend_station() -> None:
    point = Point2((10, 10))
    micro = MicroDefendTanks()
    _configure_defend_micro(micro, point)
    micro.move_type = MoveType.DefensiveRetreat
    tank = UnitStub(1, UnitTypeId.SIEGETANKSIEGED, position=(10, 10))

    result = micro.unit_solve_combat(tank, Action(Point2((0, 0)), False))
    assert result.ability == AbilityId.UNSIEGE_UNSIEGE


def test_lurker_deploys_and_stays_burrowed_at_defend_station() -> None:
    point = Point2((10, 10))
    micro = MicroDefendLurkers()
    _configure_defend_micro(micro, point)
    micro.cache = SimpleNamespace(own_in_range=lambda *_: Units([], micro.ai))
    lurker = UnitStub(1, UnitTypeId.LURKERMP, position=(10, 10))

    assert isinstance(micro.unit_solve_combat(lurker, Action(point, False)), NoAction)
    micro.ai.time = 1.0
    assert micro.unit_solve_combat(lurker, Action(point, False)).ability == AbilityId.BURROWDOWN_LURKER

    burrowed = UnitStub(1, UnitTypeId.LURKERMPBURROWED, position=(10, 10), is_burrowed=True)
    assert isinstance(micro.unit_solve_combat(burrowed, Action(point, False)), NoAction)


def test_home_tank_must_reach_station_before_deploying():
    micro=MicroDefendTanks()
    target=Point2((30,30));_configure_defend_micro(micro,target)
    micro.rules.station_before_deploy=True
    tank=UnitStub(1,UnitTypeId.SIEGETANK,position=(10,10))
    result=micro.unit_solve_combat(tank,Action(target,False))
    assert result.position==target and result.ability is None
    tank.type_id=UnitTypeId.SIEGETANKSIEGED
    assert micro.unit_solve_combat(tank,Action(target,False)).ability==AbilityId.UNSIEGE_UNSIEGE


def test_tank_four_units_from_home_station_can_deploy_without_squeezing_to_center():
    micro=MicroDefendTanks();target=Point2((14,10))
    _configure_defend_micro(micro,target);micro.rules.station_before_deploy=True
    tank=UnitStub(1,UnitTypeId.SIEGETANK,position=(10,10))
    result=micro.unit_solve_combat(tank,Action(target,False))
    assert result.ability==AbilityId.SIEGEMODE_SIEGEMODE

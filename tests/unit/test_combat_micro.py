"""Unit tests for platform combat micro helpers."""

from __future__ import annotations

from types import SimpleNamespace

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.combat import Action

from sc2bench_env.backends.sharpy.micro import MicroBanshee, build_combat_micro_rules


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

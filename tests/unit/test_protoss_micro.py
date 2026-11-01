"""Protoss spell and form micro that the combat rules actually register."""
from types import SimpleNamespace
import pytest

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units
from sharpy.combat import Action, MoveType
from sharpy.combat.action import NoAction
from tests.unit.test_combat_execution import UnitStub
from sc2bench_env.backends.sharpy.micro import build_combat_micro_rules
from sc2bench_env.backends.sharpy.protoss_micro import (
    MicroCarrierSafe, MicroDarkTemplarSafe, MicroMothership, MicroObserverSafe,
    MicroOracleSafe, MicroWarpPrismSafe,
)


def enemy(tag, position):
    return UnitStub(tag, position=position, health=100, shield=0, energy=0)


def configure(micro, enemies=(), ready=()):
    micro.move_type = MoveType.Assault
    micro.ai = SimpleNamespace(time=0, start_location=Point2((0, 0)))
    micro.original_target = Point2((10, 10))
    micro.enemies_near_by = Units(list(enemies), micro.ai)
    micro.cd_manager = SimpleNamespace(
        is_ready=lambda tag, ability: ability in ready,
        used_ability=lambda tag, ability: None,
    )
    return micro


def test_spellcasters_keep_dedicated_handlers():
    rules = build_combat_micro_rules()
    expected = {
        UnitTypeId.ZEALOT: "MicroZealots",
        UnitTypeId.STALKER: "MicroStalkers",
        UnitTypeId.SENTRY: "MicroSentries",
        UnitTypeId.ADEPT: "MicroAdepts",
        UnitTypeId.HIGHTEMPLAR: "MicroHighTemplars",
        UnitTypeId.DISRUPTOR: "MicroDisruptor",
        UnitTypeId.DISRUPTORPHASED: "MicroPurificationNova",
        UnitTypeId.PHOENIX: "MicroPhoenixes",
        UnitTypeId.VOIDRAY: "MicroVoidrays",
        UnitTypeId.ORACLE: "MicroOracleSafe",
        UnitTypeId.DARKTEMPLAR: "MicroDarkTemplarSafe",
        UnitTypeId.CARRIER: "MicroCarrierSafe",
        UnitTypeId.OBSERVER: "MicroObserverSafe",
        UnitTypeId.OBSERVERSIEGEMODE: "MicroObserverSafe",
        UnitTypeId.WARPPRISM: "MicroWarpPrismSafe",
        UnitTypeId.WARPPRISMPHASING: "MicroWarpPrismSafe",
        UnitTypeId.MOTHERSHIP: "MicroMothership",
    }
    for unit_type, name in expected.items():
        assert type(rules.unit_micros[unit_type]).__name__ == name


def test_observer_sieges_in_contact_and_releases_to_move():
    near = [enemy(2, (3, 0))]
    micro = configure(MicroObserverSafe(), near, [AbilityId.MORPH_SURVEILLANCEMODE])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.OBSERVER), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.MORPH_SURVEILLANCEMODE

    micro = configure(MicroObserverSafe(), [], [AbilityId.MORPH_OBSERVERMODE])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.OBSERVERSIEGEMODE), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.MORPH_OBSERVERMODE


@pytest.mark.parametrize('ability',[AbilityId.MORPH_OBSERVERMODE,AbilityId.MORPH_SURVEILLANCEMODE])
def test_observer_waits_for_its_current_form_change(ability):
    micro=configure(MicroObserverSafe(),[],[AbilityId.MORPH_OBSERVERMODE])
    observer=UnitStub(1,UnitTypeId.OBSERVERSIEGEMODE,
                      orders=[SimpleNamespace(ability=SimpleNamespace(id=ability))])
    assert isinstance(micro.unit_solve_combat(observer,Action(Point2((8,0)),False)),NoAction)


def test_fungal_stalker_keeps_combat_micro_instead_of_retrying_forbidden_blink(monkeypatch):
    from sharpy.combat.protoss.micro_stalkers import MicroStalkers
    from sharpy.combat import GenericMicro
    micro=configure(MicroStalkers(),[],[AbilityId.EFFECT_BLINK_STALKER])
    unit=UnitStub(1,UnitTypeId.STALKER,buffs={BuffId.FUNGALGROWTH})
    fallback=Action(Point2((8,0)),True)
    monkeypatch.setattr(GenericMicro,'unit_solve_combat',lambda *_:fallback)
    assert micro.unit_solve_combat(unit,fallback) is fallback


@pytest.mark.parametrize('micro,kind', [(MicroObserverSafe,UnitTypeId.OBSERVERSIEGEMODE),
                                      (MicroWarpPrismSafe,UnitTypeId.WARPPRISMPHASING)])
@pytest.mark.parametrize('move_type',[MoveType.Assault,MoveType.DefensiveRetreat])
def test_immobile_support_waits_for_mobile_form_instead_of_issuing_move(micro,kind,move_type):
    handler=configure(micro())
    handler.move_type=move_type
    result=handler.unit_solve_combat(UnitStub(1,kind),Action(Point2((8,0)),False))
    assert isinstance(result,NoAction)


def test_warp_prism_phases_without_loading_and_unphases_on_retreat():
    near = [enemy(2, (3, 0))]
    micro = configure(MicroWarpPrismSafe(), near, [AbilityId.MORPH_WARPPRISMPHASINGMODE])
    prism = UnitStub(1, UnitTypeId.WARPPRISM, cargo_max=8, cargo_used=0)
    result = micro.unit_solve_combat(prism, Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.MORPH_WARPPRISMPHASINGMODE

    micro.move_type = MoveType.DefensiveRetreat
    micro.cd_manager = SimpleNamespace(
        is_ready=lambda tag, ability: ability == AbilityId.MORPH_WARPPRISMTRANSPORTMODE,
        used_ability=lambda tag, ability: None,
    )
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.WARPPRISMPHASING), Action(Point2((0, 0)), False))
    assert result.ability == AbilityId.MORPH_WARPPRISMTRANSPORTMODE


def test_oracle_revelation_and_beam_off():
    cluster = [enemy(index, (float(index), 0)) for index in range(2, 5)]
    micro = configure(MicroOracleSafe(), cluster, [AbilityId.ORACLEREVELATION_ORACLEREVELATION])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.ORACLE, energy=75), Action(None, False))
    assert result.ability == AbilityId.ORACLEREVELATION_ORACLEREVELATION

    micro = configure(MicroOracleSafe(), cluster, [AbilityId.BEHAVIOR_PULSARBEAMOFF])
    micro.move_type = MoveType.PanicRetreat
    unit = UnitStub(1, UnitTypeId.ORACLE, energy=10, has_buff=lambda buff: buff == BuffId.ORACLEWEAPON)
    result = micro.unit_solve_combat(unit, Action(Point2((0, 0)), False))
    assert result.ability == AbilityId.BEHAVIOR_PULSARBEAMOFF


def test_oracle_drops_a_stasis_ward_when_leaving_a_group():
    cluster = [enemy(index, (float(index), 0)) for index in range(2, 5)]
    micro = configure(MicroOracleSafe(), cluster, [AbilityId.ORACLESTASISTRAP_ORACLEBUILDSTASISTRAP])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.ORACLE, energy=50), Action(Point2((0, 0)), False))
    assert result.ability == AbilityId.ORACLESTASISTRAP_ORACLEBUILDSTASISTRAP


def test_dark_templar_blinks_toward_an_enemy_after_research():
    micro = configure(MicroDarkTemplarSafe(), [enemy(2, (8, 0))], [AbilityId.EFFECT_SHADOWSTRIDE])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.DARKTEMPLAR), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.EFFECT_SHADOWSTRIDE


def test_carrier_builds_interceptors_until_the_game_stops_offering_them():
    micro = configure(MicroCarrierSafe(), ready=[AbilityId.BUILD_INTERCEPTORS])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.CARRIER), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.BUILD_INTERCEPTORS


def test_mothership_time_warp_and_no_recall_on_retreat():
    cluster = [enemy(index, (float(index), 1)) for index in range(2, 6)]
    micro = configure(MicroMothership(), cluster, [AbilityId.EFFECT_TIMEWARP])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.MOTHERSHIP, energy=100), Action(None, False))
    assert result.ability == AbilityId.EFFECT_TIMEWARP

    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.MOTHERSHIP, energy=200), Action(Point2((0, 0)), False))
    assert result.ability is None
    assert not isinstance(result, NoAction)

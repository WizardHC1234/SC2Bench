"""Zerg spell and burrow handlers registered on combat micro."""
from types import SimpleNamespace

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units
from sharpy.combat import Action, MoveType
from tests.unit.test_combat_execution import UnitStub
from sc2bench_env.backends.sharpy.micro import build_combat_micro_rules
from sc2bench_env.backends.sharpy.zerg_micro import (
    MicroBanelingSafe, MicroEscapeBurrow, MicroOverseerSafe, MicroRoachSafe, MicroViperSafe,
)


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
        UnitTypeId.QUEEN: "MicroQueens",
        UnitTypeId.QUEENBURROWED: "MicroQueens",
        UnitTypeId.RAVAGER: "MicroRavagers",
        UnitTypeId.RAVAGERBURROWED: "MicroRavagers",
        UnitTypeId.LURKERMP: "MicroDefendLurkers",
        UnitTypeId.LURKERMPBURROWED: "MicroDefendLurkers",
        UnitTypeId.INFESTOR: "MicroInfestors",
        UnitTypeId.INFESTORBURROWED: "MicroInfestors",
        UnitTypeId.SWARMHOSTMP: "MicroSwarmHosts",
        UnitTypeId.SWARMHOSTBURROWEDMP: "MicroSwarmHosts",
        UnitTypeId.VIPER: "MicroViperSafe",
        UnitTypeId.ROACH: "MicroRoachSafe",
        UnitTypeId.ROACHBURROWED: "MicroRoachSafe",
        UnitTypeId.BANELING: "MicroBanelingSafe",
        UnitTypeId.BANELINGBURROWED: "MicroBanelingSafe",
        UnitTypeId.ZERGLING: "MicroEscapeBurrow",
        UnitTypeId.ZERGLINGBURROWED: "MicroEscapeBurrow",
        UnitTypeId.HYDRALISK: "MicroEscapeBurrow",
        UnitTypeId.HYDRALISKBURROWED: "MicroEscapeBurrow",
        UnitTypeId.ULTRALISK: "MicroEscapeBurrow",
        UnitTypeId.ULTRALISKBURROWED: "MicroEscapeBurrow",
        UnitTypeId.OVERSEER: "MicroOverseerSafe",
        UnitTypeId.OVERSEERSIEGEMODE: "MicroOverseerSafe",
    }
    for unit_type, name in expected.items():
        assert type(rules.unit_micros[unit_type]).__name__ == name
    assert rules.unit_micros[UnitTypeId.LURKERMP] is rules.unit_micros[UnitTypeId.LURKERMPBURROWED]


def test_hurt_roach_burrows_and_surfaces_when_healthy():
    micro = configure(MicroRoachSafe(), ready=[AbilityId.BURROWDOWN_ROACH])
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.ROACH, health_percentage=0.2, is_burrowed=False),
        Action(Point2((8, 0)), False),
    )
    assert result.ability == AbilityId.BURROWDOWN_ROACH

    micro = configure(MicroRoachSafe(), ready=[AbilityId.BURROWUP_ROACH])
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.ROACHBURROWED, health_percentage=0.9, is_burrowed=True),
        Action(Point2((8, 0)), False),
    )
    assert result.ability == AbilityId.BURROWUP_ROACH


def test_baneling_stays_mobile_on_retreat_and_surfaces_before_moving():
    micro = configure(MicroBanelingSafe(), [UnitStub(2, position=(3, 0))], [AbilityId.BURROWDOWN_BANELING])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.BANELING, is_burrowed=False), Action(Point2((0, 0)), False))
    assert result.ability is None
    assert result.target == Point2((0, 0))

    micro = configure(MicroBanelingSafe(), ready=[AbilityId.BURROWUP_BANELING])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.BANELINGBURROWED, is_burrowed=True), Action(Point2((0, 0)), False),
    )
    assert result.ability == AbilityId.BURROWUP_BANELING


def test_burrowed_roach_surfaces_before_retreating():
    micro = configure(MicroRoachSafe(), ready=[AbilityId.BURROWUP_ROACH])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.ROACHBURROWED, health_percentage=0.2, is_burrowed=True),
        Action(Point2((0, 0)), False),
    )
    assert result.ability == AbilityId.BURROWUP_ROACH


def test_overseer_contaminates_a_building_and_switches_oversight_by_contact():
    building = UnitStub(2, position=(3, 0), is_structure=True)
    micro = configure(MicroOverseerSafe(), ready=[AbilityId.CONTAMINATE_CONTAMINATE])
    micro.ai.enemy_structures = [building]
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.OVERSEERSIEGEMODE, energy=125), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.CONTAMINATE_CONTAMINATE

    micro = configure(MicroOverseerSafe(), [UnitStub(2, position=(4, 0))], [AbilityId.MORPH_OVERSIGHTMODE])
    result = micro.unit_solve_combat(UnitStub(1, UnitTypeId.OVERSEER, energy=10), Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.MORPH_OVERSIGHTMODE

    micro = configure(MicroOverseerSafe(), ready=[AbilityId.MORPH_OVERSEERMODE])
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.OVERSEERSIEGEMODE, energy=10), Action(Point2((8, 0)), False),
    )
    assert result.ability == AbilityId.MORPH_OVERSEERMODE

    micro = configure(
        MicroOverseerSafe(),
        ready=[AbilityId.MORPH_OVERSEERMODE, AbilityId.CONTAMINATE_CONTAMINATE],
    )
    micro.move_type = MoveType.DefensiveRetreat
    micro.ai.enemy_structures = [building]
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.OVERSEERSIEGEMODE, energy=125), Action(Point2((0, 0)), False),
    )
    assert result.ability == AbilityId.MORPH_OVERSEERMODE


def test_viper_consumes_a_legal_building_and_rejects_a_unit():
    roach = UnitStub(2, UnitTypeId.ROACH, position=(1, 0), health=80, is_flying=False)
    spine = UnitStub(3, UnitTypeId.SPINECRAWLER, position=(2, 0), health=300, is_structure=True, is_ready=True)
    dying = UnitStub(4, UnitTypeId.SPORECRAWLER, position=(1, 0), health=100, is_structure=True, is_ready=True)
    micro = configure(MicroViperSafe(), ready=[AbilityId.VIPERCONSUME_VIPERCONSUME])
    micro.ai.structures = [roach, spine, dying]
    micro.ai.units = [roach]
    viper = UnitStub(1, UnitTypeId.VIPER, position=(0, 0), energy=20)
    result = micro.unit_solve_combat(viper, Action(Point2((8, 0)), False))
    assert result.ability == AbilityId.VIPERCONSUME_VIPERCONSUME
    assert result.target is spine

    micro = configure(MicroViperSafe(), ready=[AbilityId.VIPERCONSUME_VIPERCONSUME])
    micro.ai.structures = [spine]
    rested = UnitStub(1, UnitTypeId.VIPER, position=(0, 0), energy=80, orders=[
        SimpleNamespace(ability=SimpleNamespace(id="VIPERCONSUME_VIPERCONSUME")),
    ])
    held = micro.unit_solve_combat(rested, Action(Point2((8, 0)), False))
    assert getattr(held, "ability", None) is None


def test_hurt_zergling_burrows_and_surfaces_before_retreat():
    rules = build_combat_micro_rules()
    micro = rules.unit_micros[UnitTypeId.ZERGLING]
    assert isinstance(micro, MicroEscapeBurrow)
    configure(micro, [UnitStub(2, position=(2, 0))], [AbilityId.BURROWDOWN_ZERGLING])
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.ZERGLING, health_percentage=0.2), Action(Point2((8, 0)), False),
    )
    assert result.ability == AbilityId.BURROWDOWN_ZERGLING
    configure(micro, ready=[AbilityId.BURROWUP_ZERGLING])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(
        UnitStub(1, UnitTypeId.ZERGLINGBURROWED, health_percentage=0.2, is_burrowed=True),
        Action(Point2((0, 0)), False),
    )
    assert result.ability == AbilityId.BURROWUP_ZERGLING
    assert not isinstance(rules.unit_micros.get(UnitTypeId.DRONE), MicroEscapeBurrow)

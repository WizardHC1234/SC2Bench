"""Spell legality, retreat and form handling for the expanded roster."""
import asyncio
from types import SimpleNamespace

import pytest
from tests.unit.test_combat_execution import UnitStub
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units
from sharpy.combat import Action, GenericMicro, MoveType
from sharpy.combat.action import NoAction
from sc2bench_env.backends.sharpy.terran_micro import (
    MicroGhost, MicroCyclone, MicroThor, MicroWidowMine, MicroLiberatorSafe,
    MicroVikingSafe, MicroRavenSupport, MicroBattlecruiserSafe,
)
from sc2bench_env.backends.sharpy.micro import build_combat_micro_rules


def enemy(tag=2, **kw):
    return UnitStub(tag, health=150, shield=0, energy=0, is_biological=False,
                    is_mechanical=False, is_psionic=False, is_cloaked=False,
                    is_light=False, has_buff=lambda _: False, **kw)


def configure(micro, enemies=(), ready=()):
    micro.move_type = MoveType.Assault
    micro.ai = SimpleNamespace(time=0, start_location=Point2((0, 0)))
    micro.original_target = Point2((10, 10))
    micro.enemies_near_by = Units(list(enemies), micro.ai)
    micro.cd_manager = SimpleNamespace(is_ready=lambda tag, ability: ability in ready,
                                       used_ability=lambda tag, ability: None)
    return micro


def test_every_train_type_has_micro_or_explicit_generic_fallback():
    from sc2bench_env.backends.sharpy.races.terran import UNITS
    rules = build_combat_micro_rules()
    for name, (unit_type, _) in UNITS.items():
        assert rules.unit_micros.get(unit_type, rules.generic_micro) is not None, name
    for unit_type in (UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.LIBERATORAG,
                      UnitTypeId.VIKINGASSAULT, UnitTypeId.THORAP):
        assert unit_type in rules.unit_micros


@pytest.mark.parametrize("cls,unit_type,ability", [
    (MicroWidowMine, UnitTypeId.WIDOWMINEBURROWED, AbilityId.BURROWUP_WIDOWMINE),
    (MicroLiberatorSafe, UnitTypeId.LIBERATORAG, AbilityId.MORPH_LIBERATORAAMODE),
    (MicroVikingSafe, UnitTypeId.VIKINGASSAULT, AbilityId.MORPH_VIKINGFIGHTERMODE),
])
def test_retreat_releases_immobile_or_slow_form_even_with_enemies(cls, unit_type, ability):
    micro = configure(cls(), [enemy()])
    micro.move_type = MoveType.DefensiveRetreat
    result = micro.unit_solve_combat(UnitStub(1, unit_type), Action(Point2((0, 0)), False))
    assert result.ability == ability


def test_ghost_does_not_interrupt_snipe():
    micro = configure(MicroGhost())
    unit = UnitStub(1, UnitTypeId.GHOST, orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.EFFECT_GHOSTSNIPE))])
    assert isinstance(micro.unit_solve_combat(unit, Action(None, False)), NoAction)


def test_ghost_emp_uses_point_and_ready_ability():
    target = enemy()
    target.shield = 100
    micro = configure(MicroGhost(), [target], [AbilityId.EMP_EMP])
    unit = UnitStub(1, UnitTypeId.GHOST, energy=100, orders=[], is_cloaked=False)
    result = micro.unit_solve_combat(unit, Action(None, False))
    assert result.ability == AbilityId.EMP_EMP
    assert result.target == target.position


def test_raven_matrix_ignores_organic_and_structures(monkeypatch):
    organic, structure, mechanical = enemy(2), enemy(3), enemy(4)
    organic.is_biological = True
    structure.is_structure = structure.is_mechanical = True
    mechanical.is_mechanical = True
    micro = configure(MicroRavenSupport(), [organic, structure, mechanical], [AbilityId.EFFECT_INTERFERENCEMATRIX])
    unit = UnitStub(1, UnitTypeId.RAVEN, energy=100)
    result = micro.unit_solve_combat(unit, Action(Point2((10, 10)), False))
    assert result.target is mechanical
    # Target already claimed by this mission is not cast on again.
    result = micro.unit_solve_combat(unit, Action(Point2((10, 10)), False))
    assert result.ability is None


def test_raven_turret_requires_validated_placement():
    micro = configure(MicroRavenSupport(), [], [AbilityId.BUILDAUTOTURRET_AUTOTURRET])
    unit = UnitStub(1, UnitTypeId.RAVEN, energy=50)
    command = Action(Point2((10, 10)), False)
    assert micro.unit_solve_combat(unit, command).ability is None
    micro.turret_points[1] = Point2((2, 0))
    assert micro.unit_solve_combat(unit, command).ability == AbilityId.BUILDAUTOTURRET_AUTOTURRET


def test_raven_prepare_drops_invalid_placement():
    micro = configure(MicroRavenSupport())
    async def invalid(*args):
        return False
    micro.ai.enemy_units = [enemy()]
    micro.ai._game_data = SimpleNamespace(
        units={UnitTypeId.AUTOTURRET.value: SimpleNamespace(creation_ability=object())}
    )
    micro.ai.can_place_single = invalid
    asyncio.run(micro.prepare([UnitStub(1, UnitTypeId.RAVEN, energy=100)], micro.ai))
    assert not micro.turret_points


def test_raven_prepare_converts_unit_type_before_placement():
    micro = configure(MicroRavenSupport())
    seen = {}

    async def place(ability, point):
        seen["ability"] = ability
        seen["point"] = point
        return True

    creation = object()
    micro.ai.enemy_units = [enemy(position=(3, 0))]
    micro.ai._game_data = SimpleNamespace(
        units={UnitTypeId.AUTOTURRET.value: SimpleNamespace(creation_ability=creation)}
    )
    micro.ai.can_place_single = place
    asyncio.run(micro.prepare([UnitStub(1, UnitTypeId.RAVEN, energy=100, position=(0, 0))], micro.ai))
    assert seen["ability"] is creation
    assert 1 in micro.turret_points


def test_raven_placement_tries_grid_aligned_alternative():
    micro = configure(MicroRavenSupport())
    points = []
    async def place(ability, point):
        points.append(point)
        return len(points) == 2
    micro.ai.enemy_units = [enemy(position=(3.3, 0.7))]
    micro.ai._game_data = SimpleNamespace(
        units={UnitTypeId.AUTOTURRET.value: SimpleNamespace(creation_ability=object())})
    micro.ai.can_place_single = place
    asyncio.run(micro.prepare([UnitStub(1, UnitTypeId.RAVEN, energy=100, position=(0.2, 0.3))], micro.ai))
    assert len(points) == 2 and points[0] != points[1]
    assert all(p.x == round(p.x) and p.y == round(p.y) for p in points)
    assert micro.turret_points[1] == points[1]


def test_raven_placement_does_not_silently_hide_query_error():
    micro = configure(MicroRavenSupport())
    async def broken(*_):
        raise RuntimeError("query failed")
    micro.ai.enemy_units = [enemy()]
    micro.ai._game_data = SimpleNamespace(
        units={UnitTypeId.AUTOTURRET.value: SimpleNamespace(creation_ability=object())})
    micro.ai.can_place_single = broken
    with pytest.raises(RuntimeError, match="query failed"):
        asyncio.run(micro.prepare([UnitStub(1, UnitTypeId.RAVEN, energy=100)], micro.ai))


def test_thor_switches_to_heavy_air_mode():
    air = enemy()
    air.is_flying = True
    micro = configure(MicroThor(), [air], [AbilityId.MORPH_THORHIGHIMPACTMODE])
    assert micro.unit_solve_combat(UnitStub(1, UnitTypeId.THOR), Action(None, False)).ability == AbilityId.MORPH_THORHIGHIMPACTMODE


def test_cyclone_lock_is_per_unit_and_preserves_channel():
    target = enemy()
    micro = configure(MicroCyclone(), [target], [AbilityId.LOCKON_LOCKON])
    unit = UnitStub(1, UnitTypeId.CYCLONE, orders=[])
    assert micro.unit_solve_combat(unit, Action(None, False)).ability == AbilityId.LOCKON_LOCKON
    unit.orders = [SimpleNamespace(ability=SimpleNamespace(id=AbilityId.LOCKON_LOCKON))]
    assert isinstance(micro.unit_solve_combat(unit, Action(None, False)), NoAction)
    assert 2 not in micro.locks
    assert not micro.locks  # A cast/order alone is not a sustained lock.


def test_cyclone_unavailable_skill_does_not_create_lock(monkeypatch):
    command = Action(Point2((10, 10)), False)
    monkeypatch.setattr(GenericMicro, "unit_solve_combat", lambda *_: command)
    micro = configure(MicroCyclone(), [enemy()])
    assert micro.unit_solve_combat(UnitStub(1, UnitTypeId.CYCLONE, orders=[]), command) is command
    assert not micro.locks and not micro.pending_locks


def test_cyclone_rejected_proposal_expires_without_kiting(monkeypatch):
    command = Action(Point2((10, 10)), False)
    monkeypatch.setattr(GenericMicro, "unit_solve_combat", lambda *_: command)
    micro = configure(MicroCyclone(), [enemy()], [AbilityId.LOCKON_LOCKON])
    unit = UnitStub(1, UnitTypeId.CYCLONE, orders=[])
    micro.unit_solve_combat(unit, command)
    micro.ai.time = 2
    micro.cd_manager.is_ready = lambda *_: False
    assert micro.unit_solve_combat(unit, command) is command
    assert not micro.locks and not micro.pending_locks


def test_cyclone_only_confirmed_buff_enables_sustained_lock():
    target = enemy()
    micro = configure(MicroCyclone(), [target], [AbilityId.LOCKON_LOCKON])
    unit = UnitStub(1, UnitTypeId.CYCLONE, orders=[])
    micro.unit_solve_combat(unit, Action(None, False))
    target.has_buff = lambda buff: buff.name == "LOCKON"
    result = micro.unit_solve_combat(unit, Action(None, False))
    assert 1 in micro.locks and 1 not in micro.pending_locks
    assert result.ability is None


def test_bc_retreat_is_not_overwritten_by_yamato_or_repairs():
    micro = configure(MicroBattlecruiserSafe())
    micro.move_type = MoveType.DefensiveRetreat
    unit = UnitStub(1, UnitTypeId.BATTLECRUISER, health_percentage=0.8)
    result = micro.unit_solve_combat(unit, Action(Point2((0, 0)), False))
    assert result.target == Point2((0, 0)) and result.ability is None


@pytest.mark.parametrize("ability", [AbilityId.YAMATO_YAMATOGUN, AbilityId.EFFECT_TACTICALJUMP])
def test_bc_preserves_observed_skill_channel(ability):
    micro = configure(MicroBattlecruiserSafe())
    unit = UnitStub(1, UnitTypeId.BATTLECRUISER, orders=[
        SimpleNamespace(ability=SimpleNamespace(id=ability))])
    assert isinstance(micro.unit_solve_combat(unit, Action(None, False)), NoAction)


def test_bc_retreat_can_interrupt_yamato_but_not_jump():
    micro = configure(MicroBattlecruiserSafe())
    micro.move_type = MoveType.DefensiveRetreat
    unit = UnitStub(1, UnitTypeId.BATTLECRUISER, health_percentage=.8, orders=[
        SimpleNamespace(ability=SimpleNamespace(id=AbilityId.YAMATO_YAMATOGUN))])
    assert micro.unit_solve_combat(unit, Action(Point2((0, 0)), False)).ability is None
    unit.orders[0].ability.id = AbilityId.EFFECT_TACTICALJUMP
    assert isinstance(micro.unit_solve_combat(unit, Action(None, False)), NoAction)


@pytest.mark.parametrize("reason", ["buff_lost", "target_lost", "retreat"])
def test_cyclone_confirmed_lock_cleans_up_on_interruption(reason, monkeypatch):
    command = Action(Point2((10, 10)), False)
    monkeypatch.setattr(GenericMicro, "unit_solve_combat", lambda *_: command)
    target = enemy()
    micro = configure(MicroCyclone(), [target])
    micro.locks[1] = (target.tag, 14)
    if reason == "target_lost":
        micro.enemies_near_by = Units([], micro.ai)
    if reason == "retreat":
        target.has_buff = lambda _: True
        micro.move_type = MoveType.DefensiveRetreat
        micro.pending_locks[1] = (target.tag, 1)
    micro.unit_solve_combat(UnitStub(1, UnitTypeId.CYCLONE, orders=[]), command)
    assert not micro.locks and not micro.pending_locks


def test_combat_mission_tracks_forms_and_skill_orders():
    from sc2bench_env.backends.sharpy.acts import ActCombatMission

    act = ActCombatMission("attack", "zone_1", {"widow_mine": 1, "ghost": 1})
    act.ai = SimpleNamespace(
        time=1.0,
        units=lambda *_: SimpleNamespace(amount=0),
        enemy_units=[],
        enemy_structures=[],
    )
    act._is_visible_enemy = lambda enemy: True
    mine = UnitStub(1, UnitTypeId.WIDOWMINEBURROWED, orders=[], is_cloaked=False)
    ghost = UnitStub(
        2,
        UnitTypeId.GHOST,
        energy=100,
        is_cloaked=True,
        orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.EMP_EMP))],
    )
    free = [mine, ghost]
    act._refresh_alive_counts(free, set())
    assert act.form_counts.get("widow_mine_burrowed") == 1
    assert act.cloaked_counts.get("ghost") == 1
    assert act.skill_evidence.get("ghost_emp", 0) >= 1
    assert act.skill_evidence.get("widow_mine_burrowed", 0) >= 1

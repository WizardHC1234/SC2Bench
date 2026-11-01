"""Gathering must never reserve free units or commandeer another task."""
import asyncio
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.managers.core.roles import UnitTask
from sc2bench_env.backends.sharpy.gather import PlanHomeGather, MOBILE_FORM_ABILITIES
from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.backends.sharpy.races.zerg import ZergAdapter


def unit(tag, type_id=UnitTypeId.MARINE, position=(130, 100), **attrs):
    u = SimpleNamespace(tag=tag, type_id=type_id, position=Point2(position),
                        is_ready=True, is_flying=False, is_structure=False,
                        is_hallucination=False, is_idle=True, cargo_used=0, commands=[])
    u.__dict__.update(attrs)
    u.distance_to = lambda p: u.position.distance_to(p)
    u.move = lambda p: u.commands.append(("move", p))
    u.attack = lambda p: u.commands.append(("attack", p))
    # Special methods must live on the class, not the instance.
    class CallableUnit(SimpleNamespace):
        def __call__(self, ability, target=None):
            self.commands.append((ability, target))
    return CallableUnit(**u.__dict__)


def gather(units=(), structures=(), roles=None, adapter=None):
    act = PlanHomeGather(adapter or TerranAdapter())
    act.ai = SimpleNamespace(start_location=Point2((100, 100)),
                             game_info=SimpleNamespace(map_center=Point2((120, 100))),
                             units=list(units), structures=list(structures), bench_combat_tags=set(),
                             in_pathing_grid=lambda p: True)
    act.zone_manager = SimpleNamespace(own_main_zone=SimpleNamespace(ramp=None))
    act.roles = SimpleNamespace(is_in_role=lambda r, u: (roles or {}).get(u.tag) == r)
    return act


def run(act):
    assert asyncio.run(act.execute()) is True


def test_free_new_soldier_moves_home_without_reservation_or_mission():
    marine = unit(1)
    act = gather([marine])
    run(act)
    assert marine.commands == [("attack", act.home_point())]
    assert act.ai.bench_combat_tags == set()
    assert act.eligible(marine)  # Binding availability has not been consumed.


@pytest.mark.parametrize("role", [UnitTask.Reserved, UnitTask.Attacking,
                                  UnitTask.Defending, UnitTask.Fighting, UnitTask.Scouting])
def test_other_tasks_are_not_pulled_home(role):
    marine = unit(1)
    act = gather([marine], roles={1: role})
    run(act)
    assert not marine.commands


def test_tags_workers_transport_and_existing_orders_are_excluded():
    units = [unit(1), unit(2, UnitTypeId.SCV), unit(3, UnitTypeId.MEDIVAC, cargo_used=1),
             unit(4, is_idle=False), unit(5, is_hallucination=True), unit(6, is_ready=False)]
    act = gather(units)
    act.ai.bench_combat_tags.add(1)
    run(act)
    assert all(not u.commands for u in units)


@pytest.mark.parametrize("type_id", [UnitTypeId.MEDIVAC, UnitTypeId.RAVEN])
def test_support_units_also_gather(type_id):
    support = unit(1, type_id, is_flying=True, can_attack=False)
    act = gather([support])
    run(act)
    assert support.commands == [("move", act.home_point())]


def test_new_mothership_inheriting_a_mineral_rally_moves_to_army_home():
    ship=unit(1,UnitTypeId.MOTHERSHIP,is_flying=True,can_attack=False,is_idle=False,
              orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.MOVE_MOVE),target=99)])
    act=gather([ship],adapter=ProtossAdapter())
    act.ai.mineral_field=[SimpleNamespace(tag=99)]
    run(act)
    assert ship.commands==[('move',act.home_point())]


def test_mineral_rally_does_not_override_a_scout_assignment():
    ship=unit(1,UnitTypeId.MOTHERSHIP,is_flying=True,is_idle=False,
              orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.MOVE_MOVE),target=99)])
    act=gather([ship],roles={1:UnitTask.Scouting},adapter=ProtossAdapter())
    act.ai.mineral_field=[SimpleNamespace(tag=99)]
    run(act)
    assert not ship.commands


_DEPLOYED_FORMS = (
    (UnitTypeId.SIEGETANKSIEGED, AbilityId.UNSIEGE_UNSIEGE, TerranAdapter()),
    (UnitTypeId.WIDOWMINEBURROWED, AbilityId.BURROWUP_WIDOWMINE, TerranAdapter()),
    (UnitTypeId.LIBERATORAG, AbilityId.MORPH_LIBERATORAAMODE, TerranAdapter()),
    (UnitTypeId.OBSERVERSIEGEMODE, AbilityId.MORPH_OBSERVERMODE, ProtossAdapter()),
    (UnitTypeId.WARPPRISMPHASING, AbilityId.MORPH_WARPPRISMTRANSPORTMODE, ProtossAdapter()),
    (UnitTypeId.HYDRALISKBURROWED, AbilityId.BURROWUP_HYDRALISK, ZergAdapter()),
    (UnitTypeId.LURKERMPBURROWED, AbilityId.BURROWUP_LURKER, ZergAdapter()),
    (UnitTypeId.INFESTORBURROWED, AbilityId.BURROWUP_INFESTOR, ZergAdapter()),
    (UnitTypeId.RAVAGERBURROWED, AbilityId.BURROWUP_RAVAGER, ZergAdapter()),
    (UnitTypeId.ROACHBURROWED, AbilityId.BURROWUP_ROACH, ZergAdapter()),
    (UnitTypeId.BANELINGBURROWED, AbilityId.BURROWUP_BANELING, ZergAdapter()),
    (UnitTypeId.SWARMHOSTBURROWEDMP, AbilityId.BURROWUP_SWARMHOST, ZergAdapter()),
    (UnitTypeId.ULTRALISKBURROWED, AbilityId.BURROWUP_ULTRALISK, ZergAdapter()),
    (UnitTypeId.ZERGLINGBURROWED, AbilityId.BURROWUP_ZERGLING, ZergAdapter()),
)


@pytest.mark.parametrize("type_id,ability,adapter", _DEPLOYED_FORMS)
def test_unassigned_deployed_forms_become_mobile_before_gathering(type_id, ability, adapter):
    deployed = unit(1, type_id)
    act = gather([deployed], adapter=adapter)
    run(act)
    assert deployed.commands == [(ability, None)]


def test_every_home_gather_mobile_form_has_a_race_case():
    assert {(type_id, ability) for type_id, ability, _adapter in _DEPLOYED_FORMS} == set(
        MOBILE_FORM_ABILITIES.items()
    )


def test_arrived_units_are_left_alone():
    marine = unit(1, position=(108, 100))
    run(gather([marine]))
    assert not marine.commands


def test_remote_defender_contact_does_not_redirect_home_army():
    home = unit(1, position=(108, 100))
    detached = unit(2, position=(160, 100))
    enemy = unit(3, position=(165, 100), is_visible=True)
    act = gather([home, detached])
    act.ai.bench_group0_tags = {1, 2}
    act.ai.all_enemy_units = [enemy]
    act.roles.set_tasks = lambda *_: None
    act._micro_started = True
    act._micro_rules = SimpleNamespace(boundary=None)
    calls = []
    act.combat = SimpleNamespace(add_unit=lambda u: None,
                                execute=lambda point, *_: calls.append(point))
    run(act)
    assert calls == [act.home_point()]
    assert act.ai.bench_group0_engaged  # Detached unit still receives combat micro.


def test_group0_defends_locally_but_remains_dispatchable():
    from sc2bench_env.backends.sharpy.combat_styles import available_for_mission
    marine = unit(1, position=(108, 100))
    enemy = unit(2, position=(115, 100), is_visible=True)
    distant = unit(3, position=(180, 100), is_visible=True)
    act = gather([marine])
    act.ai.all_enemy_units = [enemy, distant]
    act.roles.ai = act.ai
    act.roles.set_tasks = lambda role, units: None
    act._micro_started = True
    act._micro_rules = SimpleNamespace(boundary=None)
    calls = []
    act.combat = SimpleNamespace(add_unit=lambda u: calls.append(u.tag),
                                execute=lambda point, mode, rules: calls.append(point))
    run(act)
    assert act.ai.bench_group0_tags == {1}
    assert act.ai.bench_group0_engaged
    assert calls == [1, enemy.position]
    act.roles.is_in_role = lambda role, u: role == UnitTask.Reserved
    assert available_for_mission(marine, act.roles, set())
    assert act._micro_rules.boundary(enemy.position)
    assert not act._micro_rules.boundary(distant.position)
    act.ai.all_enemy_units = [distant]
    run(act)
    assert calls[-1] == act.home_point() and not act.ai.bench_group0_engaged


def test_transport_overlord_peace_role_still_allows_explicit_dispatch():
    from sc2bench_env.backends.sharpy.combat_styles import available_for_mission
    transport=unit(1,UnitTypeId.OVERLORDTRANSPORT,is_flying=True)
    act=gather([transport],roles={1:UnitTask.Reserved},adapter=ZergAdapter())
    assert not act.eligible(transport)  # Agent-owned transport stays where it is.
    assert available_for_mission(transport,act.roles,set())
    assert not available_for_mission(transport,act.roles,{1})


@pytest.mark.parametrize('role',[UnitTask.Scouting,UnitTask.Attacking,UnitTask.Defending,UnitTask.Fighting])
def test_busy_transport_overlord_is_not_available_for_another_mission(role):
    from sc2bench_env.backends.sharpy.combat_styles import available_for_mission
    transport=unit(1,UnitTypeId.OVERLORDTRANSPORT,is_flying=True)
    act=gather([transport],roles={1:role},adapter=ZergAdapter())
    assert not available_for_mission(transport,act.roles,set())


def test_passing_home_does_not_merge_an_outbound_group():
    marine = unit(1, position=(108, 100))
    act = gather([marine])
    act.ai.bench_combat_tags = {1}
    run(act)
    assert act.ai.bench_group0_tags == set()


@pytest.mark.parametrize('kind,adapter',[
    (UnitTypeId.MARINE,TerranAdapter()),(UnitTypeId.STALKER,ProtossAdapter()),
    (UnitTypeId.ROACH,ZergAdapter())])
@pytest.mark.parametrize('moving',[False,True])
def test_rallying_troops_enter_micro_as_soon_as_they_see_an_enemy(kind,adapter,moving):
    troop=unit(1,kind,position=(130,100),is_idle=not moving,sight_range=10)
    act=gather([troop],adapter=adapter)
    point=act.home_point()
    if moving:
        troop.orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.MOVE_MOVE),target=point)]
    enemy=unit(2,position=(138,100),is_visible=True)
    act.ai.all_enemy_units=[enemy]
    act.roles.set_tasks=lambda *_:None
    act._micro_started=True
    act._micro_rules=SimpleNamespace()
    calls=[]
    act.combat=SimpleNamespace(add_unit=lambda u:calls.append(u.tag),
                               execute=lambda target,*_:calls.append(target))
    run(act)
    assert act.ai.bench_group0_tags=={1}
    assert calls==[1,point]  # Local fighting is handled by the admitted unit's micro.
    assert not troop.commands


def test_enemy_does_not_pull_a_scout_or_explicit_order_into_home_micro():
    troop=unit(1,position=(130,100),is_idle=False,
               orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.MOVE_MOVE),target=Point2((190,100)))])
    act=gather([troop])
    assert not act.eligible(troop,rally_point=act.home_point())


def test_all_ready_landed_producers_rally_once_and_landing_reapplies():
    buildings = [unit(i, t, is_structure=True) for i, t in enumerate(
        [UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT], 1)]
    unfinished = unit(4, UnitTypeId.BARRACKS, is_structure=True, is_ready=False)
    townhall = unit(5, UnitTypeId.COMMANDCENTER, is_structure=True)
    act = gather(structures=buildings + [unfinished, townhall])
    run(act)
    run(act)
    assert all(b.commands == [(AbilityId.RALLY_BUILDING, act.home_point())] for b in buildings)
    assert not unfinished.commands and not townhall.commands
    buildings[0].is_flying = True
    run(act)
    buildings[0].is_flying = False
    run(act)
    assert len(buildings[0].commands) == 2


def test_ramp_point_stays_main_side_and_ignores_frontier_gather_solver():
    act = gather()
    act.zone_manager.own_main_zone.ramp = SimpleNamespace(
        top_center=Point2((100, 110)), bottom_center=Point2((100, 120)))
    act.zone_manager.gather_point = Point2((500, 500))
    assert 103<=act.home_point().y<=108.5
    assert 2<=abs(act.home_point().x-100)<=8
    station=act.home_point()
    act.ai.in_pathing_grid = lambda p: False
    assert act.home_point() == station


def test_completed_natural_becomes_home_point_with_main_fallback():
    act = gather()
    townhall = SimpleNamespace(is_ready=True, is_flying=False, position=Point2((120, 100)))
    natural = SimpleNamespace(
        our_townhall=townhall,
        center_location=Point2((120, 100)),
        gather_point=Point2((116, 100)),
    )
    act.zone_manager.own_natural = natural
    assert act.home_point() == Point2((116, 100))
    townhall.is_ready = False
    assert act.home_point() == Point2((108, 100))


def test_terran_tactics_really_registers_home_gather():
    tactics = TerranAdapter().create_tactics()
    assert any(isinstance(act, PlanHomeGather) for act in tactics.orders)

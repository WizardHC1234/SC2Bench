"""One new base remains one new base after upgrades or mineral depletion."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.units import Units
from sharpy.plans.acts import Expand
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.races import get_adapter


@pytest.mark.parametrize('kind',['lair','hive'])
def test_zerg_expansion_absolute_target_includes_upgraded_base(kind):
    adapter=get_adapter('zerg')
    snapshot=BackendSnapshot(buildings={kind:1},info={'base_count':1})
    act=adapter.create_act(NS(action='build',target='hatchery'),
        to_count=adapter.production_owned_count(snapshot,'build','hatchery')+1)
    assert act.to_count==2


def test_townhall_upgrade_progress_does_not_count_as_a_second_base():
    snapshot=BackendSnapshot(buildings={'command_center':1},
        info={'base_count':1,'under_construction':{'orbital_command':1}})
    assert get_adapter('terran').production_owned_count(snapshot,'build','command_center')==1


@pytest.mark.parametrize('kind,ready,order',[
    (U.COMMANDCENTER,True,None),(U.ORBITALCOMMAND,True,None),
    (U.NEXUS,True,None),(U.LAIR,True,None),(U.HIVE,False,None),
    (U.HATCHERY,False,A.UPGRADETOLAIR_LAIR)])
def test_executor_counts_existing_bases_even_if_their_minerals_are_depleted(kind,ready,order):
    hall=NS(type_id=kind,is_ready=ready,orders=[NS(ability=NS(id=order))] if order else [])
    act=Expand(2);act.ai=NS(townhalls=Units([hall],None))
    act.zone_manager=NS(our_zones_with_minerals=[])
    assert act.current_active_base_count==1


def test_new_unfinished_base_is_left_to_pending_counter():
    act=Expand(2);act.ai=NS(townhalls=Units([NS(type_id=U.NEXUS,is_ready=False,orders=[])],None))
    act.zone_manager=NS(our_zones_with_minerals=[])
    assert act.current_active_base_count==0

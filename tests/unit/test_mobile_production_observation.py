"""Paid eggs, cocoons and warp-ins are production, not unstarted requests."""
from types import SimpleNamespace as NS

import pytest
from sc2.ids.ability_id import AbilityId as A
from sc2.units import Units
from sc2.position import Point2
from sc2bench_env.backends.sharpy.races import get_adapter
from sc2bench_env.backends.sharpy.state_reader import read_snapshot
from tests.unit.test_race_production_orders import snapshot_fixture
from tests.unit.test_platform_information import building


def mobile(kind, tag, *, ready=True, ability=None):
    result=building(kind,tag,ready=ready)
    result.is_structure=False
    if ability is not None:
        result.orders=[NS(ability=NS(id=ability))]
    return result


def read(race, *units):
    ai=snapshot_fixture()
    ai.start_location=Point2((0,0))
    ai.structures=Units([],None);ai.townhalls=Units([],None);ai.units=Units(list(units),None)
    return read_snapshot(ai,get_adapter(race))


@pytest.mark.parametrize('ability,target,count',[
    (A.LARVATRAIN_ROACH,'roach',1),(A.LARVATRAIN_ZERGLING,'zergling',2),
    (A.LARVATRAIN_OVERLORD,'overlord',1),(A.LARVATRAIN_DRONE,'drone',1)])
def test_eggs_report_actual_batch_production_without_completed_inventory(ability,target,count):
    snapshot,production,*_=read('zerg',mobile('EGG',1,ability=ability))
    assert production=={target:count}
    assert not snapshot.units.get(target)


@pytest.mark.parametrize('kind,target',[
    ('BANELINGCOCOON','baneling'),('RAVAGERCOCOON','ravager'),
    ('LURKERMPEGG','lurker'),('OVERLORDCOCOON','overseer'),
    ('TRANSPORTOVERLORDCOCOON','transport_overlord'),('BROODLORDCOCOON','brood_lord')])
def test_morph_cocoons_report_one_target_even_without_an_order(kind,target):
    snapshot,production,*_=read('zerg',mobile(kind,1,ready=False))
    assert production=={target:1}
    assert not snapshot.units.get(target)


@pytest.mark.parametrize('kind,target',[('STALKER','stalker'),('ZEALOT','zealot'),('ARCHON','archon')])
def test_incomplete_protoss_unit_is_not_omitted_or_reported_complete(kind,target):
    snapshot,production,*_=read('protoss',mobile(kind,1,ready=False))
    assert production=={target:1}
    assert not snapshot.units.get(target)


def test_completed_units_and_combat_or_form_orders_are_not_production():
    _,production,*_=read('zerg',mobile('ROACH',1,ability=A.BURROWDOWN_ROACH),
        mobile('QUEEN',2,ability=A.BUILD_CREEPTUMOR_QUEEN))
    assert production=={}


def test_two_eggs_and_a_hatched_unit_are_separate_counts():
    snapshot,production,*_=read('zerg',mobile('EGG',1,ability=A.LARVATRAIN_ROACH),
        mobile('EGG',2,ability=A.LARVATRAIN_ROACH),mobile('ROACH',3))
    assert production=={'roach':2}
    assert snapshot.units['roach']==1

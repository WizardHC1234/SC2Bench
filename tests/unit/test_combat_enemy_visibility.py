"""Historical contacts cannot become the active army's attack center."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from sc2.units import Units
from sc2.ids.unit_typeid import UnitTypeId as U
from tests.unit.test_combat_execution import UnitStub
from sharpy.combat.group_combat_manager import GroupCombatManager
from sharpy.combat.default_micro_methods import DefaultMicroMethods


@pytest.mark.parametrize('flags',[dict(is_memory=True),dict(is_snapshot=True),dict(is_visible=False)])
def test_unseen_contact_is_not_an_attack_group_center(monkeypatch,flags):
    import sharpy.combat.group_combat_manager as module
    live=UnitStub(1,position=(0,0),can_be_attacked=True)
    ghost=UnitStub(2,position=(20,0),can_be_attacked=True,**flags)
    manager=GroupCombatManager();manager.ai=NS(all_enemy_units=Units([live,ghost],None))
    manager.cache=NS(enemy_numpy_vectors=[np.array(u.position) for u in [live,ghost]])
    manager.unit_values=NS(combat_ignore=set());manager.knowledge=NS()
    monkeypatch.setattr(module,'CombatUnits',lambda units,_:NS(units=units))
    groups=manager.group_enemy_units()
    assert [u.tag for g in groups for u in g.units]==[1]


def test_memory_does_not_bridge_two_separate_visible_enemy_groups(monkeypatch):
    import sharpy.combat.group_combat_manager as module
    units=[UnitStub(1,position=(0,0),can_be_attacked=True),
           UnitStub(2,position=(6,0),can_be_attacked=True,is_memory=True),
           UnitStub(3,position=(12,0),can_be_attacked=True)]
    manager=GroupCombatManager();manager.ai=NS(all_enemy_units=Units(units,None))
    manager.cache=NS(enemy_numpy_vectors=[np.array(u.position) for u in units])
    manager.unit_values=NS(combat_ignore=set());manager.knowledge=NS()
    monkeypatch.setattr(module,'CombatUnits',lambda units,_:NS(units=units))
    assert sorted(sorted(u.tag for u in g.units) for g in manager.group_enemy_units())==[[1],[3]]


def test_unit_micro_does_not_kite_around_a_memory_contact():
    unit=UnitStub(1,U.STALKER,position=(0,0))
    unit._proto=NS(pos=NS(x=0,y=0))
    ghost=UnitStub(2,position=(5,0),is_memory=True)
    step=NS(ready_to_shoot=lambda _:True,closest_units={},
        cache=None,knowledge=NS(unit_cache=NS(enemy_in_range=lambda *_:Units([ghost],None))),
        engaged_power=NS(add_units=lambda *_:None),
        unit_values=NS(real_range=lambda *_:6))
    group=NS(closest_target_group=lambda _:None,center=unit.position,units=[unit])
    DefaultMicroMethods.init_micro_group(step,group,Units([unit],None),[],None)
    assert not step.enemies_near_by
    assert 1 not in step.closest_units


@pytest.mark.parametrize('kind',[U.MARINE,U.STALKER,U.HYDRALISK])
def test_a_distant_visible_enemy_does_not_replace_this_armys_objective(kind):
    unit=UnitStub(1,kind,position=(92,45),sight_range=10)
    remote=UnitStub(2,U.BANSHEE,position=(125,25),can_be_attacked=True)
    values=NS(real_range=lambda *_:6)
    group=NS(units=[unit])
    remote_group=NS(units=[remote])
    assert DefaultMicroMethods.local_enemy_groups(group,[remote_group],values)==[]
    remote.position=unit.position.offset((8,0))
    assert DefaultMicroMethods.local_enemy_groups(group,[remote_group],values)==[remote_group]


def test_a_visible_enemy_with_a_weapon_already_in_range_remains_local_contact():
    unit=UnitStub(1,U.STALKER,position=(0,0),sight_range=9)
    tank=UnitStub(2,U.SIEGETANKSIEGED,position=(12,0))
    values=NS(real_range=lambda attacker,_:13 if attacker is tank else 6)
    assert DefaultMicroMethods.local_contact(NS(units=[unit]),tank,values)

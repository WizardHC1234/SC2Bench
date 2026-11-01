"""A contact-triggered drop cannot repeatedly unload into unwalkable terrain."""
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from tests.unit.test_combat_execution import UnitStub, mission


def test_interrupted_drop_moves_from_cliff_to_nearby_ground_before_unloading():
    carrier=UnitStub(1,U.MEDIVAC,flying=True,cargo_used=1)
    act=mission(own=[carrier]);act._unload_here=True
    act.ai.in_pathing_grid=lambda p:p.x>=5
    act.ai.all_units=[]
    act._run_unload(act.ai.units,Point2((100,100)))
    assert carrier.commands[-1][0]=='move'
    landing=carrier.commands[-1][1]
    assert 5.5<=landing.x<=8 and abs(landing.y)<=1
    carrier.position=landing;carrier.commands.clear()
    act._run_unload(act.ai.units,Point2((100,100)))
    assert carrier.commands[-1][0]!='move'
    assert carrier.commands[-1][1]==landing


def test_no_nearby_ground_does_not_send_an_illegal_unload_order():
    carrier=UnitStub(1,U.MEDIVAC,flying=True,cargo_used=1)
    act=mission(own=[carrier]);act._unload_here=True
    act.ai.in_pathing_grid=lambda _:False
    act.ai.all_units=[]
    act._run_unload(act.ai.units,Point2((100,100)))
    assert not carrier.commands
    act.ai.time=11;act._run_unload(act.ai.units,Point2((100,100)))
    assert act.phase=='withdrawing'


def test_home_withdrawal_uses_the_same_ground_check():
    carrier=UnitStub(1,U.MEDIVAC,flying=True,cargo_used=1)
    act=mission(own=[carrier]);act.ai.in_pathing_grid=lambda p:p.x>=3
    act.ai.all_units=[]
    assert not act._run_withdraw(act.ai.units)
    assert carrier.commands[-1][0]=='move' and carrier.commands[-1][1].x>=3.5

"""The audit must respect the installed client's half-supply correction."""
from types import SimpleNamespace as NS
import pytest
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as U
from analysis.platform_audit.probe_interface import expected_native_supply


@pytest.mark.parametrize('race,kinds,expected',[
    (Race.Zerg,[U.ZERGLING]*3,28),
    (Race.Zerg,[U.ZERGLING]*4,27),
    (Race.Zerg,[U.BANELINGCOCOON],28),
    (Race.Terran,[U.MARINE],27),
    (Race.Protoss,[U.STALKER],27),
])
def test_native_supply_comparison_uses_client_correction(race,kinds,expected):
    ai=NS(race=race,units=[NS(type_id=kind) for kind in kinds])
    assert expected_native_supply(ai,NS(food_used=27))==expected

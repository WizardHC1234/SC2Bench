"""Acceptance cannot confuse proposals, one skill, or survival with success."""
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2bench_env.backends.sharpy.acts import ActCombatMission
from sc2bench_env.backends.sharpy.micro import MissionMicroRules
from sc2bench_env.backends.sharpy.terran_micro import cast
from tests.e2e.test_expanded_micro_e2e import SKILL_REQUIREMENTS, _missing_skill_requirements
from tests.unit.test_combat_execution import UnitStub


@pytest.mark.parametrize("skill", SKILL_REQUIREMENTS)
def test_every_required_skill_fails_independently_when_missing(skill):
    evidence = dict.fromkeys(SKILL_REQUIREMENTS, 1)
    del evidence[skill]
    assert _missing_skill_requirements(evidence) == [skill]


def test_bc_survival_does_not_prove_yamato_or_jump():
    evidence = dict.fromkeys(SKILL_REQUIREMENTS, 1)
    evidence.pop("bc_yamato")
    evidence.pop("bc_jump")
    evidence["battlecruiser"] = 1
    assert _missing_skill_requirements(evidence) == ["bc_yamato", "bc_jump"]


def test_raven_cast_orders_do_not_prove_effect_hits():
    evidence = dict.fromkeys(SKILL_REQUIREMENTS, 1)
    evidence["raven_matrix_hit"] = evidence["raven_antiarmor_hit"] = 0
    evidence["raven_matrix"] = evidence["raven_antiarmor"] = 10
    assert _missing_skill_requirements(evidence) == ["raven_matrix_hit", "raven_antiarmor_hit"]


def test_global_proposed_casts_do_not_become_observed_mission_evidence():
    act = ActCombatMission("attack", "zone_1", {"ghost": 1})
    act.ai = SimpleNamespace(
        bench_skill_evidence=dict.fromkeys(SKILL_REQUIREMENTS, 100),
        units=lambda *_: SimpleNamespace(amount=0), enemy_units=[], enemy_structures=[],
    )
    act._refresh_skill_evidence([])
    assert act.skill_evidence == {}


def test_guard_replaced_cast_is_not_recorded_as_success():
    ai = SimpleNamespace()
    micro = SimpleNamespace(ai=ai, cd_manager=SimpleNamespace(used_ability=lambda *_: None))
    unit = SimpleNamespace(tag=1, type_id=UnitTypeId.GHOST, position=Point2((20, 20)))
    proposed = cast(micro, unit, AbilityId.EMP_EMP, Point2((30, 30)))
    rules = MissionMicroRules()
    rules.boundary = lambda _: False
    rules.return_point = Point2((0, 0))
    actual = rules.guard_action(unit, proposed)
    assert actual.ability is None
    assert not hasattr(ai, "bench_skill_evidence")


def test_turret_build_order_does_not_prove_turret_entity_exists():
    act = ActCombatMission("attack", "zone_1", {"raven": 1})
    act.ai = SimpleNamespace(units=lambda *_: SimpleNamespace(amount=0), enemy_units=[], enemy_structures=[])
    raven = UnitStub(1, UnitTypeId.RAVEN, is_cloaked=False,
                     orders=[SimpleNamespace(ability=SimpleNamespace(id=AbilityId.BUILDAUTOTURRET_AUTOTURRET))])
    act._refresh_skill_evidence([raven])
    assert act.skill_evidence.get("raven_turret_order", 0) > 0
    assert act.skill_evidence.get("raven_turret", 0) == 0


def test_auto_turret_structure_is_observed_as_real_entity():
    act = ActCombatMission("attack", "zone_1", {"raven": 1})
    act.ai = SimpleNamespace(
        units=lambda *_: SimpleNamespace(amount=0),
        structures=lambda kind: SimpleNamespace(amount=1 if kind == UnitTypeId.AUTOTURRET else 0),
        enemy_units=[], enemy_structures=[],
    )
    act._refresh_skill_evidence([])
    assert act.skill_evidence["raven_turret"] == 1


def test_observed_lock_buff_proves_hit_without_order_hint():
    from sc2.ids.buff_id import BuffId
    act = ActCombatMission("attack", "zone_1", {"cyclone": 1})
    act.ai = SimpleNamespace(
        units=lambda *_: SimpleNamespace(amount=0),
        enemy_units=[SimpleNamespace(has_buff=lambda buff: buff == BuffId.LOCKON)],
        enemy_structures=[],
    )
    act._is_visible_enemy = lambda _: True
    act._refresh_skill_evidence([])
    assert act.skill_evidence["cyclone_lock_hit"] == 1
    assert act.skill_evidence.get("cyclone_lock", 0) == 0


@pytest.mark.parametrize("ids,version,expected", [
    ([280], "", True), ([279], "5.0.16.97563", False),
    ([300], "5.0.16.97563", True), ([300], "5.0.15.95299", False),
    ([300], "", False), ([999999], "5.0.16.97563", False),
])
def test_antiarmor_buff_alias_is_build_scoped_not_target_tint(ids, version, expected):
    from sc2bench_env.backends.sharpy.compat import has_antiarmor_debuff
    unit = SimpleNamespace(_proto=SimpleNamespace(buff_ids=ids),
                           has_buff=lambda buff: buff.value in ids)
    assert has_antiarmor_debuff(unit, version) is expected

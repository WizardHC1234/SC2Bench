"""Complete multiplayer Terran catalog/adapter contract checks."""
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.ability_id import AbilityId
from sc2bench_env.interface.action_catalog import targets_for_action, get_target
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter, UNITS, RESEARCH, combat_unit_types
from sc2bench_env.interface.actions import parse_decision

ROSTER = {"scv", "marine", "marauder", "reaper", "ghost", "hellion", "hellbat",
          "widow_mine", "cyclone", "siege_tank", "thor", "viking", "medivac",
          "liberator", "raven", "banshee", "battlecruiser"}


def test_complete_train_and_combat_roster():
    assert set(UNITS) == ROSTER
    assert {s.name for s in targets_for_action("train")} == ROSTER
    parse_decision([{"action": "combat", "style": "attack", "target": "zone_0",
                     "units": {name: 1 for name in ROSTER - {"scv"}}}, {"action": "wait"}])


@pytest.mark.parametrize("spec", targets_for_action("train") + targets_for_action("build") + targets_for_action("research"), ids=lambda s: s.name)
def test_every_catalog_target_has_executable_adapter_and_observation_mapping(spec):
    adapter = TerranAdapter()
    act = adapter.create_act(SimpleNamespace(action=spec.action, target=spec.name), to_count=1)
    assert act is not None
    if spec.action == "train":
        assert adapter.normalize_unit_name(UNITS[spec.name][0].name) == spec.name
    if spec.action == "research":
        assert adapter.normalize_upgrade_name(RESEARCH[spec.name].name) == spec.name


@pytest.mark.parametrize("unit_type,name", [
    (UnitTypeId.VIKINGASSAULT, "viking"), (UnitTypeId.LIBERATORAG, "liberator"),
    (UnitTypeId.WIDOWMINEBURROWED, "widow_mine"), (UnitTypeId.THORAP, "thor"),
    (UnitTypeId.SIEGETANKSIEGED, "siege_tank"), (UnitTypeId.HELLIONTANK, "hellbat"),
])
def test_alternate_forms_share_mission_identity(unit_type, name):
    assert unit_type in combat_unit_types(name)
    assert TerranAdapter().normalize_unit_name(unit_type.name) == name


def test_modern_costs_and_required_tech():
    assert get_target("viking").minerals == 125
    assert get_target("ghost").supply == 3
    assert "ghost_academy" in get_target("ghost").prerequisites
    assert "fusion_core" in get_target("battlecruiser").prerequisites
    assert "armory" in get_target("hellbat").prerequisites
    assert "infantry_weapons_2" in get_target("infantry_weapons_3").prerequisites


def test_missing_ability_registration_is_idempotent_and_preserves_existing_ids():
    from sc2bench_env.backends.sharpy.compat import register_modern_terran_abilities
    original = dict(AbilityId._value2member_map_)
    register_modern_terran_abilities()
    first = dict(AbilityId._value2member_map_)
    register_modern_terran_abilities()
    assert first == AbilityId._value2member_map_
    assert all(AbilityId(value) is member for value, member in original.items())
    assert AbilityId(807).value == 807
    assert AbilityId(1535).value == 1535
    from sc2.game_data import AbilityData
    assert AbilityData.id_exists(807) and AbilityData.id_exists(1535)


def test_medivac_research_generic_remap_does_not_merge_distinct_orders():
    from sc2bench_env.backends.sharpy.compat import register_modern_terran_abilities
    register_modern_terran_abilities()
    energy = SimpleNamespace(id=AbilityId.STARPORTTECHLABRESEARCH_RESEARCHMEDIVACENERGYUPGRADE,
                             exact_id=AbilityId(1535), button_name="MedivacEnergyRegeneration")
    speed = SimpleNamespace(id=energy.id,
                            exact_id=AbilityId.FUSIONCORERESEARCH_RESEARCHRAPIDREIGNITIONSYSTEM,
                            button_name="RapidReignitionSystem")
    game_data = SimpleNamespace(upgrades={
        UpgradeId.MEDIVACCADUCEUSREACTOR.value: SimpleNamespace(research_ability=energy),
        UpgradeId.MEDIVACINCREASESPEEDBOOST.value: SimpleNamespace(research_ability=speed),
    })
    adapter = TerranAdapter()
    assert adapter.research_target_from_order(energy, game_data) == "caduceus_reactor"
    assert adapter.research_target_from_order(speed, game_data) is None
    act = adapter.create_act(SimpleNamespace(action="research", target="caduceus_reactor"), to_count=1)
    act.ai = SimpleNamespace(_game_data=game_data, state=SimpleNamespace(upgrades=set()))
    assert act.solve_ability() == energy.exact_id
    assert act.already_pending_upgrade([SimpleNamespace(orders=[SimpleNamespace(ability=speed, progress=.5)])]) == 0
    assert act.already_pending_upgrade([SimpleNamespace(orders=[SimpleNamespace(ability=energy, progress=.5)])]) == .5


def test_yamato_uses_runtime_upgrade_for_cost_and_completion():
    assert RESEARCH["yamato_cannon"] == UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS
    assert get_target("rapid_reignition_system") is None  # Replaced by Caduceus in 5.0.12.


def test_upgrade_prerequisite_requires_completed_previous_level():
    from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
    macro = ActOngoingMacroTasks([])
    macro.ai = SimpleNamespace(state=SimpleNamespace(upgrades=set()))
    assert macro._count_ready("infantry_weapons_1") == 0
    macro.ai.state.upgrades.add(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)
    assert macro._count_ready("infantry_weapons_1") == 1


def test_fake_upgrade_prerequisite_requires_completed_previous_level():
    from sc2bench_env.backends.fake import FakeBackend
    backend = FakeBackend()
    backend.buildings.update(engineering_bay=1, armory=1)
    backend.in_progress_research.add("infantry_weapons_1")
    assert not backend._has_prereqs("infantry_weapons_2")
    backend.upgrades.add("infantry_weapons_1")
    assert backend._has_prereqs("infantry_weapons_2")


@pytest.mark.parametrize("level", (1, 2, 3))
def test_combined_armour_uses_active_ability_not_retired_swarm_data(level):
    target = f"vehicle_ship_armor_{level}"
    upgrade = RESEARCH[target]
    retired = getattr(AbilityId, f"ARMORYRESEARCHSWARM_TERRANVEHICLEANDSHIPPLATINGLEVEL{level}")
    active = getattr(AbilityId, f"ARMORYRESEARCH_TERRANVEHICLEANDSHIPPLATINGLEVEL{level}")
    game_data = SimpleNamespace(upgrades={upgrade.value: SimpleNamespace(
        research_ability=SimpleNamespace(exact_id=retired, id=retired))})
    adapter = TerranAdapter()
    act = adapter.create_act(SimpleNamespace(action="research", target=target), to_count=1)
    act.ai = SimpleNamespace(_game_data=game_data)
    assert act.solve_ability() == active
    order_ability = SimpleNamespace(exact_id=active, id=active, button_name=f"CombinedPlating{level}")
    assert adapter.research_target_from_order(order_ability, game_data) == target

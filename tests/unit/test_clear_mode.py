"""Clear-map orders share the army lifecycle and use only observed targets."""
import asyncio
from types import SimpleNamespace

import pytest

from sc2bench_env import Environment, ToolCall
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import ActionValidationError, parse_game_action
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.observation_text import brief_combat_lines, combat_lines
from sc2bench_env.interface.tools import tool_specs, validate_tool_arguments
from sc2bench_env.adapters.llm.adapter import LLMAdapter
from sc2bench_env.adapters.llm.tool_results import render_tool_result
from tests.unit.test_combat_execution import UnitStub, mission

from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId
from sc2.units import Units
from sc2bench_env.backends.sharpy.clear_search import SearchTarget
from sc2bench_env.backends.sharpy.zones import ZoneRegistry


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


def decision(env, *calls, seconds=1):
    turn = env.begin_tool_turn()
    results = [turn.call(ToolCall(item["name"], item["arguments"])) for item in calls]
    assert turn.call(ToolCall("advance", {"seconds": seconds})).status == "decision_ready"
    obs, feedback, terminated, info = env.step(turn.finish())
    return obs, feedback, results


@pytest.mark.parametrize("race,unit", [("terran", "marine"), ("protoss", "stalker"), ("zerg", "roach")])
def test_clear_contract_roundtrip_and_schema(race, unit):
    entries = [call("attack_group", group="group_1", mode="clear"),
               call("dispatch_group", source="group_0", units={unit: 3}, order="attack", mode="clear")]
    specs = {spec.name: spec for spec in tool_specs(race)}
    for entry in entries:
        action = parse_game_action(entry, race=race)
        assert action.style == "clear" and action.target is None
        assert action.to_tool_call() == entry
        assert validate_tool_arguments(specs[entry["name"]], entry["arguments"]) is None
    assert "search" not in specs and "search_group" not in specs


@pytest.mark.parametrize("entry", [
    call("attack_group", group="group_0", mode="clear"),
    call("attack_group", group="group_1", mode="clear", target="zone_1"),
    call("attack_group", group="group_1", mode="unknown"),
    call("attack_group", group="group_1"),
    call("dispatch_group", source="group_0", units={"marine": 1}, order="defend", mode="clear"),
    call("dispatch_group", source="group_0", units={"marine": 1}, order="clear"),
    call("defend_group", group="group_1", mode="clear"),
])
def test_ambiguous_or_invalid_clear_calls_are_rejected(entry):
    with pytest.raises(ActionValidationError):
        parse_game_action(entry)


def test_clear_json_schema_matches_public_tool_validation():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft7Validator(decision_json_schema())
    validator.validate([call("attack_group", group="group_1", mode="clear"), call("advance", seconds=1)])
    validator.validate([call("dispatch_group", source="group_0", units={"marine": 2}, order="attack", mode="clear"), call("advance", seconds=1)])
    for arguments in ({"mode": "clear", "target": "zone_1"}, {"mode": "unknown"}, {}):
        assert list(validator.iter_errors([call("attack_group", group="group_1", **arguments), call("advance", seconds=1)]))


@pytest.mark.parametrize("race,unit", [("terran", "marine"), ("protoss", "stalker"), ("zerg", "roach")])
def test_clear_switch_reinforce_split_and_retreat_keep_identity(race, unit):
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.units[unit] = 10
        obs, _, results = decision(env, call("dispatch_group", source="group_0", units={unit: 4}, order="attack", mode="clear"))
        assert results[0].status == "staged"
        assert obs.combat["group_1"]["style"] == "clear"
        assert obs.combat["group_0"]["alive"] == {unit: 6}
        task = next(d for d in env.task_manager.active_demands() if d.group == "group_1")
        revision, started = task.command_revision, task.order_started_at
        obs, _, results = decision(env, call("attack_group", group="group_1", mode="clear"), seconds=12)
        assert results[0].status == "no_op"
        assert task.command_revision == revision and task.order_started_at == started
        assert obs.combat["group_1"]["search"]["visited"] > 0
        obs, _, _ = decision(env, call("reinforce_group", source="group_0", destination="group_1", units={unit: 2}))
        assert obs.combat["group_1"]["style"] == "clear"
        assert obs.combat["group_1"]["alive"] == {unit: 6}
        obs, _, _ = decision(env, call("dispatch_group", source="group_1", units={unit: 2}, order="defend", target="zone_1"))
        assert obs.combat["group_1"]["style"] == "clear"
        assert obs.combat["group_1"]["alive"] == {unit: 4}
        obs, _, _ = decision(env, call("attack_group", group="group_1", target="zone_2"))
        assert obs.combat["group_1"]["style"] == "attack" and "search" not in obs.combat["group_1"]
        obs, _, _ = decision(env, call("attack_group", group="group_1", mode="clear"))
        assert obs.combat["group_1"]["style"] == "clear"
        obs, _, _ = decision(env, call("defend_group", group="group_1", target="zone_3"))
        assert obs.combat["group_1"]["style"] == "defend"
        obs, _, _ = decision(env, call("attack_group", group="group_1", mode="clear"))
        obs, _, _ = decision(env, call("retreat_group", group="group_1"), seconds=5)
        assert "group_1" not in obs.combat and obs.combat["group_0"]["alive"] == {unit: 8}
        assert obs.combat["group_2"]["alive"] == {unit: 2}
    finally:
        env.close()


def test_clear_staging_does_not_mutate_committed_search_or_report_arrival():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 6
        obs, _, _ = decision(env, call("dispatch_group", source="group_0", units={"marine": 3}, order="attack", mode="clear"), seconds=12)
        turn = env.begin_tool_turn()
        before = env.backend.snapshot().info["combat_progress"]
        result = turn.call(ToolCall("defend_group", {"group": "group_1", "target": "zone_1"}))
        assert result.status == "staged" and env.backend.snapshot().info["combat_progress"] == before
        text = LLMAdapter().render_tool_result(ToolCall("defend_group", {}), result)
        assert "clear -> defend zone_1" in text and "arrived" not in text
    finally:
        env.close()


def test_outbound_split_and_merge_into_clear_keep_destination_order():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 10
        decision(env, call("dispatch_group", source="group_0", units={"marine": 4}, order="attack", target="zone_2"),
                 call("dispatch_group", source="group_0", units={"marine": 2}, order="defend", target="zone_1"))
        obs, _, results = decision(env, call("dispatch_group", source="group_1", units={"marine": 2}, order="attack", mode="clear"))
        assert results[0].status == "staged" and obs.combat["group_3"]["alive"] == {"marine": 2}
        assert obs.combat["group_1"]["style"] == "attack" and obs.combat["group_1"]["alive"] == {"marine": 2}
        obs, _, _ = decision(env, call("reinforce_group", source="group_1", destination="group_3", units={"marine": 2}))
        assert obs.combat["group_3"]["alive"] == {"marine": 4}
        obs, _, _ = decision(env, call("merge_groups", destination="group_3", sources=["group_2"]))
        assert obs.combat["group_3"]["style"] == "clear" and obs.combat["group_3"]["alive"] == {"marine": 6}
        assert "group_2" not in obs.combat and obs.combat["group_0"]["alive"] == {"marine": 4}
    finally:
        env.close()


def test_clear_obeys_existing_queen_attack_restriction_on_dispatch_retarget_and_transfers():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="zerg"))
        env.backend.units.update(queen=1, roach=2)
        _, _, results = decision(env, call("dispatch_group", source="group_0", units={"queen": 1}, order="attack", mode="clear"))
        assert results[0].status == "rejected" and not env.task_manager.active_demands()
        decision(env, call("dispatch_group", source="group_0", units={"queen": 1}, order="defend", target="zone_0"),
                 call("dispatch_group", source="group_0", units={"roach": 2}, order="attack", mode="clear"))
        for entry in (call("attack_group", group="group_1", mode="clear"),
                      call("reinforce_group", source="group_1", destination="group_2", units={"queen": 1}),
                      call("merge_groups", destination="group_2", sources=["group_1"])):
            obs, _, results = decision(env, entry)
            assert results[0].status == "rejected"
            assert obs.combat["group_1"]["style"] == "defend" and obs.combat["group_2"]["style"] == "clear"
    finally:
        env.close()


def test_search_feedback_is_visible_in_brief_full_and_query():
    row = {"style": "clear", "target": None, "status": "active", "phase": "searching", "alive": {"marine": 2},
           "search": {"visited": 3, "total": 16, "sweeps": 1, "target_kind": "search_point", "position": [12, 34],
                      "blocked": {"cannot_attack_air": 1}}}
    for text in ("\n".join(brief_combat_lines({"group_1": row})),
                 "\n".join(combat_lines({"group_1": row})),
                 render_tool_result("query_army_state", {"groups": {"group_1": row}})):
        assert "points inspected 3/16" in text
        assert "cannot attack air 1" in text
        assert "clear unknown" not in text


def fixture(own=(), enemies=(), structures=()):
    act = mission("clear", own, enemies, structures)
    act.zone_id = ""
    registry = ZoneRegistry()
    registry.sync_from_centers([(0, 0), (40, 0), (80, 0)])
    act.ai.zone_registry = registry
    act.ai.is_visible = lambda point: False
    act.ai.in_pathing_grid = lambda point: True
    return act, registry


def update(act, registry, **kwargs):
    return asyncio.run(act.clear_search.update(act.ai, registry, act.ai.units, act._can_hit,
                                             act.unit_values.real_range, **kwargs))


def building(tag, position, **kwargs):
    return UnitStub(tag, UnitTypeId.SUPPLYDEPOT, position, is_structure=True, ground_weapon=False, **kwargs)


def test_search_targets_actual_visible_building_position_and_stays_stable():
    first = building(10, (65, 9))
    act, registry = fixture([UnitStub(1)], structures=[first])
    assert update(act, registry) == first.position
    assert act.clear_search.current.zone_id == "zone_2"
    act.ai.enemy_structures.append(building(11, (12, 0)))
    assert update(act, registry) == first.position  # no nearer-target oscillation


def test_last_seen_target_precedes_map_search_but_visible_structure_preempts_it():
    remembered = building(10, (62, 0))
    act, registry = fixture([UnitStub(1)], structures=[remembered])
    update(act, registry)
    act.ai.enemy_structures = Units([], act.ai)
    assert update(act, registry) == remembered.position
    assert act.clear_search.current.kind == "last_seen_structure"
    fresh = building(11, (77, 0))
    act.ai.enemy_structures = Units([fresh], act.ai)
    assert update(act, registry) == fresh.position


def test_fog_snapshot_is_not_a_visible_target_and_does_not_reveal_hidden_position():
    act, registry = fixture([UnitStub(1)], structures=[building(10, (70, 0), is_snapshot=True)])
    update(act, registry)
    assert act.clear_search.current.kind == "search_point"
    assert not act.clear_search._known


def test_destroyed_structure_resumes_search_without_defend_transition():
    enemy = building(10, (30, 0))
    act, registry = fixture([UnitStub(1)], structures=[enemy])
    update(act, registry)
    act.ai.enemy_structures = Units([], act.ai)
    act.ai.is_visible = lambda point: point.distance_to(enemy.position) < 2
    update(act, registry)
    assert act.clear_search.current.kind == "search_point"
    assert act.style == "clear"


def test_local_combat_preserves_route_and_resumes_after_contact():
    act, registry = fixture([UnitStub(1)])
    update(act, registry)
    original = act.clear_search.current
    act.ai.time = 60
    assert update(act, registry, in_contact=True) == original.position
    assert act.clear_search.current == original and act.clear_search.phase == "engaging"
    act.ai.is_visible = lambda point: point == original.position
    update(act, registry)
    assert original.key in act.clear_search.visited
    assert act.clear_search.current.key != original.key


def test_incompatible_air_target_is_reported_and_anti_air_reinforcement_unlocks_it():
    flying = building(10, (40, 0), flying=True)
    act, registry = fixture([UnitStub(1, UnitTypeId.ZERGLING)], structures=[flying])
    update(act, registry)
    assert act.clear_search.current.kind == "search_point"
    assert act.clear_search.observation()["blocked"] == {"cannot_attack_air": 1}
    act.ai.units.append(UnitStub(2, UnitTypeId.HYDRALISK, air_weapon=True))
    assert update(act, registry) == flying.position
    assert "blocked" not in act.clear_search.observation()


def test_detection_block_is_not_reported_as_a_clear_map():
    hidden = building(10, (40, 0), can_be_attacked=False, is_cloaked=True)
    act, registry = fixture([UnitStub(1)], structures=[hidden])
    update(act, registry)
    assert act.clear_search.observation()["blocked"] == {"detection_required": 1}
    hidden.can_be_attacked = True
    assert update(act, registry) == hidden.position


@pytest.mark.parametrize("race,form,weapon_form,target_flying,weapon_type", [
    ("terran", UnitTypeId.VIKINGFIGHTER, UnitTypeId.VIKINGASSAULT, False, 1),
    ("terran", UnitTypeId.VIKINGASSAULT, UnitTypeId.VIKINGFIGHTER, True, 2),
    ("zerg", UnitTypeId.LURKERMP, UnitTypeId.LURKERMPBURROWED, False, 1),
])
def test_clear_does_not_mistake_an_alternate_weapon_form_for_missing_capability(race, form, weapon_form, target_flying, weapon_type):
    from sc2bench_env.backends.sharpy.races import get_adapter

    unit = UnitStub(1, form, ground_weapon=False, air_weapon=False, can_attack=False)
    act, _ = fixture([unit])
    act.ai.adapter = get_adapter(race)
    act.ai._game_data = SimpleNamespace(units={weapon_form.value: SimpleNamespace(
        _proto=SimpleNamespace(weapons=[SimpleNamespace(type=weapon_type, range=6, damage=10)]),
    )})
    enemy = building(10, (40, 0), flying=target_flying)
    assert not act._can_hit(unit, enemy)
    assert act._clear_weapon_range(unit, enemy) == 6


def test_unreachable_target_does_not_stop_other_search_or_consume_group():
    inaccessible, reachable = building(10, (40, 0)), building(11, (80, 0))
    act, registry = fixture([UnitStub(1)], structures=[inaccessible, reachable])

    async def path(origin, target):
        return None if 30 < target.x < 50 else origin.distance_to(target)

    act.ai.client = SimpleNamespace(query_pathing=path)
    assert update(act, registry) == reachable.position
    assert act.clear_search.observation()["blocked"] == {"unreachable": 1}
    assert act.end_reason is None and act.failure_reason is None


def test_stalled_search_retries_another_point_instead_of_moving_forever():
    act, registry = fixture([UnitStub(1, position=(20, 0))])
    update(act, registry)
    original = act.clear_search.current.key
    act.ai.time = 21
    update(act, registry)
    assert act.clear_search.current.key != original
    assert "movement_stalled" in act.clear_search.observation()["blocked"]


def test_inaccessible_points_do_not_prevent_a_new_sweep_of_previously_seen_ground():
    act, registry = fixture([UnitStub(1)])
    search = act.clear_search
    search._initialized = True
    search.route = [SearchTarget("one", Point2((0, 0)), "search_point"),
                    SearchTarget("island", Point2((80, 0)), "search_point")]
    act.ai.is_visible = lambda point: point.x < 10
    act.ai.in_pathing_grid = lambda point: point.x < 10
    assert update(act, registry) is None
    assert search.visited == {"one"} and search.observation()["skipped"] == 1
    act.ai.is_visible = lambda point: False
    act.ai.time = 1
    assert update(act, registry) == Point2((0, 0))
    assert search.sweeps == 1 and search.current.key == "one"


def test_search_grid_includes_non_expansion_locations_and_never_claims_victory():
    act, registry = fixture([UnitStub(1)])
    act.ai.game_info = SimpleNamespace(playable_area=SimpleNamespace(x=0, y=0, width=100, height=100))
    update(act, registry)
    assert any(target.position.y > 50 for target in act.clear_search.route)
    act.ai.is_visible = lambda point: True
    act.ai.time = 30
    update(act, registry)
    assert act.clear_search.sweeps == 1
    assert act.end_reason is None and act.style == "clear"
    assert "victory" not in str(act.clear_search.observation()).lower()


def test_ground_approach_offsets_stay_inside_playable_map_bounds():
    act, registry = fixture([UnitStub(1)])
    registry.sync_from_centers([(91.75, 91.75)])
    act.ai.game_info = SimpleNamespace(playable_area=SimpleNamespace(x=0, y=0, width=100, height=100))

    def pathable(point):
        assert 0 <= point.rounded.x < 100 and 0 <= point.rounded.y < 100
        return point.x > 1 and point.y > 1

    act.ai.in_pathing_grid = pathable
    target = update(act, registry)
    assert target is not None and target.x > 1 and target.y > 1
    assert all(0 <= point.position.rounded.x < 100 and 0 <= point.position.rounded.y < 100
               for point in act.clear_search.route)


def test_native_clear_order_keeps_cargo_and_same_revision_keeps_progress():
    carrier = UnitStub(2, UnitTypeId.MEDIVAC, flying=True, cargo_used=1)
    act = mission("attack", [carrier])
    act._tags, act._bound = [1, 2], True
    act.update_order("clear", "", False, 1)
    assert act._tags == [1, 2] and act.phase == "transit"
    search = act.clear_search
    search.visited.add("existing_progress")
    act.update_order("clear", "", False, 1)
    assert act.clear_search is search and "existing_progress" in search.visited
    act.update_order("defend", "zone_1", False, 2)
    assert act.style == "defend" and act._tags == [1, 2] and not act.clear_search.visited


def test_reissuing_clear_can_cancel_a_pending_recall_without_resetting_search():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="protoss"))
        env.backend.units["stalker"] = 2
        decision(env, call("dispatch_group", source="group_0", units={"stalker": 2}, order="attack", mode="clear"))
        decision(env, call("retreat_group", group="group_1", method="recall"))
        demand = next(d for d in env.task_manager.active_demands() if d.group == "group_1")
        assert demand.recall_status == "pending"
        _, _, results = decision(env, call("attack_group", group="group_1", mode="clear"))
        assert results[0].status == "staged" and demand.recall_status is None
    finally:
        env.close()
    act = mission("clear", [UnitStub(1)])
    act._tags, act._bound, act.zone_id = [1], True, ""
    search = act.clear_search
    search.visited.add("inspected")
    act.update_order("clear", "", False, 1, "recall", "pending")
    assert act.recall_status == "pending"
    act.update_order("clear", "", False, 2)
    assert act.recall_status is None and act.retreat_method == "move"
    assert act.clear_search is search and "inspected" in search.visited


@pytest.mark.parametrize("unit_type", [UnitTypeId.MARINE, UnitTypeId.STALKER, UnitTypeId.ROACH])
def test_native_clear_execute_binds_only_existing_group_and_keeps_searching(unit_type):
    unit = UnitStub(1, unit_type)
    act, registry = fixture([unit])
    act._bound, act._tags = True, [1]
    act._resolve_zone = lambda: (registry, None)
    act._collect_mission_units = lambda: (act.ai.units, set())
    act._refresh_alive_counts = lambda *args: None
    commands = []

    async def drive(units, point, move_type):
        commands.append((set(unit.tag for unit in units), point))

    act._drive_combat = drive
    assert asyncio.run(act.execute()) is False
    assert act.clear_search.current is not None
    assert commands and commands[0][0] == {1}
    assert act.style == "clear" and act._tags == [1]

"""Group membership changes preserve stable IDs and unit ownership."""

from tests.helpers.drive import drive, query

from sc2bench_env import Environment, ToolCall
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.observations import agent_observation_view
from sc2bench_env.runtime.query_tools import query_production_state
from sc2bench_env.runtime.observation_builder import compact_production_priority


def _advance(env, *calls):
    return drive(env, [*calls, {"name": "advance", "arguments": {"seconds": 1}}])


def _dispatch(count, target):
    return {
        "name": "combat",
        "arguments": {"style": "attack", "target": target, "units": {"marine": count}},
    }


def test_reinforce_existing_group_without_creating_another_group():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 12
        _advance(env, _dispatch(4, "zone_15"))
        observation, feedback, _, _ = _advance(env, {
            "name": "combat", "arguments": {"group": "group_1", "units": {"marine": 3}},
        })
        assert feedback.receipts[0].result == "accepted"
        assert set(observation.combat) == {"group_0", "group_1"}
        assert observation.combat["group_1"]["alive"] == {"marine": 7}
        assert observation.combat["group_0"]["alive"] == {"marine": 5}
        assert observation.combat["group_1"]["reinforcements"] == [{"marine": 3}]
        assert env.task_manager.group_counter == 1
        call = ToolCall("query_army_state", {"groups": ["group_1"]})
        text = LLMAdapter().render_tool_result(call, query(env, call))
        assert "Requested total: marine x7" in text
        assert "Reinforcement 1: marine x3" in text
        assert "{'marine':" not in text
    finally:
        env.close()


def test_repeated_reinforcement_keeps_one_outbound_group():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 20
        _advance(env, _dispatch(2, "zone_15"))
        for _ in range(10):
            observation, feedback, _, _ = _advance(env, {
                "name": "combat",
                "arguments": {"group": "group_1", "units": {"marine": 1}},
            })
            assert feedback.receipts[0].result == "accepted"
        assert set(observation.combat) == {"group_0", "group_1"}
        assert observation.combat["group_1"]["alive"] == {"marine": 12}
        assert env.task_manager.group_counter == 1
    finally:
        env.close()


def test_merge_groups_keeps_destination_and_ends_sources():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 12
        _advance(env, _dispatch(4, "zone_15"), _dispatch(3, "zone_14"))
        observation, feedback, _, _ = _advance(env, {
            "name": "merge_groups",
            "arguments": {"destination": "group_1", "sources": ["group_2"]},
        })
        assert feedback.receipts[0].result == "accepted"
        assert set(observation.combat) == {"group_0", "group_1"}
        assert observation.combat["group_1"]["alive"] == {"marine": 7}
        assert observation.combat["group_1"]["merged_from"] == ["group_2"]
        assert any(
            event.get("type") == "group_merged"
            and event.get("source") == "group_2"
            and event.get("destination") == "group_1"
            for event in observation.recent_events
        )
    finally:
        env.close()


def test_merge_multiple_sources_keeps_one_destination():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 15
        _advance(
            env,
            _dispatch(4, "zone_15"),
            _dispatch(3, "zone_14"),
            _dispatch(2, "zone_13"),
        )
        observation, feedback, _, _ = _advance(env, {
            "name": "merge_groups",
            "arguments": {
                "destination": "group_1",
                "sources": ["group_2", "group_3"],
            },
        })
        assert feedback.receipts[0].result == "accepted"
        assert set(observation.combat) == {"group_0", "group_1"}
        assert observation.combat["group_1"]["alive"] == {"marine": 9}
        merged = [
            event for event in observation.recent_events
            if event.get("type") == "group_merged"
        ]
        assert {(row["source"], row["destination"]) for row in merged} == {
            ("group_2", "group_1"), ("group_3", "group_1"),
        }
        aggregate = next(
            event for event in observation.recent_events
            if event.get("type") == "groups_merged"
        )
        assert aggregate["sources"] == ["group_2", "group_3"]
        assert aggregate["members"] == {
            "group_2": {"marine": 3},
            "group_3": {"marine": 2},
        }
    finally:
        env.close()


def test_invalid_merge_is_atomic():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 8
        _advance(env, _dispatch(4, "zone_15"))
        observation, feedback, _, _ = _advance(env, {
            "name": "merge_groups",
            "arguments": {"destination": "group_1", "sources": ["group_9"]},
        })
        assert feedback.receipts[0].result == "rejected"
        assert feedback.receipts[0].reason == "group_not_found"
        assert observation.combat["group_1"]["alive"] == {"marine": 4}
    finally:
        env.close()


def test_dynamic_read_tools_share_the_frozen_snapshot_time():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        production = query(env, ToolCall("query_production_state", {}))
        army = query(env, ToolCall("query_army_state", {}))
        assert production["as_of_game_time"] == army["as_of_game_time"] == 0
        assert "persistent_work" in production
        assert "group_0" in army["groups"]
    finally:
        env.close()


def test_production_query_target_filter_keeps_related_infrastructure_and_live_slots():
    result = query_production_state(
        {
            "production": [
                {"facility": "barracks", "ready_grounded": 1, "free_slots": 1},
                {"facility": "factory", "ready_grounded": 1, "free_slots": 1},
            ],
            "building": {
                "barracks": {"completed": 1, "under_construction": 0},
                "factory": {"completed": 1, "under_construction": 0},
            },
        },
        targets=["marine"],
    )

    assert "facilities" not in result
    assert result["production_capacity"] == [{"facility":"barracks","ready_grounded":1,"free_slots":1}]
    assert result["infrastructure"] == {
        "barracks": {"completed": 1, "under_construction": 0},
    }


def test_brief_priority_groups_equivalent_requests_but_query_keeps_full_order():
    rows = [
        {
            "action": "train", "target": "marine", "state": "waiting_to_start",
            "remaining": 4, "in_production": 0, "waiting_to_produce": 4,
            "waiting_for": "supply",
        },
        {
            "action": "build", "target": "supply_depot", "state": "waiting_to_start",
            "remaining": 1, "waiting_for": "minerals",
        },
        {
            "action": "train", "target": "marine", "state": "waiting_to_start",
            "remaining": 3, "in_production": 1, "waiting_to_produce": 2,
            "waiting_for": "supply",
        },
    ]

    brief = compact_production_priority(rows)
    assert len(brief) == 3
    assert [r["target"] for r in brief] == ["marine", "supply_depot", "marine"]
    assert brief[0]["remaining"] == 4 and brief[2]["remaining"] == 3
    assert brief[0]["in_production"] == 0 and brief[2]["in_production"] == 1
    full = query_production_state({"production_priority": rows})
    assert full["persistent_work"] == rows


def test_agent_observation_is_a_brief_copy_while_queries_keep_full_snapshot():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        full, _, _, _ = _advance(env)
        public = agent_observation_view(full)
        assert public is not full
        assert public.map_topology == {}
        assert len(public.zone_state) <= len(full.zone_state)
        assert public.zones == public.relevant_zone_ids
        assert public.text_previous.get("map_topology") is None
        assert env.latest_observation is full
        overview = query(env, ToolCall("query_map_overview", {}))
        assert overview["map_topology"]["zones"]
    finally:
        env.close()


def test_sharpy_group_absorb_transfers_tags_without_releasing_home_pool():
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.units import Units
    from tests.unit.test_combat_execution import UnitStub, mission

    passenger = UnitStub(4)
    units = [
        UnitStub(1),
        UnitStub(2),
        UnitStub(
            3, UnitTypeId.MEDIVAC, flying=True,
            passengers=[passenger], cargo_used=1,
        ),
    ]
    destination = mission(own=units)
    source = mission(own=units)
    source.ai = destination.ai
    source.roles = destination.roles
    destination.ai.bench_combat_tags = {1, 2, 3}
    destination.ai.bench_group0_tags = set()
    destination.ai.units = Units(units, destination.ai)
    destination._bound, destination._tags = True, [1]
    source.units = {"marine": 2, "medivac": 1}
    source._bound, source._tags = True, [2, 3, 4]

    destination.absorb_group(source, destination_group="group_1")

    assert destination._tags == [1, 2, 3, 4]
    assert source._tags == []
    assert source.end_reason == "merged_into:group_1"
    assert source.merge_transferred_counts == {"marine": 2, "medivac": 1}
    assert destination.ai.bench_combat_tags == {1, 2, 3, 4}
    assert destination.ai.bench_group0_tags == set()

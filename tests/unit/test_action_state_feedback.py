"""Immediate command state must reflect staging, not simulated execution."""
import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from sc2bench_env.adapters.llm import LLMAdapter


def call(turn, name, **args):
    return turn.call(ToolCall(name, args))


def commit(env, turn):
    call(turn, "advance", seconds=1)
    return env.step(turn.finish())


@pytest.mark.parametrize("race,worker", [("terran", "scv"), ("protoss", "probe"), ("zerg", "drone")])
def test_queue_includes_prior_staged_work_and_cancel_without_advancing(tmp_path, race, worker):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(race=race))
        turn = env.begin_tool_turn()
        call(turn, "train", target=worker, count=3)
        result = call(turn, "train", target=worker, count=2)
        rows = result.data["production_tasks"]
        assert sum(r["remaining"] for r in rows) == 5
        assert all(r["submission"] == "staged" for r in rows)
        cancelled = call(turn, "cancel", target_action="train", target=worker, count=2)
        assert sum(r["remaining"] for r in cancelled.data["production_tasks"]) == 3
        assert env.task_manager.active_demands() == []
        assert env.backend.snapshot().game_time_seconds == 0
        text = LLMAdapter(race=race).render_tool_result(ToolCall("train", {"target": worker, "count": 2}), result)
        assert "Production tasks after staging:" in text and "staged" in text
    finally:
        env.close()


def test_validation_rejection_preserves_current_staged_queue(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        call(turn, "build", target="supply_depot")
        result = call(turn, "train", target="marine", count=-1)
        assert result.status == "rejected"
        assert [(r["target"], r["remaining"]) for r in result.data["production_tasks"]] == [("supply_depot", 1)]
        assert env.task_manager.active_demands() == []
    finally:
        env.close()


def test_previous_paid_work_is_not_reported_as_newly_staged(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        call(turn, "train", target="scv", count=1)
        commit(env, turn)
        turn = env.begin_tool_turn()
        result = call(turn, "build", target="supply_depot")
        old = next(r for r in result.data["production_tasks"] if r["target"] == "scv")
        new = next(r for r in result.data["production_tasks"] if r["target"] == "supply_depot")
        assert old["submission"] == "committed" and old["in_production"] == 1
        assert new["submission"] == "staged"
    finally:
        env.close()


@pytest.mark.parametrize("race,unit", [("terran", "marine"), ("protoss", "zealot"), ("zerg", "zergling")])
def test_dispatch_split_and_reinforcement_preserve_home_counts(tmp_path, race, unit):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.units[unit] = 20
        turn = env.begin_tool_turn()
        first = call(turn, "dispatch_group", source="group_0", order="attack", target="zone_3", units={unit: 10})
        assert first.data["combat_state"]["home_available"] == {unit: 10}
        second = call(turn, "dispatch_group", source="group_1", order="defend", target="zone_2", units={unit: 3})
        state = second.data["combat_state"]
        assert state["home_available"] == {unit: 10}
        rows = {r["group"]: r for r in state["after"]}
        assert rows["group_1"]["allocated_units"] == {unit: 7}
        assert rows["group_2"]["allocated_units"] == {unit: 3}
        assert rows["group_2"]["observed_units"] == {}
        third = call(turn, "reinforce_group", source="group_0", destination="group_1", units={unit: 2})
        assert third.data["combat_state"]["home_available"] == {unit: 8}
        assert third.data["effect"]["available_after_staging"] == {unit: 8}
        assert env.task_manager.active_demands() == []
        assert env.backend.snapshot().game_time_seconds == 0
    finally:
        env.close()


def test_order_change_rejection_merge_and_retreat_remain_pending(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        env.backend.units["marine"] = 20
        turn = env.begin_tool_turn()
        call(turn, "dispatch_group", source="group_0", order="attack", target="zone_3", units={"marine": 6})
        call(turn, "dispatch_group", source="group_0", order="defend", target="zone_2", units={"marine": 4})
        commit(env, turn)
        turn = env.begin_tool_turn()
        changed = call(turn, "attack_group", group="group_1", target="zone_5")
        assert changed.data["combat_state"]["before"][0]["target"] == "zone_3"
        assert changed.data["combat_state"]["after"][0]["target"] == "zone_5"
        rejected = call(turn, "reinforce_group", source="group_0", destination="group_1", units={"marine": 50})
        assert rejected.status == "rejected" and rejected.data["effect"]["missing"] == {"marine": 40}
        assert rejected.data["combat_state"]["home_available"] == {"marine": 10}
        assert rejected.data["combat_state"]["after"][0]["target"] == "zone_5"
        merged = call(turn, "merge_groups", destination="group_1", sources=["group_2"])
        rows = {r["group"]: r for r in merged.data["combat_state"]["after"]}
        assert rows["group_1"]["pending_merge_sources"] == ["group_2"]
        assert rows["group_1"]["allocated_units"] == {"marine": 6}
        assert rows["group_2"]["merge_pending_into"] == "group_1"
        turn.abort()
        turn = env.begin_tool_turn()
        result = call(turn, "retreat_group", group="group_1")
        row = result.data["combat_state"]["after"][0]
        assert row["order"] == "retreat" and row["method"] == "move"
        assert row["target"] == "zone_0"
        assert result.data["combat_state"]["home_available"] == {"marine": 10}
        text = LLMAdapter().render_tool_result(ToolCall("retreat_group", {"group": "group_1"}), result)
        assert "arrival pending" in text
    finally:
        env.close()


def test_malformed_group_arguments_still_return_rejection(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        result = call(turn, "merge_groups", destination=["bad"], sources=[{}])
        assert result.status == "rejected"
        assert result.data["combat_state"]["before"] == result.data["combat_state"]["after"] == []
    finally:
        env.close()


def test_casualties_are_not_counted_as_available_reinforcements(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        env.backend.units["marine"] = 20
        turn = env.begin_tool_turn()
        call(turn, "dispatch_group", source="group_0", order="attack", target="zone_3", units={"marine": 10})
        commit(env, turn)
        group = next(d for d in env.task_manager.active_demands() if d.group == "group_1")
        # Simulate six casualties in the backend's observed membership.
        work = next(w for w in env.backend._queue if w.demand_id == group.demand_id)
        work.alive_units = {"marine": 4}
        env.backend.units["marine"] = 14
        turn = env.begin_tool_turn()
        result = call(turn, "reinforce_group", source="group_0", destination="group_1", units={"marine": 2})
        row = result.data["combat_state"]["after"][0]
        assert row["observed_units"] == {"marine": 4}
        assert row["allocated_units"] == {"marine": 6}
        assert result.data["combat_state"]["home_available"] == {"marine": 8}
    finally:
        env.close()


def test_noop_and_empty_cancel_queue_are_explicit(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        call(turn, "research", target="stimpack")
        duplicate = call(turn, "research", target="stimpack")
        assert duplicate.status == "no_op" and len(duplicate.data["production_tasks"]) == 1
        cancelled = call(turn, "cancel", target_action="research", target="stimpack")
        assert cancelled.data["production_tasks"] == []
        text = LLMAdapter().render_tool_result(ToolCall("cancel", {"target_action": "research", "target": "stimpack"}), cancelled)
        assert "Production tasks after staging:\nnone" in text
    finally:
        env.close()


def test_started_building_remains_visible_after_its_request_completes(tmp_path):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        env.backend.minerals = 500
        turn = env.begin_tool_turn()
        call(turn, "build", target="supply_depot")
        call(turn, "advance", seconds=5)
        env.step(turn.finish())
        turn = env.begin_tool_turn()
        result = call(turn, "train", target="scv", count=1)
        assert not any(r["target"] == "supply_depot" for r in result.data["production_tasks"])
        assert result.data["production_in_progress"]["construction"]["supply_depot"] == 1
    finally:
        env.close()


@pytest.mark.parametrize("race,townhall", [
    ("terran", "command_center"), ("protoss", "nexus"), ("zerg", "hatchery"),
])
def test_staged_response_does_not_promise_construction_after_advance(race, townhall):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.minerals = 0
        turn = env.begin_tool_turn()
        result = call(turn, "build", target=townhall)
        text = LLMAdapter(race=race).render_tool_result(ToolCall("build", {}), result)
        assert result.status == "staged" and "Execution: awaiting advance" in text
        assert "begins after advance" not in text
        observation, _, _, _ = commit(env, turn)
        work = next(row for row in observation.production_priority if row["target"] == townhall)
        assert work["waiting_for"] == "resources"
        assert observation.building[townhall]["under_construction"] == 0
    finally:
        env.close()


def test_native_work_preserves_unknowns_and_filters_inactive_research(monkeypatch):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset()
        snapshot = env.backend.snapshot()
        snapshot.info.update({
            "under_construction": None,
            "in_production_units": None,
            "in_progress_research": {"stimpack": 1, "combat_shield": 0},
        })
        monkeypatch.setattr(env.backend, "snapshot", lambda: snapshot)
        turn = env.begin_tool_turn()
        result = call(turn, "build", target="supply_depot")
        assert result.data["production_in_progress"] == {
            "construction": None, "training": None, "research": ["stimpack"],
        }
        text = LLMAdapter().render_tool_result(ToolCall("build", {}), result)
        assert "- Construction: unknown" in text
        assert "- Training: unknown" in text
        assert "- Research: stimpack" in text
        assert "combat_shield" not in text
    finally:
        env.close()

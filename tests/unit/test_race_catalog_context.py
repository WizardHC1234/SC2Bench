"""Explicit episode race reaches parser, schema and execution metadata."""

from tests.helpers.drive import drive
import pytest

from sc2bench_env import Environment
from sc2bench_env.backends import fake
from sc2bench_env.catalog import registry as action_catalog, aliases as target_aliases
from sc2bench_env.interface import actions, decision_rules
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime import task_manager


DECISION = [
    {"name": "build", "arguments": {'target': "supply_depot"}},
    {"name": "train", "arguments": {'target': "marine", 'count': 2}},
    {"name": "research", "arguments": {'target': "stim_pack"}},
    {"name": "morph_townhall", "arguments": {'target': "townhall_0", 'to': "orbital_command"}},
    {"name": "combat", "arguments": {'style': "attack", 'target': "zone_1", 'units': {"marine": 1}}},
    {"name": "advance", "arguments": {'seconds': 1}},
]


def test_parser_passes_race_to_all_catalog_checks_and_preserves_aliases(monkeypatch):
    calls = []
    original = action_catalog.known_target_names

    def lookup(action=None, *, race):
        calls.append((action, race))
        return original(action, race=race)

    monkeypatch.setattr(actions, "known_target_names", lookup)
    monkeypatch.setattr(decision_rules, "known_target_names", lookup)
    batch = actions.parse_decision(DECISION, race="terran")
    assert {verb for verb, _ in calls} == {"build", "train", "research", "morph_townhall"}
    assert all(race == "terran" for _, race in calls)
    assert batch.actions[2].target == "stimpack"
    assert batch.raw[2]["arguments"]["target"] == "stim_pack"
    assert batch.normalizations[0]["canonical"] == "stimpack"
    assert batch == actions.parse_decision(DECISION)


def test_schema_wrapper_passes_race_to_catalog_queries(monkeypatch):
    original = action_catalog.known_target_names
    calls = []

    def lookup(action=None, *, race):
        calls.append((action, race))
        return original(action, race=race)

    expected = decision_rules.decision_json_schema()
    monkeypatch.setattr(decision_rules, "known_target_names", lookup)
    assert decision_rules.decision_json_schema(race="terran") == expected
    assert calls == [(verb, "terran") for verb in (
        "build", "train", "research", "morph_townhall", "scan", "call_mule", "supply_drop",
        "chrono_boost", "inject_larva", "spawn_creep_tumor", "morph_townhall",
    )]
    assert "x-sc2bench-wait-conditions" not in expected


@pytest.mark.parametrize("operation", [
    lambda: actions.parse_decision([{"name": "advance", "arguments": {'seconds': 5}}], race="random"),
    lambda: actions.parse_game_action({"name": "call_mule", "arguments": {}}, race="random"),
    lambda: actions.parse_advance_action({"name": "advance", "arguments": {"seconds": 5}}, race="random"),
    lambda: actions.parse_decision([], race="random"),
    lambda: decision_rules.decision_json_schema(race="random"),
    lambda: target_aliases.normalize_target_aliases({"name": "research", "arguments": {'target': "stim_pack"}}, race="random"),
])
def test_unimplemented_context_cannot_use_default_terran_rules(operation):
    with pytest.raises(ValueError, match="Unsupported own race"):
        operation()


def test_schema_entry_check_reports_unsupported_context():
    assert "Unsupported own race" in decision_rules.schema_accepts_entry({"name": "advance", "arguments": {'seconds': 5}}, race="random")


@pytest.mark.parametrize("race,worker,foreign_worker", [
    ("terran", "scv", "probe"),
    ("protoss", "probe", "drone"),
    ("zerg", "drone", "scv"),
])
def test_environment_and_runtime_use_the_episode_race(race, worker, foreign_worker):
    from sc2bench_env.interface.tools import ToolCall

    env = Environment(record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("train", {"target": foreign_worker, "count": 1})).status == "rejected"
        assert turn.call(ToolCall("train", {"target": worker, "count": 1})).status == "staged"
        turn.call(ToolCall("advance", {"seconds": 5}))
        batch = turn.finish()
        _, feedback, _, _ = env.step(batch)
        assert feedback.receipts[0].result == "accepted"
        assert env.task_manager.race == race
        manager = task_manager.TaskManager(race=race)
        receipts = manager.submit_decision(batch, game_time=0)
        assert receipts[0].result == "accepted"
        assert manager.active_demands()[0].target == worker
    finally:
        env.close()


def test_bad_runtime_race_reset_preserves_accepted_work():
    manager = task_manager.TaskManager()
    manager.submit_decision(actions.parse_decision([{"name": "train", "arguments": {'target': "scv", 'count': 1}}, {"name": "advance", "arguments": {'seconds': 5}}], race="terran"), game_time=0)
    before = list(manager.active_demands())
    with pytest.raises(ValueError, match="Unsupported own race"):
        manager.reset(race="random")
    assert manager.race == "terran" and manager.active_demands() == before


def test_fake_loads_per_episode_catalog_with_explicit_race(monkeypatch):
    original_costs, original_prerequisites = fake.cost_table, fake.prerequisite_table
    calls = []

    def costs(*, race):
        calls.append(("costs", race))
        return original_costs(race=race)

    def prerequisites(*, race):
        calls.append(("prerequisites", race))
        return original_prerequisites(race=race)

    monkeypatch.setattr(fake, "cost_table", costs)
    monkeypatch.setattr(fake, "prerequisite_table", prerequisites)
    backend = fake.FakeBackend()
    calls.clear()
    backend.start_episode(EpisodeConfig())
    assert calls == [("costs", "terran"), ("prerequisites", "terran")]
    assert backend._costs == fake.COSTS and backend._costs is not fake.COSTS
    assert backend._prerequisites == fake.PREREQUISITES


def test_fake_metadata_is_independent_across_instances_and_resets():
    first, second = fake.FakeBackend(), fake.FakeBackend()
    first._costs["marine"]["minerals"] = -1
    first._prerequisites["marine"].clear()
    assert second._costs["marine"]["minerals"] == fake.COSTS["marine"]["minerals"] == 50
    assert second._prerequisites["marine"] == ["barracks"]
    first.start_episode(EpisodeConfig())
    assert first._costs["marine"]["minerals"] == 50
    assert first._prerequisites["marine"] == ["barracks"]


def test_sharpy_macro_queries_use_the_selected_race(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy import macro

    original = action_catalog.get_target
    calls = []

    def lookup(name, *, race):
        calls.append(race)
        return original(name, race=race)

    monkeypatch.setattr(macro, "get_target", lookup)
    assert macro._estimate_cost({"action": "train", "target": "marine"}, race="terran")["minerals"] == 50
    act = macro.ActOngoingMacroTasks([], race="terran")
    monkeypatch.setattr(act, "_count_ready", lambda name: 0)
    assert act._prereq_blocked({"action": "train", "target": "marine"})
    assert calls == ["terran", "terran"]
    with pytest.raises(ValueError, match="Unsupported own race"):
        macro.ActOngoingMacroTasks([], race="random")

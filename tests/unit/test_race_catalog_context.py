"""Explicit episode race reaches parser, schema and execution metadata."""

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends import fake
from sc2bench_env.interface import action_catalog, actions, decision_rules, target_aliases
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime import task_manager


DECISION = [
    {"action": "build", "target": "supply_depot"},
    {"action": "train", "target": "marine", "count": 2},
    {"action": "research", "target": "stim_pack"},
    {"action": "upgrade", "target": "cc_0", "to": "orbital_command"},
    {"action": "combat", "style": "attack", "target": "zone_1", "units": {"marine": 1}},
    {"action": "wait", "any_of": [{"condition": "interval", "seconds": 1}]},
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
    assert {verb for verb, _ in calls} == {"build", "train", "research", "upgrade"}
    assert all(race == "terran" for _, race in calls)
    assert batch.actions[2].target == "stimpack"
    assert batch.raw[2]["target"] == "stim_pack"
    assert batch.normalizations[0]["canonical"] == "stimpack"
    assert batch == actions.parse_decision(DECISION)


def test_schema_wrapper_passes_race_to_catalog_queries(monkeypatch):
    original = action_catalog.known_target_names
    calls = []

    def lookup(action=None, *, race):
        calls.append((action, race))
        return original(action, race=race)

    expected = action_catalog.decision_json_schema()
    monkeypatch.setattr(decision_rules, "known_target_names", lookup)
    assert action_catalog.decision_json_schema(race="terran") == expected
    assert calls == [(verb, "terran") for verb in ("build", "train", "research", "upgrade")]
    assert expected["x-sc2bench-wait-conditions"] == list(action_catalog.WAIT_CONDITIONS)


@pytest.mark.parametrize("operation", [
    lambda: actions.parse_decision([{"action": "wait"}], race="zerg"),
    lambda: actions.parse_game_action({"action": "call_mule"}, race="zerg"),
    lambda: actions.parse_wait_action({"action": "wait"}, race="zerg"),
    lambda: actions.parse_actions([], race="zerg"),
    lambda: action_catalog.decision_json_schema(race="zerg"),
    lambda: target_aliases.normalize_target_aliases({"action": "research", "target": "stim_pack"}, race="zerg"),
])
def test_unimplemented_context_cannot_use_default_terran_rules(operation):
    with pytest.raises(ValueError, match="Unsupported own race"):
        operation()


def test_schema_entry_check_reports_unsupported_context():
    assert "Unsupported own race" in decision_rules.schema_accepts_entry({"action": "wait"}, race="zerg")


def test_environment_and_runtime_parse_using_the_episode_race(monkeypatch):
    import sc2bench_env.env as env_module

    original = actions.parse_decision
    calls = []

    def parse(raw, *, race):
        calls.append(race)
        return original(raw, race=race)

    monkeypatch.setattr(env_module, "parse_decision", parse)
    monkeypatch.setattr(task_manager, "parse_decision", parse)
    env = Environment(record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="terran"))
        _, feedback, _, _ = env.step([{"action": "train", "target": "scv", "count": 1}, {"action": "wait"}])
        assert feedback.receipts[0].result == "accepted"
        assert env.task_manager.race == "terran"
        manager = task_manager.TaskManager(race="terran")
        receipts = manager.submit_decision([{"action": "train", "target": "scv", "count": 1}, {"action": "wait"}], game_time=0)
        assert receipts[0].result == "accepted"
        assert calls == ["terran", "terran"]
    finally:
        env.close()


def test_bad_runtime_race_reset_preserves_accepted_work():
    manager = task_manager.TaskManager()
    manager.submit_decision([{"action": "train", "target": "scv", "count": 1}, {"action": "wait"}], game_time=0)
    before = list(manager.active_demands())
    with pytest.raises(ValueError, match="Unsupported own race"):
        manager.reset(race="protoss")
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
        macro.ActOngoingMacroTasks([], race="protoss")

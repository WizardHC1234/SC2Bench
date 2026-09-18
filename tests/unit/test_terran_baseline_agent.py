"""External sample Agent plans from public JSON, never backend internals."""

from tests.helpers.terran_baseline import TerranBaselineAgent
from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import parse_decision


def test_agent_uses_serialized_observation_and_avoids_duplicate_build_orders():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        observation = env.reset().to_dict()
        agent = TerranBaselineAgent()
        first = agent.decide(observation)
        assert {row["target"] for row in first if row["action"] == "build"} == {"supply_depot"}
        assert parse_decision(first).to_dicts() == first

        observation["building"]["supply_depot"] = {
            "completed": 0, "under_construction": 0,
            "worker_en_route": 1, "waiting_to_start": 0,
        }
        second = agent.decide(observation)
        assert not any(row["action"] == "build" for row in second)
        assert parse_decision(second).to_dicts() == second
    finally:
        env.close()


def test_agent_scouts_once_and_attacks_only_with_free_marines():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        observation = env.reset().to_dict()
        observation["building"]["barracks"] = {
            "completed": 1, "under_construction": 0,
            "worker_en_route": 0, "waiting_to_start": 0,
        }
        observation["own_forces"]["army"]["marine"] = 12
        observation["own_forces"]["assigned"]["marine"] = 5
        observation["own_forces"]["free"]["marine"] = 7
        agent = TerranBaselineAgent()
        first = agent.decide(observation)
        assert any(row["action"] == "scout" for row in first)
        assert not any(row["action"] == "combat" for row in first)

        observation["own_forces"]["free"]["marine"] = 12
        second = agent.decide(observation)
        assert not any(row["action"] == "scout" for row in second)
        assert any(row["action"] == "combat" and row["units"] == {"marine": 12}
                   for row in second)
        assert parse_decision(second).to_dicts() == second
    finally:
        env.close()

"""Internal Terran rule fixture for environment stability checks.

This is intentionally an example harness, not a platform policy or a
competitive StarCraft strategy. Decisions use only serialized Observation.
"""
from __future__ import annotations


from typing import Any, Mapping


def _building_count(building: Mapping[str, Any], target: str, *, ready_only: bool = False) -> int:
    row = building.get(target) or {}
    fields = ("completed",) if ready_only else (
        "completed", "under_construction", "worker_en_route", "waiting_to_start",
    )
    return sum(int(row.get(field, 0)) for field in fields)


class TerranBaselineAgent:
    """Stateless economic decisions plus one remembered scouting route."""

    def __init__(self) -> None:
        self.scouted = False

    def decide(self, observation: Mapping[str, Any]) -> list[dict[str, Any]]:
        economy = observation["economy"]
        building = observation["building"]
        training = observation["training"]
        own_forces = observation["own_forces"]
        zones = observation["zone_state"]
        actions: list[dict[str, Any]] = []

        if (int(economy["supply_left"]) <= 4 and int(economy["supply_cap"]) < 120
                and _building_count(building, "supply_depot")
                == _building_count(building, "supply_depot", ready_only=True)):
            actions.append({"name": "build", "arguments": {'target': "supply_depot"}})

        if (_building_count(building, "supply_depot", ready_only=True) >= 1
                and _building_count(building, "barracks") < 2):
            actions.append({"name": "build", "arguments": {'target': "barracks"}})

        if (int(economy["worker_count"]) < 20 and int(economy["supply_left"]) > 0
                and "scv" not in training):
            actions.append({"name": "train", "arguments": {'target': "scv", 'count': 2}})

        if (_building_count(building, "barracks", ready_only=True) >= 1
                and "marine" not in training):
            actions.append({"name": "train", "arguments": {'target': "marine", 'count': 6}})

        if not self.scouted and _building_count(building, "barracks", ready_only=True):
            route = [row["zone_id"] for role in ("enemy_natural", "enemy_main")
                     for row in zones if row.get("zone_role") == role]
            if route:
                actions.append({"name": "scout", "arguments": {'route': route}})
                self.scouted = True

        enemy_main = next((row["zone_id"] for row in zones
                           if row.get("zone_role") == "enemy_main"), None)
        free_marines = int(own_forces["free"].get("marine", 0))
        if enemy_main and free_marines >= 10 and not any(
            name != "group_0" for name in observation["combat"]
        ):
            actions.append({"name": "combat", "arguments": {'style': "attack", 'target': enemy_main, 'units': {"marine": min(16, free_marines)}}})

        actions.append({"name": "advance", "arguments": {"seconds": 15}})
        return actions


# Test-only pilot selection and fresh rule-agent factory.
from pathlib import Path
from sc2bench_env.benchmark import BenchmarkSuite

DEFAULT_SUITE = Path(__file__).resolve().parents[2] / "benchmarks" / "terran_pilot.json"

def pilot_configs(repetitions=1, *, opponents=("hard",)):
    suite = BenchmarkSuite.load(DEFAULT_SUITE).with_overrides(
        repetitions=repetitions, opponents=opponents)
    return [item["config"] for item in suite.episode_plan()]

def create_rule_agent():
    from sc2bench_env.interface.agent import AgentTurn
    from sc2bench_env.interface.tools import ToolCall

    agent = TerranBaselineAgent()

    def act(request):
        for item in agent.decide(request.observation.to_dict()):
            request.call_tool(ToolCall(item["name"], dict(item.get("arguments") or {})))
        return AgentTurn()

    return act

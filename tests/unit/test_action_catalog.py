"""Action Catalog single-source checks."""
from __future__ import annotations
from sc2bench_env.adapters.llm import LLMAdapter


from sc2bench_env import Environment
from sc2bench_env.backends.fake import COSTS, PREREQUISITES, FakeBackend
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.catalog.registry import (
    TERRAN_TARGETS,
    cost_table,
    get_target,
    prerequisite_table,
    render_system_prompt,
    render_action_catalog,
    validate_catalog,
)
from sc2bench_env.interface.config import EpisodeConfig


def test_catalog_is_internally_consistent() -> None:
    validate_catalog(TERRAN_TARGETS)
    assert cost_table() == COSTS
    assert prerequisite_table() == PREREQUISITES


def test_system_prompt_comes_from_catalog() -> None:
    env = Environment(FakeBackend())
    prompt = LLMAdapter(race=env.config.race if env.config else "terran").system_prompt()
    assert "Target tables (terran):" not in prompt
    assert "Action Catalog" not in prompt
    assert "supply_depot" not in prompt
    assert "stimpack" not in prompt
    assert "orbital_command" not in prompt
    env.reset(EpisodeConfig(race="terran"))
    assert "terran" in LLMAdapter(race=env.config.race if env.config else "terran").system_prompt().lower()
    env.close()


def test_decision_schema_lists_catalog_targets() -> None:
    schema = decision_json_schema()
    targets = schema["x-sc2bench-targets"]
    assert "barracks" in targets["build"]
    assert "marine" in targets["train"]
    assert "marauder" in targets["train"]
    assert "siege_tank" in targets["train"]
    assert "medivac" in targets["train"]
    assert "banshee" in targets["train"]
    assert "stimpack" in targets["research"]
    assert "orbital_command" in targets["morph_townhall.to"]


def test_reference_corrections_are_shared_with_fake_costs() -> None:
    assert get_target("starport").base_time_seconds == 36
    assert COSTS["starport"]["build_time"] == 36
    assert get_target("armory").vespene == 50
    assert COSTS["armory"]["vespene"] == 50


def test_ability_energy_and_prerequisites_are_catalogued() -> None:
    for name in ("scan", "call_mule", "supply_drop"):
        spec = get_target(name)
        assert spec.energy == COSTS[name]["energy"] == 50
        assert spec.prerequisites == ("orbital_command",)
        assert spec.to_dict()["energy"] == 50
    assert get_target("scout").prerequisites == ("scv",)


def test_prompt_hides_fake_ability_durations() -> None:
    catalog = render_action_catalog()
    target_lines = catalog.splitlines()
    rows = {
        name: next(line for line in target_lines if line.startswith(f"{name} |"))
        for name in ("scan", "call_mule", "supply_drop", "scout", "starport", "armory")
    }
    for name in ("scan", "call_mule", "supply_drop", "scout"):
        assert "base_seconds" not in rows[name]
    assert rows["scan"].startswith("scan | 50 |")
    assert rows["call_mule"].startswith("call_mule | 50 |")
    assert rows["supply_drop"].startswith("supply_drop | 50 |")
    assert "starport | 150/100 | 36" in rows["starport"]
    assert "armory | 150/50 | 46" in rows["armory"]
    assert rows["scout"].startswith("scout |")
    # Fake timing is unchanged; hiding it must not change route simulation.
    assert COSTS["scout"]["build_time"] == 8
    assert COSTS["scan"]["build_time"] == 1


def test_prompt_explains_completion_and_game_restrictions() -> None:
    prompt = render_system_prompt()
    assert "Tech Lab unlocks advanced units or research" in prompt
    assert "total supply is capped at 200" in prompt
    from sc2bench_env.interface.platform_rules import ACTION_RULES
    assert "construction or the structure morph starts" in ACTION_RULES["build"]
    assert "count is the minimum number of additional target units" in ACTION_RULES["train"]
    assert "after Research reports it completed" in ACTION_RULES["research"]
    assert "not when the new form finishes" in ACTION_RULES["morph_townhall"]
    assert "Split the requested units" in ACTION_RULES["dispatch_group"]
    assert get_target("attack").success_boundary == "mission_ended"
    assert get_target("scout").success_boundary == "route_completed"


def test_sharpy_budget_uses_catalog_energy() -> None:
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.macro import _estimate_cost

    for action in ("scan", "call_mule", "supply_drop"):
        assert _estimate_cost({"action": action})["energy"] == get_target(action).energy
    assert _estimate_cost({"action": "build", "target": "armory"})["vespene"] == 50

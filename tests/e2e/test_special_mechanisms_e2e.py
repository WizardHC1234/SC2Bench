"""Windows client check: Supply Drop spends the shared Orbital bank and raises supply."""
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.catalog.knowledge import food_provided
from sc2bench_env.interface.config import EpisodeConfig
from tests.e2e.test_macro_losses_e2e import advance_until, wait
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_supply_drop_shares_orbital_energy_and_adds_supply(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(opponent="veryeasy", game_time_limit_seconds=600))
        cc = next(row["id"] for row in obs.structures if row["type"] == "command_center")
        for target in ("supply_depot", "barracks"):
            drive(env, [{"name": "build", "arguments": {"target": target}}, wait()])
            advance_until(env, lambda obs: obs.building.get(target, {}).get("completed", 0) == 1)
        drive(env, [{"name": "morph_townhall", "arguments": {"target": cc, "to": "orbital_command"}}, wait()])
        obs = advance_until(env, lambda obs: obs.orbital_count == 1 and int(obs.scan_ready or 0) >= 1)
        before_cap = int(obs.economy.supply_cap)
        energy = env.backend.snapshot().info["orbital_energies"]
        assert len(energy) == 1 and energy[0] >= 50
        zone = next(row["zone_id"] for row in obs.zone_state if row["zone_role"] == "enemy_main")
        obs, feedback, done, _ = drive(env, [
            {"name": "supply_drop", "arguments": {}},
            {"name": "scan", "arguments": {"target": zone}},
            wait(3),
        ])
        assert not done
        assert [receipt.result for receipt in feedback.receipts] == ["accepted", "accepted"]
        demands = {d.action: d for d in env.task_manager.demands.values() if d.action in {"supply_drop", "scan"}}
        assert demands["supply_drop"].produced == 1 and not demands["supply_drop"].is_active
        assert demands["scan"].produced == 0 and demands["scan"].waiting_for == "energy"
        grant = food_provided("terran", "supply_depot")
        obs = advance_until(
            env,
            lambda obs: int(obs.economy.supply_cap) >= before_cap + grant,
            max_seconds=15,
        )
        assert int(obs.economy.supply_cap) >= before_cap + grant
        assert env.backend.snapshot().info["orbital_energies"][0] < 50
        assert int(obs.scan_ready or 0) == 0
    finally:
        env.close()

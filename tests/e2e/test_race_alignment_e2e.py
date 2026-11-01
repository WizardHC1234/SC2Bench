"""Live 5.0.16 checks for supply, warp-gate research, and queen reservation.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_race_alignment_e2e.py -q
"""
from __future__ import annotations

import os

import pytest

from tests.e2e.test_combat_e2e import _build_ready, _wait, _wait_until
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _opening(race: str, tmp_path):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy", record_dir=tmp_path)
    obs = env.reset(EpisodeConfig(
        race=race,
        opponent="veryeasy",
        game_time_limit_seconds=900,
    ))
    return env, obs


def test_e2e_opening_supply_matches_the_running_client(tmp_path):
    from sc2bench_env.catalog.knowledge import food_provided

    env, obs = _opening("terran", tmp_path)
    try:
        header = (env.record_path / "episode.txt").read_text(encoding="utf-8")
        assert "5.0.16.97563" in header
        assert obs.economy.supply_cap == food_provided("terran", "command_center") == 13
    finally:
        env.close()


def test_e2e_warp_gate_research_does_not_morph_gateways(tmp_path, monkeypatch):
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.ids.upgrade_id import UpgradeId

    from sc2bench_env.backends.sharpy.gather import PlanHomeGather
    from sc2bench_env.catalog.knowledge import food_provided

    counts = {}
    original = PlanHomeGather.execute

    async def observe(act):
        counts["gateway"] = int(act.ai.structures(UnitTypeId.GATEWAY).ready.amount)
        counts["warpgate"] = int(act.ai.structures(UnitTypeId.WARPGATE).ready.amount)
        counts["research"] = float(act.ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH))
        return await original(act)

    monkeypatch.setattr(PlanHomeGather, "execute", observe)
    env, obs = _opening("protoss", tmp_path)
    try:
        assert obs.economy.supply_cap == food_provided("protoss", "nexus") == 13
        for target in ("pylon", "gateway", "assimilator", "cybernetics_core"):
            _build_ready(env, target)
        drive(env, [{"name": "research", "arguments": {"target": "warp_gate"}}, _wait(8)])
        obs, ended = _wait_until(
            env,
            lambda o: "warp_gate" in (o.upgrades or []),
            max_steps=40,
        )
        assert not ended, "episode ended before warp_gate research finished"
        drive(env, [_wait(12)])
        assert counts.get("research", 0) >= 1
        assert counts.get("gateway", 0) >= 1
        assert counts.get("warpgate", 0) == 0
    finally:
        env.close()


def test_e2e_one_build_warpgate_converts_one_gateway(tmp_path, monkeypatch):
    """5.0.16 research does not morph. One build warpgate converts one Gateway.

    The mineral and gas samples decide whether this client charges the first morph.
    Linux 4.10 is not started here; its cost stays the extracted incremental of 0.
    """
    from sc2.ids.ability_id import AbilityId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.ids.upgrade_id import UpgradeId

    from sc2bench_env.backends.sharpy.gather import PlanHomeGather
    from sc2bench_env.backends.sharpy.races.protoss_warp import (
        ActWarpGateConversion, home_warp_point,
    )

    series = []
    homes = []
    costs = []
    issued = []
    original_gather = PlanHomeGather.execute
    original_morph = ActWarpGateConversion.execute

    async def observe(act):
        pylons = list(act.ai.structures(UnitTypeId.PYLON).ready)
        if pylons and not homes:
            point = home_warp_point(act.ai)
            if point is not None:
                homes.append((point.x, point.y))
        series.append((
            float(act.ai.time),
            int(act.ai.minerals),
            int(act.ai.vespene),
            int(act.ai.structures(UnitTypeId.GATEWAY).amount),
            int(act.ai.structures(UnitTypeId.WARPGATE).amount),
            float(act.ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH)),
        ))
        return await original_gather(act)

    async def watch_morph(act):
        bank = (
            float(act.ai.time),
            int(act.ai.minerals),
            int(act.ai.vespene),
            int(act.ai.structures(UnitTypeId.WARPGATE).amount),
        )
        cost = act.ai._game_data.calculate_ability_cost(AbilityId.MORPH_WARPGATE)
        costs.append((int(cost.minerals), int(cost.vespene)))
        result = await original_morph(act)
        if act.waiting_reason is None and bank[3] < act.target_count:
            issued.append(bank)
        return result

    monkeypatch.setattr(PlanHomeGather, "execute", observe)
    monkeypatch.setattr(ActWarpGateConversion, "execute", watch_morph)
    env, _obs = _opening("protoss", tmp_path)
    try:
        for target in ("pylon", "gateway", "assimilator", "cybernetics_core"):
            _build_ready(env, target)
        drive(env, [{"name": "research", "arguments": {"target": "warp_gate"}}, _wait(8)])
        obs, ended = _wait_until(env, lambda item: "warp_gate" in (item.upgrades or []), max_steps=40)
        assert not ended, "episode ended before warp_gate research finished"
        before = next(row for row in reversed(series) if row[5] >= 1)
        assert before[4] == 0, series[-12:]
        drive(env, [{"name": "build", "arguments": {"target": "warpgate"}}, _wait(8)])
        obs, ended = _wait_until(
            env,
            lambda item: int((item.building.get("gateway") or {}).get("completed") or 0) >= 0 and any(
                row[4] >= 1 for row in series
            ),
            max_steps=20,
        )
        assert not ended
        converted = [row for row in series if row[4] >= 1]
        assert converted, "build warpgate did not produce a Warp Gate"
        assert max(row[4] for row in series) == 1
        first = converted[0]
        earlier = [row for row in series if row[0] <= first[0] and row[4] == 0]
        assert earlier
        previous = earlier[-1]
        assert previous[3] - first[3] == 1
        assert homes, "no powered home warp point"
        assert issued, "build warpgate never issued MORPH_WARPGATE"
        issue = issued[0]
        nxt = next(row for row in series if row[0] > issue[0] + 1e-3)
        mineral_drop = issue[1] - nxt[1]
        gas_drop = issue[2] - nxt[2]
        ability_cost = costs[-1] if costs else None
        # Measured on 5.0.16.97563: issuing MORPH_WARPGATE dropped the bank by 25
        # minerals and about 25 gas. The ability table's 150 is the Gateway price.
        assert 20 <= mineral_drop <= 30 and 15 <= gas_drop <= 30, (
            f"mineral_drop={mineral_drop} gas_drop={gas_drop} "
            f"ability_cost={ability_cost} issue={issue} next={nxt}"
        )
    finally:
        env.close()


def test_e2e_outbound_queen_is_not_used_for_inject(tmp_path, monkeypatch):
    from sc2.ids.unit_typeid import UnitTypeId

    from sc2bench_env.backends.sharpy.acts import ActInject

    stolen = []
    original = ActInject.execute

    async def watch(act):
        combat = set(getattr(act.ai, "bench_combat_tags", set()) or ())
        queens = [queen.tag for queen in act.ai.units(UnitTypeId.QUEEN)]
        result = await original(act)
        if act._done and any(tag in combat for tag in queens):
            stolen.append(queens)
        return result

    monkeypatch.setattr(ActInject, "execute", watch)
    env, _obs = _opening("zerg", tmp_path)
    try:
        _build_ready(env, "spawning_pool")
        drive(env, [{"name": "train", "arguments": {"target": "queen", "count": 1}}, _wait(8)])
        obs, ended = _wait_until(env, lambda o: o.units.get("queen", 0) >= 1, max_steps=20)
        assert not ended and obs.units.get("queen", 0) >= 1
        natural = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "own_natural")
        feedback = drive(env, [
            {"name": "combat", "arguments": {"style": "defend", "target": natural, "units": {"queen": 1}}},
            _wait(6),
        ])[1]
        assert feedback.receipts[0].result == "accepted", feedback.receipts[0]
        obs, ended = _wait_until(
            env,
            lambda o: any(row.get("style") == "defend" and row.get("alive", {}).get("queen", 0) >= 1
                          for row in (o.combat or {}).values()),
            max_steps=15,
        )
        assert not ended
        drive(env, [{"name": "inject_larva", "arguments": {}}, _wait(10)])
        assert stolen == []
    finally:
        env.close()

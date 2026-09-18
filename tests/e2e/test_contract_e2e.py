"""Real SC2 checks for soft-reserve, multi-order, and partial cancel.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e -q
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 8.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def _wait_until(env, pred, *, max_steps: int = 40):
    obs = None
    terminated = False
    for _ in range(max_steps):
        obs, _, terminated, _ = env.step([_wait()])
        assert_obs_consistent(obs)
        if pred(obs) or terminated:
            return obs, terminated
    return obs, terminated


def _boot_depot_barracks(env) -> None:
    env.step([{"action": "build", "target": "supply_depot"}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=20
    )
    assert not terminated
    env.step([{"action": "build", "target": "barracks"}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=25
    )
    assert not terminated
    assert obs.buildings.get("barracks", 0) >= 1


def test_e2e_soft_reserve_blocks_later_barracks_for_cc() -> None:
    """Earlier CC waiting on minerals must soft-reserve so barracks cannot sneak in first."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=3.0,
                game_time_limit_seconds=240.0,
                opponent="builtin_easy",
            )
        )
        env.step([{"action": "build", "target": "supply_depot"}, _wait(5)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=18
        )
        assert not terminated

        hit_window = False
        for _ in range(40):
            minerals = int(obs.resources.minerals)
            # Leave headroom so income during the decision cannot immediately fund CC.
            if 200 <= minerals < 350:
                hit_window = True
                break
            if minerals >= 350:
                break
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if terminated:
                break
        assert hit_window, f"missed mineral window; minerals={obs.resources.minerals}"

        start_minerals = int(obs.resources.minerals)
        obs, feedback, _, _ = env.step(
            [
                {"action": "build", "target": "command_center"},
                {"action": "build", "target": "barracks"},
                _wait(2),
            ]
        )
        assert [r.result for r in feedback.receipts[:2]] == ["accepted", "accepted"]
        assert start_minerals < 400

        def _build_started(row: dict) -> bool:
            return (
                int(row.get("under_construction", 0))
                + int(row.get("worker_en_route", 0))
            ) > 0

        # Soft-reserve window: until the earlier CC demand actually begins, barracks
        # must stay idle while minerals are still below the CC cost.
        saw_cc_start = False
        for _ in range(16):
            minerals = int(obs.resources.minerals)
            cc_row = obs.building.get("command_center", {})
            br_row = obs.building.get("barracks", {})
            # Ignore completed: starting townhall already counts as completed=1.
            cc_on = _build_started(cc_row)
            br_on = _build_started(br_row) or int(obs.buildings.get("barracks", 0)) > 0
            if cc_on:
                saw_cc_start = True
                # Once CC has begun, leftover/income may legally fund barracks.
                break
            if minerals >= 400:
                # Can afford CC now; barracks still must not have jumped the queue.
                assert not br_on
                break
            assert not br_on, (
                f"barracks started while CC still waiting; minerals={minerals} "
                f"cc={cc_row} barracks={br_row}"
            )
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if terminated:
                break

        # Barracks must never be the first of the two to start.
        if _build_started(obs.building.get("barracks", {})) or int(obs.buildings.get("barracks", 0)) > 0:
            assert saw_cc_start or _build_started(obs.building.get("command_center", {}))
    finally:
        env.close()


def test_e2e_two_marine_orders_not_merged() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=5.0,
                game_time_limit_seconds=300.0,
                opponent="builtin_easy",
            )
        )
        _boot_depot_barracks(env)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(5)])
        obs, _ = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 2, max_steps=18
        )

        obs, feedback, _, _ = env.step(
            [
                {"action": "train", "target": "marine", "count": 2},
                {"action": "train", "target": "marine", "count": 2},
                _wait(4),
            ]
        )
        assert [r.result for r in feedback.receipts] == ["accepted", "accepted"]
        marine_demands = [
            d
            for d in env.task_manager.active_demands()
            if d.action == "train" and d.target == "marine"
        ]
        assert len(marine_demands) == 2
        assert [d.count for d in marine_demands] == [2, 2]
        assert marine_demands[0].order_index < marine_demands[1].order_index

        def _produced_total(o) -> int:
            return sum(
                int(d.produced)
                for d in env.task_manager.demands.values()
                if d.action == "train" and d.target == "marine"
            )

        obs, terminated = _wait_until(
            env,
            lambda o: _produced_total(o) >= 4 or o.units.get("marine", 0) >= 4,
            max_steps=35,
        )
        produced = _produced_total(obs)
        living = int(obs.units.get("marine", 0))
        assert produced >= 4 or living >= 4, (
            f"expected 4 marines from two orders; produced={produced} living={living} "
            f"terminated={terminated}"
        )
    finally:
        env.close()


def test_e2e_cancel_after_partial_marine_train() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=5.0,
                game_time_limit_seconds=420.0,
                opponent="builtin_easy",
            )
        )
        _boot_depot_barracks(env)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        obs, _ = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 2, max_steps=18
        )

        env.step([{"action": "train", "target": "marine", "count": 6}, _wait(5)])
        obs = None
        demand = None
        for _ in range(45):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            demand = next(
                (
                    d
                    for d in env.task_manager.active_demands()
                    if d.action == "train" and d.target == "marine"
                ),
                None,
            )
            if demand is not None and demand.produced >= 1 and demand.remaining >= 2:
                break
            if terminated:
                break
        assert demand is not None
        assert demand.produced >= 1
        assert demand.remaining >= 2
        produced_before = int(demand.produced)

        obs, feedback, _, _ = env.step(
            [
                {"action": "cancel", "target_action": "train", "target": "marine"},
                _wait(4),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        assert "cleared_waiting=" in (feedback.receipts[0].reason or "")
        cleared = int((feedback.receipts[0].reason or "").split("cleared_waiting=")[1])
        assert cleared >= 1

        for _ in range(40):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            active = [
                d
                for d in env.task_manager.active_demands()
                if d.action == "train" and d.target == "marine"
            ]
            if not active:
                break
            if terminated:
                break

        final = int(obs.units.get("marine", 0))
        assert final < 6
        assert final >= produced_before
        assert not any(
            d.action == "train" and d.target == "marine" and d.is_active
            for d in env.task_manager.demands.values()
        )
    finally:
        env.close()


def test_e2e_research_keeps_order_among_trains() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=6.0,
                game_time_limit_seconds=540.0,
                opponent="builtin_easy",
            )
        )
        _boot_depot_barracks(env)
        env.step([{"action": "build", "target": "refinery"}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("refinery", 0) >= 1, max_steps=20
        )
        assert not terminated
        env.step([{"action": "build", "target": "barracks_techlab"}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks_techlab", 0) >= 1, max_steps=25
        )
        assert obs.buildings.get("barracks_techlab", 0) >= 1
        obs, _ = _wait_until(
            env,
            lambda o: o.resources.vespene >= 100 and o.resources.minerals >= 150,
            max_steps=30,
        )

        before_indexes = {
            d.demand_id: d.order_index for d in env.task_manager.demands.values()
        }
        obs, feedback, _, _ = env.step(
            [
                {"action": "train", "target": "marine", "count": 1},
                {"action": "research", "target": "stimpack"},
                {"action": "train", "target": "marine", "count": 1},
                _wait(4),
            ]
        )
        assert [r.result for r in feedback.receipts] == [
            "accepted",
            "accepted",
            "accepted",
        ]
        # Include just-completed research: order_index must stay train < research < train.
        fresh = [
            d
            for d in env.task_manager.demands.values()
            if d.demand_id not in before_indexes
        ]
        assert len(fresh) >= 3
        ordered = sorted(fresh, key=lambda d: d.order_index)
        kinds = [(d.action, d.target) for d in ordered[:3]]
        assert kinds[0] == ("train", "marine")
        assert kinds[1] == ("research", "stimpack")
        assert kinds[2] == ("train", "marine")
        assert ordered[0].order_index < ordered[1].order_index < ordered[2].order_index

        obs, _ = _wait_until(
            env,
            lambda o: o.units.get("marine", 0) >= 2
            and (
                o.research.get("stimpack") in {"in_progress", "completed"}
                or "stimpack" in o.upgrades
            ),
            max_steps=45,
        )
        assert obs.units.get("marine", 0) >= 2
        assert (
            obs.research.get("stimpack") in {"in_progress", "completed"}
            or "stimpack" in obs.upgrades
        )
    finally:
        env.close()

"""Core contract boundary tests: ordering, capacity, partial cancel."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import TaskManager


def _wait(seconds: float = 1.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_mixed_research_keeps_order_index_among_trains() -> None:
    manager = TaskManager()
    manager.submit(
        [
            {"action": "train", "target": "marine", "count": 4},
            {"action": "research", "target": "stimpack"},
            {"action": "train", "target": "marine", "count": 2},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    active = manager.active_demands()
    assert [d.action for d in active] == ["train", "research", "train"]
    assert [d.order_index for d in active] == [0, 1, 2]
    assert [d.count for d in active if d.action == "train"] == [4, 2]


def test_same_type_orders_are_not_merged() -> None:
    """PLATFORM_PLAN §5.3: marine then marine must keep two ordered demands."""
    manager = TaskManager()
    manager.submit(
        [
            {"action": "train", "target": "marine", "count": 8},
            {"action": "build", "target": "barracks"},
            {"action": "train", "target": "marine", "count": 4},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    marine = [d for d in manager.active_demands() if d.action == "train"]
    assert len(marine) == 2
    assert marine[0].count == 8
    assert marine[1].count == 4
    assert marine[0].order_index < marine[1].order_index
    barracks = [d for d in manager.active_demands() if d.target == "barracks"]
    assert len(barracks) == 1
    assert marine[0].order_index < barracks[0].order_index < marine[1].order_index


def test_production_slots_block_extra_marines() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=1.0, game_time_limit_seconds=120.0))
    backend.buildings["supply_depot"] = 2
    backend.buildings["barracks"] = 1  # one production slot
    backend.supply_cap = 30
    backend.minerals = 500
    backend.vespene = 0

    obs, _, _, _ = env.step(
        [{"action": "train", "target": "marine", "count": 3}, _wait(1)]
    )
    marine = obs.training.get("marine", {})
    assert marine.get("in_production", 0) == 1
    assert marine.get("waiting_to_produce", 0) >= 1
    assert backend.units.get("marine", 0) == 0
    env.close()


def test_cancel_after_partial_train_keeps_in_flight_only() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=1.0, game_time_limit_seconds=300.0))
    backend.buildings["supply_depot"] = 2
    backend.buildings["barracks"] = 1
    backend.supply_cap = 40
    backend.minerals = 1000

    env.step([{"action": "train", "target": "marine", "count": 5}, _wait(1)])
    obs = None
    for _ in range(40):
        obs, _, _, _ = env.step([_wait(1)])
        demand = next(
            (
                d
                for d in env.task_manager.active_demands()
                if d.action == "train" and d.target == "marine"
            ),
            None,
        )
        if demand is None:
            continue
        if demand.produced >= 1 and demand.remaining >= 2:
            break
    assert obs is not None
    demand = next(
        d
        for d in env.task_manager.active_demands()
        if d.action == "train" and d.target == "marine"
    )
    living_before = int(obs.units.get("marine", 0))
    assert demand.produced >= 1
    assert living_before == demand.produced
    in_prod = int(obs.training.get("marine", {}).get("in_production", 0))
    produced_before = demand.produced
    count_before = demand.count

    obs, feedback, _, _ = env.step(
        [
            {"action": "cancel", "target_action": "train", "target": "marine"},
            _wait(1),
        ]
    )
    assert feedback.receipts[0].result == "accepted"
    assert "cleared_waiting=" in (feedback.receipts[0].reason or "")
    cleared = int((feedback.receipts[0].reason or "").split("cleared_waiting=")[1])
    assert cleared >= 1

    demand = next(
        (
            d
            for d in env.task_manager.active_demands()
            if d.action == "train" and d.target == "marine"
        ),
        None,
    )
    if in_prod >= 1:
        assert demand is not None
        assert demand.count == produced_before + 1
        assert obs.training.get("marine", {}).get("waiting_to_produce", 0) == 0
        expected_final = produced_before + 1
    else:
        # Cancelled between slots: demand should complete at produced_before.
        expected_final = produced_before
        assert demand is None or demand.count == produced_before

    for _ in range(40):
        obs, _, _, _ = env.step([_wait(1)])
        active = [
            d
            for d in env.task_manager.active_demands()
            if d.action == "train" and d.target == "marine"
        ]
        if not active:
            break
    assert obs.units.get("marine", 0) == expected_final
    assert obs.units.get("marine", 0) < count_before
    assert not any(
        d.action == "train" and d.target == "marine" and d.is_active
        for d in env.task_manager.demands.values()
    )
    env.close()


def test_cancel_waiting_train_order_leaves_other_marine_order() -> None:
    manager = TaskManager()
    manager.submit(
        [
            {"action": "train", "target": "marine", "count": 8},
            {"action": "train", "target": "marine", "count": 4},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    first, second = [d for d in manager.active_demands() if d.action == "train"]
    first.state = DemandState.IN_PRODUCTION
    first.produced = 2
    receipts = manager.submit(
        [
            {"action": "cancel", "target_action": "train", "target": "marine"},
            {"action": "wait"},
        ],
        game_time=1.0,
    )
    assert receipts[0].result == "accepted"
    active = [d for d in manager.active_demands() if d.action == "train"]
    # Fully waiting second order cancelled; first trimmed to produced+1.
    assert len(active) == 1
    assert active[0].demand_id == first.demand_id
    assert active[0].count == 3
    assert active[0].produced == 2

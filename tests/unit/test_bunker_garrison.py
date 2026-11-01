"""Bunker passenger conservation and automatic garrison assignment."""
from __future__ import annotations


from sc2bench_env.backends.sharpy.bunker import assign_bunker_marines
from sc2bench_env.backends.sharpy.state_reader import _count_new_passengers
from sc2bench_env.interface.observation_text import section
from sc2bench_env.interface.observations import split_own_forces
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.runtime.task_manager import TaskManager


class _Type:
    def __init__(self, name: str) -> None:
        self.name = name


class _Passenger:
    def __init__(self, tag: int, type_name: str) -> None:
        self.tag = tag
        self.type_id = _Type(type_name)


class _Carrier:
    def __init__(self, type_name: str, passengers) -> None:
        self.type_id = _Type(type_name)
        self.passengers = passengers


class _Adapter:
    def normalize_unit_name(self, type_name: str):
        return str(type_name).lower()


def test_threatened_bunker_fills_before_unthreatened_half_budget() -> None:
    free = list(range(1, 11))
    assigned = assign_bunker_marines(
        [
            {"id": 20, "occupied": 0, "threatened": False},
            {"id": 10, "occupied": 0, "threatened": True},
        ],
        free,
    )
    by_bunker: dict[int, list[int]] = {}
    for bunker_id, tag in assigned:
        by_bunker.setdefault(bunker_id, []).append(tag)
    assert by_bunker[10] == [1, 2, 3, 4]
    assert by_bunker[20] == [5, 6, 7, 8]
    assert len({tag for _, tag in assigned}) == 8


def test_unthreatened_budget_and_full_bunker() -> None:
    assert assign_bunker_marines(
        [{"id": 1, "occupied": 0, "threatened": False}],
        [1, 2, 3],
    ) == [(1, 1)]
    assert assign_bunker_marines(
        [{"id": 1, "occupied": 0, "threatened": False}],
        [1],
    ) == []
    assert assign_bunker_marines(
        [{"id": 1, "occupied": 0, "threatened": True}],
        [1],
    ) == [(1, 1)]
    assert assign_bunker_marines(
        [{"id": 1, "occupied": 4, "threatened": True}],
        [1, 2, 3, 4],
    ) == []


def test_one_marine_is_not_assigned_twice() -> None:
    assigned = assign_bunker_marines(
        [
            {"id": 1, "occupied": 0, "threatened": True},
            {"id": 2, "occupied": 0, "threatened": True},
        ],
        [7, 8, 9],
    )
    assert [bunker for bunker, _ in assigned] == [1, 1, 1]
    assert len({tag for _, tag in assigned}) == 3


def test_bunker_passengers_are_counted_once() -> None:
    units = {"marine": 1}
    unavailable: dict[str, int] = {}
    ready = {"marine": [1]}
    garrison: dict[str, int] = {}
    _count_new_passengers(
        [
            _Carrier("BUNKER", [_Passenger(1, "MARINE"), _Passenger(2, "MARINE")]),
            _Carrier("MEDIVAC", [_Passenger(3, "MARINE")]),
        ],
        adapter=_Adapter(),
        units=units,
        unavailable_army=unavailable,
        ready_unit_tags=ready,
        counted_tags={1},
        garrison=garrison,
    )
    assert units == {"marine": 3}
    assert unavailable == {"marine": 2}
    assert garrison == {"marine": 2}
    assert sorted(ready["marine"]) == [1, 2, 3]


def test_observation_shows_garrison_only_when_present() -> None:
    empty = split_own_forces({"marine": 4, "scv": 12}).to_dict()
    assert "bunker_garrison" not in empty
    filled = split_own_forces(
        {"marine": 6, "scv": 12},
        assigned={"marine": 4},
        bunker_garrison={"marine": 4},
    ).to_dict()
    assert filled["bunker_garrison"] == {"marine": 4}
    assert filled["free"] == {"marine": 2}
    text = section("own_forces", filled)
    assert "Bunker garrison: marine 4" in text
    assert "Bunker garrison" not in section("own_forces", empty)


def test_rejected_combat_reports_bunker_garrison() -> None:
    receipts = TaskManager().submit_decision(
        parse_decision([
            {"name": "combat", "arguments": {"style": "attack", "target": "zone_3", "units": {"marine": 4}}},
            {"name": "advance", "arguments": {"seconds": 1}},
        ]),
        game_time=10,
        idle_army={"marine": 1},
        bunker_garrison={"marine": 4, "marauder": 1},
    )
    receipt = receipts[0]
    assert receipt.result == "rejected"
    assert receipt.reason == "insufficient_units"
    assert receipt.details["bunker_garrison"] == {"marine": 4}
    assert "marauder" not in receipt.details["bunker_garrison"]

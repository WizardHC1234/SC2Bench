"""Defensive building spots stay off the ramp and off the production grid."""

from sc2bench_env.backends.sharpy.defense_placement import choose_defense_position


def test_bunker_uses_threatened_entrance_without_blocking_the_ramp() -> None:
    ramp_bottom = (10, 10)
    behind = (10, 16)
    on_ramp = (10, 12)
    chosen = choose_defense_position(
        "bunker",
        [on_ramp, behind, (30, 30)],
        ramp_tops=[(10, 14)],
        threatened_ramps=[(10, 14)],
        ramp_bottoms=[ramp_bottom],
    )
    assert chosen == behind


def test_bunker_prefers_ground_behind_the_ramp() -> None:
    chosen = choose_defense_position(
        "bunker",
        [(4, 4), (10, 18), (40, 40)],
        ramp_tops=[(10, 14)],
        production=[(4, 8)],
    )
    assert chosen == (10, 18)


def test_turret_covers_an_open_mineral_line() -> None:
    chosen = choose_defense_position(
        "missile_turret",
        [(0, 6), (30, 30)],
        minerals=[(0, 0)],
        production=[(12, 0)],
    )
    assert chosen == (0, 6)


def test_second_turret_does_not_stack_on_the_first() -> None:
    chosen = choose_defense_position(
        "missile_turret",
        [(0, 6), (10, 0)],
        minerals=[(0, 0)],
        production=[(12, 0)],
        existing=[(0, 5)],
    )
    assert chosen == (10, 0)


def test_sensor_tower_faces_out_from_the_base() -> None:
    chosen = choose_defense_position(
        "sensor_tower",
        [(8, 10), (18, 10), (2, 10)],
        townhalls=[(10, 10)],
        map_center=(40, 10),
    )
    assert chosen == (18, 10)


def test_missing_defensive_spot_falls_back() -> None:
    assert choose_defense_position("bunker", []) is None
    assert choose_defense_position("sensor_tower", [(10, 10)]) is None


def test_defensive_act_is_a_grid_build_that_avoids_the_wall() -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sharpy.plans.acts import GridBuilding

    from sc2bench_env.backends.sharpy.defense_placement import DefensiveGridBuilding

    act = DefensiveGridBuilding(UnitTypeId.BUNKER, 1, "bunker")
    assert isinstance(act, GridBuilding)
    assert act.allow_wall is False
    assert act.kind == "bunker"


def test_zerg_defensive_act_disables_the_pylon_fallback(monkeypatch) -> None:
    import asyncio

    from sc2.ids.unit_typeid import UnitTypeId

    from sc2bench_env.backends.sharpy.defense_placement import (
        DefensiveGridBuilding,
        ZergDefensiveGridBuilding,
    )

    async def generic_start(self, knowledge):
        self.make_pylon = object()

    monkeypatch.setattr(DefensiveGridBuilding, "start", generic_start)
    act = ZergDefensiveGridBuilding(UnitTypeId.SPINECRAWLER, 1, "spine_crawler")
    asyncio.run(act.start(None))

    assert act.make_pylon is None

"""Zone contents distinguish own state, current enemy evidence, and history."""

from types import SimpleNamespace as NS

from sc2bench_env.backends.sharpy.zone_contents import (
    ZoneEnemyTracker,
    is_currently_visible_enemy,
    normalized_unit_name,
    summarize_entities,
)
from sc2bench_env.backends.sharpy.state_reader import _place_loaded_passengers_with_transport


class Adapter:
    ALIASES = {
        "SCV": "scv",
        "MEDIVAC": "medivac",
        "SIEGETANKSIEGED": "siege_tank",
        "COMMANDCENTER": "command_center",
    }

    def normalize_unit_name(self, name):
        return self.ALIASES.get(name)


def entity(tag, raw="ZERGLING", *, name="Zergling", structure=False,
           x=0, y=0, visible=True, memory=False, snapshot=False,
           hallucination=False, health=100, passengers=None):
    point = NS(x=x, y=y)
    return NS(tag=tag, type_id=NS(name=raw), name=name,
              is_structure=structure, position=point, x=x, y=y,
              is_visible=visible, is_memory=memory, is_snapshot=snapshot,
              is_hallucination=hallucination, health=health,
              passengers=list(passengers or []))


def test_own_contents_include_structures_forms_and_loaded_passengers_once():
    passenger = entity(3, "SIEGETANKSIEGED", name="Siege Tank (Sieged)")
    transport = entity(2, "MEDIVAC", name="Medivac", passengers=[passenger])
    values = summarize_entities([
        entity(1, "SCV", name="SCV"),
        transport,
        passenger,  # transition frame: also still present outside cargo
        entity(4, "COMMANDCENTER", name="Command Center", structure=True),
    ], Adapter(), include_loaded_passengers=True)
    assert values == {
        "units": {"medivac": 1, "scv": 1, "siege_tank": 1},
        "buildings": {"command_center": 1},
    }


def test_loaded_passenger_follows_transport_zone_across_boundary_once():
    passenger = entity(3, "SIEGETANKSIEGED", name="Siege Tank (Sieged)")
    transport = entity(2, "MEDIVAC", name="Medivac", passengers=[passenger])
    assigned = {"zone_0": [passenger], "zone_1": [transport]}
    _place_loaded_passengers_with_transport(assigned)
    assert passenger not in assigned["zone_0"]
    assert assigned["zone_1"] == [transport, passenger]
    totals = {zone: summarize_entities(items, Adapter()) for zone, items in assigned.items()}
    assert totals["zone_0"]["units"] == {}
    assert totals["zone_1"]["units"] == {"medivac": 1, "siege_tank": 1}


def test_cross_race_name_uses_game_data_display_name():
    cannon = entity(1, "PHOTONCANNON", name="Photon Cannon", structure=True)
    assert normalized_unit_name(cannon, Adapter()) == "photon_cannon"


def test_current_enemy_filter_rejects_fog_snapshot_hallucination_and_dead():
    assert is_currently_visible_enemy(entity(1))
    assert not is_currently_visible_enemy(entity(1, visible=False))
    assert not is_currently_visible_enemy(entity(1, memory=True))
    assert not is_currently_visible_enemy(entity(1, snapshot=True))
    assert not is_currently_visible_enemy(entity(1, hallucination=True))
    assert not is_currently_visible_enemy(entity(1, health=0))


def test_visible_enemy_moves_to_history_then_reobserved_position_clears():
    visible_positions = set()
    ai = NS(time=10.0, is_visible=lambda point: (point.x, point.y) in visible_positions)
    tracker = ZoneEnemyTracker()
    zergling = entity(7, x=4, y=5)

    current = tracker.observe(
        ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": [zergling]}, now=10
    )
    assert current["zone_0"]["visible_enemy_contents"]["units"] == {"zergling": 1}
    assert current["zone_0"]["last_seen_enemy_contents"]["units"] == {}
    assert current["zone_0"]["enemy_information_age_seconds"] is None

    zergling.is_visible = False
    zergling.is_memory = True
    history = tracker.observe(
        ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": [zergling]}, now=14.25
    )
    assert history["zone_0"]["visible_enemy_contents"]["units"] == {}
    assert history["zone_0"]["last_seen_enemy_contents"]["units"] == {"zergling": 1}
    assert history["zone_0"]["enemy_information_age_seconds"] == 4.2

    visible_positions.add((4, 5))
    cleared = tracker.observe(
        ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": []}, now=16
    )
    assert cleared["zone_0"]["last_seen_enemy_contents"]["units"] == {}
    assert cleared["zone_0"]["enemy_information_age_seconds"] is None


def test_same_tag_reappearing_in_new_zone_updates_without_stale_duplicate():
    ai = NS(time=0.0, is_visible=lambda point: False)
    tracker = ZoneEnemyTracker()
    tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={
        "zone_0": [entity(9, x=1)], "zone_1": []}, now=0)
    moved = tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={
        "zone_0": [], "zone_1": [entity(9, x=20)]}, now=3)
    assert moved["zone_0"]["last_seen_enemy_contents"]["units"] == {}
    assert moved["zone_1"]["visible_enemy_contents"]["units"] == {"zergling": 1}


def test_information_age_is_oldest_history_and_reset_forgets_episode():
    ai = NS(time=0.0, is_visible=lambda point: False)
    tracker = ZoneEnemyTracker()
    first = entity(1, x=1)
    second = entity(2, raw="PYLON", name="Pylon", structure=True, x=2)
    tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": [first]}, now=1)
    first.is_visible = False
    tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": [second]}, now=4)
    second.is_visible = False
    history = tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": []}, now=9)
    assert history["zone_0"]["last_seen_enemy_contents"] == {
        "units": {"zergling": 1}, "buildings": {"pylon": 1}}
    assert history["zone_0"]["enemy_information_age_seconds"] == 8.0
    tracker.reset()
    reset = tracker.observe(ai=ai, adapter=Adapter(), visible_by_zone={"zone_0": []}, now=10)
    assert reset["zone_0"]["last_seen_enemy_contents"] == {"units": {}, "buildings": {}}

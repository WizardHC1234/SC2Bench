"""Fixed early/mid/late Brief Observation samples for plan baselines.

Samples are synthetic Observation dicts shaped like FakeBackend/Sharpy output.
They are not strategy advice; they pin field coverage and Brief rendering.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _zone(
    zone_id: str,
    *,
    role: str,
    owner: str,
    vision: str = "visible",
    own_units: dict | None = None,
    own_buildings: dict | None = None,
    enemy_units: dict | None = None,
    enemy_buildings: dict | None = None,
    last_seen_units: dict | None = None,
    last_seen_buildings: dict | None = None,
    age: float | None = None,
    weapon_threat: bool = False,
) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "zone_role": role,
        "known_owner": owner,
        "vision_state": vision,
        "own_contents": {"units": dict(own_units or {}), "buildings": dict(own_buildings or {})},
        "visible_enemy_contents": {
            "units": dict(enemy_units or {}),
            "buildings": dict(enemy_buildings or {}),
        },
        "last_seen_enemy_contents": {
            "units": dict(last_seen_units or {}),
            "buildings": dict(last_seen_buildings or {}),
        },
        "enemy_information_age_seconds": age,
        "visible_enemy_weapon_in_range": weapon_threat,
    }


def _base_resources(zone_id: str, minerals: int, vespene: int, gas: int, free: int) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "minerals_remaining": minerals,
        "vespene_remaining": vespene,
        "owned_gas_structure_count": gas,
        "available_geyser_slots": free,
    }


def terran_early() -> dict[str, Any]:
    return {
        "game": {
            "game_time_seconds": 90.0,
            "game_time_limit_seconds": 1800.0,
            "seconds_remaining": 1710.0,
            "race": "terran",
            "enemy_race": "zerg",
            "status": "ongoing",
        },
        "economy": {
            "minerals": 150, "vespene": 0, "supply_used": 15, "supply_cap": 23,
            "supply_left": 8, "army_supply": 0, "worker_count": 14,
            "workers_on_minerals": 12, "mineral_worker_saturation": 16,
            "workers_on_vespene": 0, "vespene_worker_saturation": 0,
            "workers_other": 2, "mineral_income_per_minute": 780.0,
            "vespene_income_per_minute": 0.0,
        },
        "map_control": {
            "own_base_count": 1, "known_enemy_base_count": 1,
            "unconfirmed_expansion_count": 12,
            "base_resources": [_base_resources("zone_0", 14000, 4500, 0, 2)],
        },
        "map_topology": {"zones": [{"zone_id": f"zone_{i}"} for i in range(16)]},
        "available_targets": {
            "build": ["supply_depot", "refinery", "barracks", "command_center"],
            "train": ["scv"], "research": [], "morph_townhall": [],
        },
        "production": [{"facility": "command_center", "ready_grounded": 1,
                        "capacity": 1, "free_slots": 1}],
        "production_priority": [],
        "building": {"command_center": {"completed": 1, "under_construction": 0},
                     "supply_depot": {"completed": 1, "under_construction": 0}},
        "training": {},
        "research": {},
        "own_forces": {"workers": {"scv": 14}, "army": {}, "assigned": {}, "free": {}},
        "structures": [{"id": "townhall_0", "type": "command_center"}],
        "abilities": {},
        "combat": {"group_0": {"status": "idle", "style": "defend", "target": "zone_0",
                               "phase": "guarding", "alive": {}}},
        "scouting": {},
        "zone_state": [
            _zone("zone_0", role="own_main", owner="self",
                  own_units={"scv": 14}, own_buildings={"command_center": 1, "supply_depot": 1}),
            _zone("zone_15", role="enemy_main", owner="enemy", vision="fogged",
                  last_seen_buildings={"hatchery": 1}, age=40.0),
        ],
        "relevant_zone_ids": ["zone_0", "zone_15"],
        "recent_events": [{"type": "building_completed", "target": "supply_depot",
                           "game_time_seconds": 78.0}],
        "terminated": False,
    }


def terran_mid() -> dict[str, Any]:
    sample = terran_early()
    sample["game"]["game_time_seconds"] = 564.0
    sample["game"]["seconds_remaining"] = 1236.0
    sample["economy"].update({
        "minerals": 640, "vespene": 220, "supply_used": 74, "supply_cap": 100,
        "supply_left": 26, "army_supply": 42, "worker_count": 32,
        "workers_on_minerals": 24, "mineral_worker_saturation": 32,
        "workers_on_vespene": 6, "vespene_worker_saturation": 6,
        "workers_other": 2, "mineral_income_per_minute": 1120.0,
        "vespene_income_per_minute": 336.0,
    })
    sample["map_control"] = {
        "own_base_count": 2, "known_enemy_base_count": 2,
        "unconfirmed_expansion_count": 8,
        "base_resources": [
            _base_resources("zone_0", 8200, 2100, 2, 0),
            _base_resources("zone_1", 11000, 4000, 2, 0),
        ],
    }
    sample["available_targets"] = {
        "build": ["supply_depot", "refinery", "barracks", "factory", "starport",
                  "engineering_bay", "command_center"],
        "train": ["scv", "marine", "marauder", "medivac", "siege_tank"],
        "research": ["stimpack", "combat_shield"],
        "morph_townhall": ["orbital_command"],
    }
    sample["production"] = [
        {"facility": "command_center", "ready_grounded": 2, "capacity": 2, "free_slots": 1},
        {"facility": "barracks", "ready_grounded": 3, "techlab_hosts": 1, "reactor_hosts": 1,
         "capacity": 5, "free_slots": 2},
        {"facility": "factory", "ready_grounded": 1, "techlab_hosts": 1,
         "capacity": 1, "free_slots": 0},
    ]
    sample["production_priority"] = [
        {"action": "train", "target": "marine", "remaining": 8, "in_production": 2,
         "waiting_to_produce": 6, "state": "waiting_to_start", "waiting_for": "production_slot",
         "request_count": 2},
        {"action": "train", "target": "siege_tank", "remaining": 1, "in_production": 1,
         "waiting_to_produce": 0, "state": "in_progress"},
    ]
    sample["building"] = {
        "command_center": {"completed": 2, "under_construction": 0},
        "orbital_command": {"completed": 1, "under_construction": 0},
        "supply_depot": {"completed": 6, "under_construction": 1},
        "barracks": {"completed": 3, "under_construction": 0},
        "factory": {"completed": 1, "under_construction": 0},
        "refinery": {"completed": 4, "under_construction": 0},
    }
    sample["training"] = {"marine": {"in_production": 2}, "siege_tank": {"in_production": 1}}
    sample["research"] = {"stimpack": "in_progress", "combat_shield": "not_started"}
    sample["own_forces"] = {
        "workers": {"scv": 32},
        "army": {"marine": 18, "marauder": 4, "medivac": 2, "siege_tank": 1},
        "assigned": {"marine": 12, "marauder": 4, "medivac": 1, "siege_tank": 1},
        "free": {"marine": 6, "medivac": 1},
    }
    sample["abilities"] = {"scan_ready": 1, "mule_ready": 1}
    sample["combat"] = {
        "group_0": {"status": "idle", "style": "defend", "target": "zone_1",
                    "phase": "guarding"},
        "group_1": {"status": "active", "style": "attack", "target": "zone_12",
                    "phase": "executing", "nearest_zone": "zone_8",
                    "alive": {"marine": 12, "marauder": 4, "medivac": 1, "siege_tank": 1},
                    "visible_enemy_nearby": {"units": 5, "buildings": 0}},
    }
    sample["scouting"] = {
        "scout_1": {"unit": "scv", "moving_to": "zone_14", "phase": "moving"},
    }
    sample["zone_state"] = [
        _zone("zone_0", role="own_main", owner="self",
              own_units={"scv": 12}, own_buildings={"command_center": 1}),
        _zone("zone_1", role="own_natural", owner="self",
              own_units={"scv": 16, "marine": 6}, own_buildings={"command_center": 1},
              enemy_units={"zergling": 6}, weapon_threat=True),
        _zone("zone_8", role="other_expansion", owner="unconfirmed",
              own_units={"marine": 12, "marauder": 4}),
        _zone("zone_12", role="enemy_expansion", owner="enemy", vision="fogged",
              last_seen_units={"drone": 8}, last_seen_buildings={"hatchery": 1}, age=28.0),
        _zone("zone_14", role="enemy_main", owner="enemy", vision="fogged",
              last_seen_buildings={"hatchery": 1, "spawning_pool": 1}, age=96.0),
    ]
    sample["relevant_zone_ids"] = ["zone_0", "zone_1", "zone_8", "zone_12", "zone_14"]
    sample["recent_events"] = [
        {"type": "unit_completed", "target": "marine", "count": 2, "game_time_seconds": 560.0},
        {"type": "group_order_changed", "group": "group_1", "from_style": "attack",
         "to_style": "attack", "game_time_seconds": 552.0},
    ]
    return sample


def terran_late() -> dict[str, Any]:
    sample = terran_mid()
    sample["game"]["game_time_seconds"] = 1120.0
    sample["game"]["seconds_remaining"] = 680.0
    sample["economy"].update({
        "minerals": 980, "vespene": 540, "supply_used": 156, "supply_cap": 200,
        "supply_left": 44, "army_supply": 96, "worker_count": 50,
        "workers_on_minerals": 36, "mineral_worker_saturation": 48,
        "workers_on_vespene": 12, "vespene_worker_saturation": 12,
        "workers_other": 2, "mineral_income_per_minute": 1480.0,
        "vespene_income_per_minute": 560.0,
    })
    sample["map_control"]["own_base_count"] = 3
    sample["map_control"]["base_resources"].append(
        _base_resources("zone_2", 9000, 3800, 2, 0)
    )
    sample["own_forces"] = {
        "workers": {"scv": 50},
        "army": {"marine": 28, "marauder": 10, "medivac": 4, "siege_tank": 6, "viking": 4},
        "assigned": {"marine": 20, "marauder": 8, "medivac": 3, "siege_tank": 5, "viking": 2},
        "free": {"marine": 8, "marauder": 2, "medivac": 1, "siege_tank": 1, "viking": 2},
        "bunker_garrison": {"marine": 4},
    }
    sample["combat"] = {
        "group_0": {"status": "idle", "style": "defend", "target": "zone_1", "phase": "guarding"},
        "group_1": {"status": "active", "style": "attack", "target": "zone_15",
                    "phase": "executing", "nearest_zone": "zone_12",
                    "alive": {"marine": 14, "marauder": 6, "medivac": 2, "siege_tank": 3},
                    "visible_enemy_nearby": {"units": 11, "buildings": 2}},
        "group_2": {"status": "active", "style": "defend", "target": "zone_2",
                    "phase": "holding_after_attack",
                    "alive": {"marine": 6, "marauder": 2, "siege_tank": 2, "viking": 2},
                    "visible_enemy_nearby": {"units": 0, "buildings": 0}},
    }
    sample["zone_state"].append(
        _zone("zone_2", role="own_expansion", owner="self",
              own_units={"scv": 14}, own_buildings={"command_center": 1})
    )
    sample["relevant_zone_ids"].append("zone_2")
    return sample


def protoss_mid() -> dict[str, Any]:
    sample = deepcopy(terran_mid())
    sample["game"]["race"] = "protoss"
    sample["game"]["enemy_race"] = "terran"
    sample["economy"]["worker_count"] = 30
    sample["available_targets"] = {
        "build": ["nexus", "pylon", "assimilator", "gateway", "cybernetics_core"],
        "train": ["probe", "zealot", "stalker", "immortal"],
        "research": ["warp_gate", "blink"],
        "morph_townhall": [],
    }
    sample["production"] = [
        {"facility": "nexus", "ready_grounded": 2, "capacity": 2, "free_slots": 1},
        {"facility": "gateway", "ready_grounded": 2, "capacity": 2, "free_slots": 1},
        {"facility": "warpgate", "ready_grounded": 2, "warp_ready": 1, "cooling_down": 1},
    ]
    sample["production_priority"] = [
        {"action": "train", "target": "stalker", "remaining": 5,
         "in_production": 1, "waiting_to_produce": 4,
         "state": "waiting_to_start", "waiting_for": "producer_busy"},
    ]
    sample["building"] = {
        "nexus": {"completed": 2, "under_construction": 0},
        "pylon": {"completed": 8, "under_construction": 1},
        "gateway": {"completed": 2, "under_construction": 0},
        "warpgate": {"completed": 2, "under_construction": 0},
        "robotics_facility": {"completed": 1, "under_construction": 0},
    }
    sample["training"] = {"stalker": {"in_production": 1}}
    sample["research"] = {"warp_gate": "completed", "blink": "in_progress"}
    sample["structures"] = [{"id": "townhall_0", "type": "nexus"}]
    sample["own_forces"] = {
        "workers": {"probe": 30},
        "army": {"zealot": 10, "stalker": 8, "immortal": 2},
        "assigned": {"zealot": 6, "stalker": 6, "immortal": 2},
        "free": {"zealot": 4, "stalker": 2},
    }
    sample["abilities"] = {"chrono_ready": 2}
    sample["combat"] = {
        "group_0": {"status": "idle", "style": "defend", "target": "zone_1", "phase": "guarding"},
        "group_1": {"status": "active", "style": "attack", "target": "zone_12",
                    "phase": "executing", "nearest_zone": "zone_8",
                    "alive": {"zealot": 6, "stalker": 6, "immortal": 2},
                    "visible_enemy_nearby": {"units": 4, "buildings": 0}},
    }
    return sample


def zerg_mid() -> dict[str, Any]:
    sample = deepcopy(terran_mid())
    sample["game"]["race"] = "zerg"
    sample["game"]["enemy_race"] = "protoss"
    sample["economy"]["worker_count"] = 34
    sample["available_targets"] = {
        "build": ["hatchery", "extractor", "spawning_pool", "roach_warren"],
        "train": ["drone", "zergling", "roach", "overlord", "queen"],
        "research": ["metabolic_boost", "glial_reconstitution"],
        "morph_townhall": ["lair"],
    }
    sample["production"] = [
        {"facility": "hatchery", "ready_grounded": 2, "larva": 5, "capacity": 5, "free_slots": 3},
    ]
    sample["production_priority"] = [
        {"action": "train", "target": "zergling", "remaining": 8,
         "in_production": 4, "waiting_to_produce": 4,
         "state": "waiting_to_start", "waiting_for": "resources"},
        {"action": "train", "target": "roach", "remaining": 2,
         "in_production": 2, "waiting_to_produce": 0,
         "state": "in_progress"},
    ]
    sample["building"] = {
        "hatchery": {"completed": 2, "under_construction": 0},
        "spawning_pool": {"completed": 1, "under_construction": 0},
        "roach_warren": {"completed": 1, "under_construction": 0},
        "extractor": {"completed": 4, "under_construction": 0},
    }
    sample["training"] = {"zergling": {"in_production": 4}, "roach": {"in_production": 2}}
    sample["research"] = {"metabolic_boost": "completed"}
    sample["structures"] = [{"id": "townhall_0", "type": "hatchery"}]
    sample["own_forces"] = {
        "workers": {"drone": 34},
        "army": {"zergling": 24, "roach": 10, "overlord": 6, "queen": 2},
        "assigned": {"zergling": 16, "roach": 8},
        "free": {"zergling": 8, "roach": 2, "overlord": 6, "queen": 2},
    }
    sample["abilities"] = {"inject_ready": 2}
    sample["combat"] = {
        "group_0": {"status": "idle", "style": "defend", "target": "zone_1", "phase": "guarding"},
        "group_1": {"status": "active", "style": "attack", "target": "zone_12",
                    "phase": "executing", "nearest_zone": "zone_8",
                    "alive": {"zergling": 16, "roach": 8},
                    "visible_enemy_nearby": {"units": 7, "buildings": 1}},
    }
    return sample


STAGE_SAMPLES = {
    ("terran", "early"): terran_early,
    ("terran", "mid"): terran_mid,
    ("terran", "late"): terran_late,
    ("protoss", "mid"): protoss_mid,
    ("zerg", "mid"): zerg_mid,
}


def living_army_counts(observation: dict[str, Any]) -> dict[str, int]:
    forces = observation.get("own_forces") or {}
    army = forces.get("army") if isinstance(forces, dict) else {}
    return {str(name): int(count) for name, count in dict(army or {}).items() if int(count) > 0}


def group_alive_total(observation: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for name, row in dict(observation.get("combat") or {}).items():
        if name == "group_0" or not isinstance(row, dict):
            continue
        for unit, count in dict(row.get("alive") or {}).items():
            totals[str(unit)] = totals.get(str(unit), 0) + int(count)
    return totals


def free_plus_assigned(observation: dict[str, Any]) -> dict[str, int]:
    forces = observation.get("own_forces") or {}
    totals: dict[str, int] = {}
    for key in ("free", "assigned"):
        for unit, count in dict(forces.get(key) or {}).items():
            totals[str(unit)] = totals.get(str(unit), 0) + int(count)
    garrison = forces.get("bunker_garrison") or {}
    for unit, count in dict(garrison).items():
        # Garrison is unavailable but still part of living army; do not double-count
        # if already present in assigned/free.
        totals.setdefault(str(unit), 0)
        _ = count
    return totals

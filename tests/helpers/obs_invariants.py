"""Observation section helpers for Phase 0 contract tests."""

from __future__ import annotations

from typing import Any, List

from sc2bench_env.interface.observations import Observation


def collect_obs_mismatches(obs: Observation) -> List[str]:
    mismatches: List[str] = []
    cc = int(obs.buildings.get("command_center", 0))
    orbital = int(obs.buildings.get("orbital_command", 0))
    pf = int(obs.buildings.get("planetary_fortress", 0))
    if obs.race == "terran" and obs.base_count != cc + orbital + pf:
        mismatches.append(
            f"base_count={obs.base_count} != townhall sum {cc + orbital + pf}"
        )
    if obs.race == "terran" and obs.orbital_count != orbital:
        mismatches.append(
            f"orbital_count={obs.orbital_count} != buildings.orbital_command={orbital}"
        )
    if "tasks" in obs.to_dict():
        mismatches.append("observation still exposes tasks list")
    if obs.resources.minerals < 0 or obs.resources.vespene < 0:
        mismatches.append(f"negative resources: {obs.resources}")
    if obs.economy.minerals != obs.resources.minerals:
        mismatches.append("economy.minerals != resources.minerals")
    if obs.economy.vespene != obs.resources.vespene:
        mismatches.append("economy.vespene != resources.vespene")
    if obs.map_control.own_base_count < obs.base_count:
        mismatches.append("map_control.own_base_count < ready base_count")
    zone_ids = [row.get("zone_id") for row in obs.zone_state if isinstance(row, dict)]
    if obs.zone_state and zone_ids != list(obs.zones):
        mismatches.append("zone_state ids do not match zones list")
    if obs.zone_state:
        owners = [row.get("known_owner") for row in obs.zone_state]
        if any(owner not in {"self", "enemy", "unconfirmed"} for owner in owners):
            mismatches.append("invalid or missing zone known_owner")
        if sum(owner == "self" for owner in owners) != obs.map_control.own_base_count:
            mismatches.append("zone self owners != map own base count")
        if sum(owner == "enemy" for owner in owners) != obs.map_control.known_enemy_base_count:
            mismatches.append("zone enemy owners != known enemy base count")
        if sum(owner == "unconfirmed" for owner in owners) != obs.map_control.unconfirmed_expansion_count:
            mismatches.append("zone unconfirmed owners != map unconfirmed count")
        for row in obs.zone_state:
            for field in ("own_contents", "visible_enemy_contents", "last_seen_enemy_contents"):
                contents = row.get(field)
                if not isinstance(contents, dict) or set(contents) != {"units", "buildings"}:
                    mismatches.append(f"invalid {field} shape: {row}")
                    continue
                if any(not isinstance(values, dict) or any(int(count) <= 0 for count in values.values())
                       for values in contents.values()):
                    mismatches.append(f"invalid {field} counts: {row}")
            age = row.get("enemy_information_age_seconds")
            history = row.get("last_seen_enemy_contents") or {}
            has_history = any(history.get(group) for group in ("units", "buildings"))
            if (age is None) != (not has_history) or (age is not None and age < 0):
                mismatches.append(f"invalid enemy information age: {row}")
    resources = obs.map_control.base_resources
    if resources and [row.get("zone_id") for row in resources] != list(obs.zones):
        mismatches.append("base_resources ids do not match zones list")
    for row in resources:
        slots = row.get("geyser_slots")
        available = row.get("available_geyser_slots")
        owned = row.get("owned_gas_structure_count", 0)
        if slots is not None and (owned < 0 or owned > slots):
            mismatches.append(f"invalid gas ownership: {row}")
        if available is not None and (slots is None or available < 0 or available + owned > slots):
            mismatches.append(f"invalid gas availability: {row}")
        for prefix in ("minerals", "vespene"):
            remaining, initial = row.get(f"{prefix}_remaining"), row.get(f"{prefix}_initial")
            if remaining is not None and remaining < 0:
                mismatches.append(f"negative zone resources: {row}")
            if initial is not None and (initial < 0 or (remaining is not None and remaining > initial)):
                mismatches.append(f"invalid initial resource baseline: {row}")
    scv = int(obs.units.get("scv", 0))
    permanent_workers = sum(int(obs.units.get(name, 0)) for name in ("scv", "probe", "drone"))
    if obs.economy.worker_count != permanent_workers:
        mismatches.append("economy.worker_count != living permanent workers")
    if int(obs.own_forces.workers.get("scv", 0)) != scv:
        mismatches.append("own_forces.workers.scv != units.scv")
    marine = int(obs.units.get("marine", 0))
    if int(obs.own_forces.army.get("marine", 0)) != marine:
        mismatches.append("own_forces.army.marine != units.marine")
    return mismatches


def assert_obs_consistent(obs: Observation, **_kwargs: Any) -> None:
    mismatches = collect_obs_mismatches(obs)
    assert not mismatches, "Observation mismatches:\n- " + "\n- ".join(mismatches)

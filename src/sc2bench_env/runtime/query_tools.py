"""Queries that combine the client snapshot with the action catalog.

catalog.knowledge does not import the registry. Callers that need both come here.
"""
from __future__ import annotations


import difflib
import heapq
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sc2bench_env.catalog import knowledge
from sc2bench_env.catalog.registry import get_catalog, get_target


def catalog_roles(name: str, forms: Sequence[Mapping[str, Any]], race: str) -> List[str]:
    found = list(knowledge._roles(name, forms, race))
    if found != ["unknown"]:
        return found
    targets = list(get_catalog(race=race).targets)
    spec = next((item for item in targets if item.name == name), None)
    has_weapon = any(bool(item.get("weapons")) for item in forms)
    resolved: List[str] = []
    if spec is not None and spec.action == "research":
        resolved.append("tech")
    elif spec is not None and spec.action in {"build", "morph_townhall"}:
        if has_weapon:
            resolved.append("defense")
        elif "techlab" in name or name.endswith("reactor"):
            resolved.append("production")
        elif any(item.action == "train" and item.produced_at == name for item in targets):
            resolved.append("production")
        elif any(item.action == "research" and item.produced_at == name for item in targets):
            resolved.append("tech")
        else:
            resolved.append("tech")
    return resolved or ["unknown"]


def attach(payload: Dict[str, Any], race: str, name: str) -> Dict[str, Any]:
    result = knowledge.attach(payload, race, name)
    if result.get("roles") == ["unknown"]:
        result["roles"] = catalog_roles(name, knowledge._forms(race, name), race)
    return result


def names_for(race: str, kind: str) -> List[str]:
    """Canonical names a knowledge query of this kind can resolve."""
    names = []
    for spec in get_catalog(race=race).targets:
        if kind == "unit" and spec.action == "train":
            names.append(spec.name)
        elif kind == "building" and spec.action in {"build", "morph_townhall"}:
            names.append(spec.name)
        elif kind == "research" and spec.action == "research":
            names.append(spec.name)
    for entry, row in (knowledge._platform().get("observable_only") or {}).items():
        if row.get("kind") == kind and row.get("race") == race:
            names.append(entry)
    return names


def close_names(race: str, query: str, kind: str) -> List[str]:
    pool = names_for(race, kind)
    return difflib.get_close_matches(str(query or "").strip().lower(), pool, n=5, cutoff=0.5)


def observable_groups(race: str) -> Dict[str, List[str]]:
    controllable = {spec.name for spec in get_catalog(race=race).targets}
    mapping = ((knowledge._aliases().get("units") or {}).get(race) or {})
    units, buildings = [], []
    for canonical in sorted(set(mapping.values())):
        if canonical in controllable:
            continue
        row = knowledge._observable(canonical, race) or {}
        kind = row.get("kind")
        if kind is None:
            forms = knowledge._forms(race, canonical)
            kind = "building" if forms and all(item.get("is_structure") for item in forms) else "unit"
        if kind == "building":
            buildings.append(canonical)
        else:
            units.append(canonical)
    return {"units": units, "buildings": buildings}


def observable_payload(race: str, name: str, kind: str) -> Optional[Dict[str, Any]]:
    """A query hit for an observation alias that is not a production target."""
    canonical = str(name or "").strip().lower()
    forms = knowledge._forms(race, canonical)
    observed = knowledge._observable(canonical, race)
    listed = (knowledge._platform().get("observable_only") or {}).get(canonical)
    if isinstance(listed, Mapping) and listed.get("race") not in (None, "", race):
        return None
    if observed is None and not forms:
        return None
    if get_target(canonical, race=race) is not None:
        return None
    resolved_kind = (observed or {}).get("kind")
    if resolved_kind is None:
        resolved_kind = "building" if forms and all(item.get("is_structure") for item in forms) else "unit"
    if resolved_kind != kind:
        return {"name": canonical, "error": f"not_a_{kind}_target"}
    row = knowledge.primary_unit(race, canonical)
    payload: Dict[str, Any] = {
        "name": canonical,
        "minerals": "unknown" if row is None else row.get("mineral_incremental"),
        "vespene": "unknown" if row is None else row.get("vespene_incremental"),
        "time_seconds": "unknown" if row is None else row.get("build_time_seconds"),
        "prerequisites": [],
        "description": (observed or {}).get("platform_behavior") or "Observation alias. Not a direct production target.",
    }
    if kind == "unit":
        food = None if row is None else row.get("food_required")
        payload.update({
            "supply": food if food is not None else "unknown",
            "produced_at": "unknown",
        })
    elif kind == "building":
        payload.update({"builder": "unknown", "kind": "building"})
    return attach(payload, race, canonical)


from typing import Dict, List, Mapping, Optional, Sequence

from sc2bench_env.catalog.registry import get_target
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.tools import KNOWLEDGE_TOOLS

_KNOWLEDGE_RACES = ("terran", "protoss", "zerg")

def _spec_payload(spec, *, kind: str, race: str = "terran") -> Dict[str, Any]:
    payload = {
        "name": spec.name,
        "minerals": spec.minerals,
        "vespene": spec.vespene,
        "time_seconds": spec.base_time_seconds,
        "prerequisites": list(spec.prerequisites),
        "description": spec.description,
    }
    if spec.action == "morph_townhall":
        payload["action"] = "morph_townhall"
    if spec.mechanism:
        payload["mechanism"] = spec.mechanism
    if spec.production_batch_size != 1:
        payload["production_batch_size"] = spec.production_batch_size
    if kind == "unit":
        payload.update({
            "supply": spec.supply,
            "produced_at": spec.produced_at,
            "dispatchable": spec.dispatchable,
        })
    elif kind == "building":
        worker = {"protoss": "probe", "zerg": "drone"}.get(race, "scv")
        payload.update({"builder": spec.produced_at or spec.morph_from or worker, "kind": spec.kind})
    elif kind == "research":
        payload.update({"facility": spec.produced_at})
    return payload


def _lookup(name: str, *, race: str, expected_actions: Sequence[str], kind: str) -> Dict[str, Any]:
    spec = get_target(name, race=race)
    if spec is None:
        observed = observable_payload(race, name, kind)
        if observed is not None:
            return observed
        return {
            "name": name,
            "error": "unknown_target",
            "candidates": close_names(race, name, kind),
        }
    if spec.action not in expected_actions:
        return {"name": spec.name, "error": f"not_a_{kind}_target", "action": spec.action}
    payload = _spec_payload(spec, kind=kind, race=race)
    if spec.morph_from:
        payload["morph_from"] = spec.morph_from
    if kind == "research":
        payload["availability"] = "controllable"
        return payload
    return attach(payload, race, spec.name)


def query_unit_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {"results": [_lookup(name, race=race, expected_actions=("train",), kind="unit") for name in names]}


def query_building_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {
        "results": [
            _lookup(name, race=race, expected_actions=("build", "morph_townhall"), kind="building")
            for name in names
        ]
    }


def query_research_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {
        "results": [_lookup(name, race=race, expected_actions=("research",), kind="research") for name in names]
    }


def query_prerequisite_path(target: str, *, race: str = "terran") -> Dict[str, Any]:
    from sc2bench_env.runtime.query_tools import close_names

    spec = get_target(target, race=race)
    if spec is None:
        return {
            "target": target,
            "error": "unknown_target",
            "candidates": list(dict.fromkeys(
                close_names(race, target, "unit")
                + close_names(race, target, "building")
                + close_names(race, target, "research")
            )),
        }
    catalog = {item.name: get_target(item.name, race=race) for item in get_catalog(race=race).targets}
    ordered: List[str] = []
    seen: set[str] = set()

    def walk(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        item = catalog.get(name)
        if item is None:
            ordered.append(name)
            return
        for req in item.prerequisites:
            walk(req)
        if name not in ordered:
            ordered.append(name)

    walk(spec.name)
    steps = []
    for index, name in enumerate(ordered, start=1):
        item = catalog.get(name)
        steps.append({
            "step": index,
            "type": None if item is None else item.action,
            "target": name,
            "minerals": None if item is None else item.minerals,
            "gas": None if item is None else item.vespene,
            "time_seconds": None if item is None else item.base_time_seconds,
            "requires": [] if item is None else list(item.prerequisites),
        })
    return {
        "target": spec.name,
        "static_dependency_path": True,
        "note": "Static dependency path, not a match build order.",
        "path": ordered,
        "steps": steps,
    }


def _require_knowledge_race(race: Any) -> str:
    if race not in _KNOWLEDGE_RACES:
        raise ValueError("race must be terran, protoss or zerg")
    return str(race)


def query_race_data(race: str) -> Dict[str, Any]:
    from sc2bench_env.runtime.query_tools import observable_groups

    require_supported_own_race(race)
    catalog = get_catalog(race=race)
    units, buildings, research, structure = [], [], [], []
    for spec in catalog.targets:
        if not spec.executable:
            continue
        if spec.action == "train":
            units.append(spec.name)
        elif spec.action == "build":
            buildings.append(spec.name)
        elif spec.action == "research":
            research.append(spec.name)
        elif spec.action == "morph_townhall":
            structure.append(spec.name)
    return {
        "race": race,
        "units": units,
        "buildings": buildings,
        "research": research,
        "morph_townhall": structure,
        "observable_only": observable_groups(race),
        "platform_abilities": [
            spec.name for spec in catalog.targets
            if spec.action in {"scan", "call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor", "scout"}
        ],
    }


def query_map_overview(observation: Mapping[str, Any]) -> Dict[str, Any]:
    topology = dict(observation.get("map_topology") or {})
    return {"map_topology": topology}


def query_zone_state(observation: Mapping[str, Any], zone_ids: Sequence[str]) -> Dict[str, Any]:
    wanted = [str(zone) for zone in zone_ids]
    rows = [
        row for row in observation.get("zone_state") or []
        if isinstance(row, Mapping) and row.get("zone_id") in set(wanted)
    ]
    resources = [
        row for row in (observation.get("map_control") or {}).get("base_resources") or []
        if isinstance(row, Mapping) and row.get("zone_id") in set(wanted)
    ]
    missing = [zone for zone in wanted if zone not in {row.get("zone_id") for row in rows}]
    return {"zones": rows, "base_resources": resources, "unknown_zone_ids": missing}


def query_route(observation: Mapping[str, Any], from_zone: str, to_zone: str) -> Dict[str, Any]:
    topology = observation.get("map_topology") or {}
    rows = {row.get("zone_id"): row for row in topology.get("zones") or [] if isinstance(row, Mapping)}
    if from_zone not in rows or to_zone not in rows:
        return {"from_zone": from_zone, "to_zone": to_zone, "error": "unknown_zone"}
    if from_zone == to_zone:
        return {"from_zone": from_zone, "to_zone": to_zone, "path": [from_zone], "segments": [], "total_distance": 0}
    neighbors = {
        name: [item["zone_id"] for item in (row.get("corridor_neighbors") or []) if isinstance(item, Mapping)]
        for name, row in rows.items()
    }
    distances = {}
    for name, row in rows.items():
        for item in row.get("corridor_neighbors") or []:
            if isinstance(item, Mapping):
                distances[(name, item["zone_id"])] = item.get("path_distance")
    best = {from_zone: 0.0}
    queue = [(0.0, from_zone, [from_zone])]
    while queue:
        total, current, path = heapq.heappop(queue)
        if total != best.get(current):
            continue
        if current == to_zone:
            segments = [
                {"from": path[i], "to": path[i + 1],
                 "distance": distances.get((path[i], path[i + 1]))}
                for i in range(len(path) - 1)
            ]
            return {
                "from_zone": from_zone, "to_zone": to_zone, "path": path,
                "segments": segments, "total_distance": total,
                "distance_meaning": "shortest_known_path",
            }
        for neighbor in neighbors.get(current, []):
            step = distances.get((current, neighbor))
            if not isinstance(step, (int, float)):
                continue
            nxt = total + float(step)
            if nxt < best.get(neighbor, float("inf")):
                best[neighbor] = nxt
                heapq.heappush(queue, (nxt, neighbor, path + [neighbor]))
    return {"from_zone": from_zone, "to_zone": to_zone, "error": "no_corridor_path"}


def run_query(
    name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    observation: Optional[Mapping[str, Any]] = None,
    race: str = "terran",
) -> Dict[str, Any]:
    arguments = dict(arguments or {})
    observation = observation or {}
    try:
        if name == "query_map_overview":
            return query_map_overview(observation)
        if name == "query_zone_state":
            ids = arguments.get("zone_ids")
            if not isinstance(ids, list):
                return {"error": "zone_ids must be an array of strings"}
            return query_zone_state(observation, ids)
        if name == "query_route":
            return query_route(observation, str(arguments.get("from_zone") or ""), str(arguments.get("to_zone") or ""))
        if name in KNOWLEDGE_TOOLS:
            selected = _require_knowledge_race(arguments.get("race"))
            if name == "query_unit_data":
                return query_unit_data(list(arguments.get("names") or []), race=selected)
            if name == "query_building_data":
                return query_building_data(list(arguments.get("names") or []), race=selected)
            if name == "query_research_data":
                return query_research_data(list(arguments.get("names") or []), race=selected)
            if name == "query_prerequisite_path":
                return query_prerequisite_path(str(arguments.get("target") or ""), race=selected)
            return query_race_data(selected)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"error": f"unknown_tool {name}"}

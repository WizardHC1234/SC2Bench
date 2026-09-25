"""Load one client snapshot and answer static knowledge questions.

Numbers come from the snapshot. The overlay only adds names, roles and
platform behavior. A client that does not match a stored snapshot is refused.
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[3] / "data" / "sc2"
_MISSING = (
    "life", "shields", "energy", "weapon_name", "minimum_range",
    "target_restrictions", "movement_layer",
)


def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_root() -> Path:
    return _ROOT


@lru_cache(maxsize=1)
def _platform() -> Dict[str, Any]:
    path = _data_root() / "common" / "platform_behavior.json"
    if not path.is_file():
        raise RuntimeError(f"Platform behavior overlay is missing: {path}")
    return _read(path)


@lru_cache(maxsize=1)
def _aliases() -> Dict[str, Any]:
    path = _data_root() / "common" / "aliases.json"
    if not path.is_file():
        raise RuntimeError(f"Knowledge aliases are missing: {path}")
    return _read(path)


@lru_cache(maxsize=None)
def _snapshot(folder: str) -> Dict[str, Any]:
    root = _data_root() / "snapshots" / folder
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Knowledge snapshot is missing: {root}")
    manifest = _read(manifest_path)
    units = list(_read(root / "units.json").get("units") or [])
    units.extend(_read(root / "buildings.json").get("buildings") or [])
    upgrades = list(_read(root / "upgrades.json").get("upgrades") or [])
    by_id = {int(row["unit_id"]): row for row in units}
    by_name = {str(row["name"]).upper(): row for row in units}
    upgrades_by_name = {str(row["name"]).upper(): row for row in upgrades}
    return {
        "folder": folder,
        "manifest": manifest,
        "by_id": by_id,
        "by_name": by_name,
        "upgrades_by_name": upgrades_by_name,
    }


def _profile(name: str) -> Dict[str, Any]:
    path = _data_root() / "profiles" / name
    if not path.is_file():
        raise RuntimeError(f"Knowledge profile is missing: {path}")
    return _read(path)


def bound_snapshot() -> Dict[str, Any]:
    """The checked-in snapshot used when no live client ping is available."""
    profile = _profile("windows_retail.json")
    folder = profile.get("snapshot_dir")
    if not folder:
        raise RuntimeError("The Windows retail profile has no extracted snapshot.")
    snapshot = _snapshot(str(folder))
    if profile.get("profile_id"):
        snapshot = dict(snapshot)
        snapshot["profile_id"] = profile["profile_id"]
    return snapshot


def list_manifests() -> List[Dict[str, Any]]:
    root = _data_root() / "snapshots"
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*/manifest.json")):
        found.append(_read(path))
    return found


def require_snapshot_match(*, game_version: str, data_version: str, base_build: int) -> Dict[str, Any]:
    """Refuse a live client that does not match a stored snapshot."""
    for manifest in list_manifests():
        if (
            str(manifest.get("game_version")) == str(game_version)
            and str(manifest.get("data_version")) == str(data_version)
            and int(manifest.get("base_build") or -1) == int(base_build)
            and str(manifest.get("ruleset") or "native") == "native"
        ):
            folder = f"{game_version}_{data_version}"
            return _snapshot(folder)
    raise RuntimeError(
        "Installed client "
        f"{game_version} data {data_version} build {base_build} "
        "has no knowledge snapshot. Formal games stop instead of using another version's numbers."
    )


def record_fields() -> Dict[str, Any]:
    snapshot = bound_snapshot()
    manifest = snapshot["manifest"]
    return {
        "knowledge_profile_id": snapshot.get("profile_id"),
        "knowledge_game_version": manifest.get("game_version"),
        "knowledge_base_build": manifest.get("base_build"),
        "knowledge_data_version": manifest.get("data_version"),
        "knowledge_ruleset": manifest.get("ruleset"),
        "knowledge_schema_version": manifest.get("schema_version"),
        "knowledge_snapshot_hash": manifest.get("snapshot_hash"),
    }


def _forms(race: str, name: str) -> List[Dict[str, Any]]:
    snapshot = bound_snapshot()
    wanted = str(name or "").strip().lower()
    mapping = ((_aliases().get("units") or {}).get(race) or {})
    rows = []
    for proto, canonical in mapping.items():
        if str(canonical) != wanted:
            continue
        row = snapshot["by_name"].get(str(proto).upper())
        if row is not None:
            rows.append(row)
    return rows


def primary_unit(race: str, name: str) -> Optional[Dict[str, Any]]:
    forms = _forms(race, name)
    if not forms:
        return None
    wanted = str(name or "").strip().lower()
    primary_name = (((_aliases().get("primary_forms") or {}).get(race) or {}).get(wanted))
    if primary_name:
        for row in forms:
            if str(row.get("name") or "").upper() == str(primary_name).upper():
                return row
    return forms[0]


def _snake(name: str) -> str:
    import re

    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or "")).replace(" ", "_").lower()


def upgrade_row(race: str, name: str) -> Optional[Dict[str, Any]]:
    wanted = str(name or "").strip().lower()
    snapshot = bound_snapshot()
    for row in snapshot["upgrades_by_name"].values():
        if _snake(row.get("name")) == wanted:
            return row
    mapping = ((_aliases().get("upgrades") or {}).get(race) or {})
    for proto, canonical in mapping.items():
        if str(canonical) == wanted:
            row = snapshot["upgrades_by_name"].get(str(proto).upper())
            if row is not None:
                return row
    return None


def food_provided(race: str, name: str) -> int:
    row = primary_unit(race, name)
    correction = (_platform().get("food_provided_corrections") or {}).get(name)
    if correction is not None:
        return int(correction["value"])
    if row is None or row.get("food_provided") is None:
        raise RuntimeError(f"No client food_provided for {race} {name}")
    return int(row["food_provided"])


def opening_supply_cap(race: str) -> int:
    if race == "zerg":
        return food_provided("zerg", "hatchery") + food_provided("zerg", "overlord")
    if race == "protoss":
        return food_provided("protoss", "nexus")
    return food_provided("terran", "command_center")


def apply_action_numbers(spec: Any, race: str) -> Any:
    """Override minerals, gas, whole-number supply and rounded time from the snapshot."""
    from dataclasses import replace

    if spec.action == "research":
        row = upgrade_row(race, spec.name)
        if row is None:
            return spec
        updated = replace(
            spec,
            minerals=int(row["mineral_cost"]),
            vespene=int(row["vespene_cost"]),
            base_time_seconds=int(round(float(row["research_time_seconds"]))),
        )
        return spec if updated == spec else updated
    if spec.action not in {"train", "build", "upgrade"}:
        return spec
    row = primary_unit(race, spec.name)
    if row is None:
        return spec
    minerals, vespene, supply = _action_cost(spec, race, row)
    corrected = ((_platform().get("action_cost_corrections") or {}).get(race) or {}).get(spec.name) or {}
    if "minerals" in corrected:
        minerals = int(corrected["minerals"])
    if "vespene" in corrected:
        vespene = int(corrected["vespene"])
    if "supply" in corrected:
        supply = int(corrected["supply"])
    seconds = row["build_time_seconds"]
    if "time_seconds" in corrected:
        seconds = corrected["time_seconds"]
    updated = replace(
        spec,
        minerals=minerals,
        vespene=vespene,
        supply=supply,
        base_time_seconds=int(round(float(seconds))),
    )
    return spec if updated == spec else updated


def _action_cost(spec: Any, race: str, row: Mapping[str, Any]) -> tuple:
    """Button cost. Full unit totals are reduced by the morph source."""
    source = _morph_source_row(spec, race)
    if _template_alias(row):
        minerals = int(row["mineral_cost"])
        vespene = int(row["vespene_cost"])
    elif row.get("tech_alias"):
        minerals = int(row["mineral_incremental"])
        vespene = int(row["vespene_incremental"])
    elif source is not None:
        minerals = int(row["mineral_incremental"]) - int(source["mineral_incremental"])
        vespene = int(row["vespene_incremental"]) - int(source["vespene_incremental"])
    else:
        minerals = int(row["mineral_incremental"])
        vespene = int(row["vespene_incremental"])
    supply = spec.supply
    food = row.get("food_required")
    if food is not None and float(food) == int(float(food)):
        if source is not None and source.get("food_required") is not None:
            source_food = float(source["food_required"])
            if source_food == int(source_food):
                supply = int(float(food)) - int(source_food)
        else:
            supply = int(float(food))
    return max(minerals, 0), max(vespene, 0), supply


def _template_alias(row: Mapping[str, Any]) -> bool:
    aliases = row.get("tech_alias") or []
    if not aliases:
        return False
    by_id = bound_snapshot()["by_id"]
    return any(float((by_id.get(int(item)) or {}).get("build_time_seconds") or 0) < 1 for item in aliases)


def _morph_source_row(spec: Any, race: str) -> Optional[Dict[str, Any]]:
    source_name = spec.morph_from
    if not source_name and spec.action == "train":
        source_name = spec.produced_at
    if not source_name:
        return None
    source = primary_unit(race, source_name)
    if source is None or source.get("is_structure"):
        return None
    return source


def _movement(proto: str, is_structure: bool) -> str:
    overlay = _platform()
    name = str(proto or "").upper()
    if name in set(overlay.get("air_proto_names") or []):
        return "air"
    if is_structure:
        return "not_applicable"
    if name in set(overlay.get("ground_proto_names") or []):
        return "ground"
    if "BURROWED" in name:
        return "ground"
    return "unknown"


def _roles(name: str, forms: Sequence[Mapping[str, Any]] = (), race: str = "") -> List[str]:
    found = [
        role for role, names in (_platform().get("roles") or {}).items()
        if name in set(names or [])
    ]
    is_structure = bool(forms) and all(bool(item.get("is_structure")) for item in forms)
    is_unit = bool(forms) and not is_structure
    has_weapon = any(bool(item.get("weapons")) for item in forms)
    if is_unit and has_weapon and "worker" not in found and "combat" not in found:
        found.append("combat")
    if race and not found:
        from sc2bench_env.interface.action_catalog import get_catalog

        targets = list(get_catalog(race=race).targets)
        spec = next((item for item in targets if item.name == name), None)
        if spec is not None and spec.action == "research":
            found.append("tech")
        elif spec is not None and spec.action in {"build", "upgrade"}:
            if has_weapon:
                found.append("defense")
            elif "techlab" in name or name.endswith("reactor"):
                found.append("production")
            elif any(item.action == "train" and item.produced_at == name for item in targets):
                found.append("production")
            elif any(item.action == "research" and item.produced_at == name for item in targets):
                found.append("tech")
            else:
                found.append("tech")
    return found or ["unknown"]


def _observable(name: str, race: str = "") -> Optional[Dict[str, Any]]:
    row = (_platform().get("observable_only") or {}).get(name)
    if not isinstance(row, Mapping):
        return None
    owner = row.get("race")
    if race and owner and owner != race:
        return None
    return dict(row)


def _behavior_text(name: str) -> str:
    raw = (_platform().get("unit_platform_behavior") or {}).get(name)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        return str(raw.get("text") or "")
    return ""


def _runtime_row(unit_id: int) -> Optional[Dict[str, Any]]:
    stats = _runtime_stats()
    if not stats:
        return None
    return (stats.get("units") or {}).get(str(int(unit_id)))


@lru_cache(maxsize=1)
def _runtime_stats() -> Optional[Dict[str, Any]]:
    snapshot = bound_snapshot()
    path = _data_root() / "snapshots" / snapshot["folder"] / "runtime_stats.json"
    if not path.is_file():
        return None
    payload = _read(path)
    manifest = snapshot["manifest"]
    if (
        str(payload.get("game_version")) != str(manifest.get("game_version"))
        or str(payload.get("data_version")) != str(manifest.get("data_version"))
        or int(payload.get("base_build") or -1) != int(manifest.get("base_build") or -2)
    ):
        raise RuntimeError(
            "Runtime stat snapshot does not match the bound client snapshot. "
            "Refusing to mix health, shields or energy from another version."
        )
    return payload


def _weapon_text(weapon: Mapping[str, Any]) -> str:
    speed = float(weapon.get("speed") or 0)
    damage = float(weapon.get("damage") or 0)
    attacks = int(weapon.get("attacks") or 0)
    dps = "unknown" if speed <= 0 else round(damage * attacks / speed, 2)
    bonuses = weapon.get("bonuses") or []
    bonus_text = ", ".join(
        f"+{item.get('bonus')} vs {item.get('attribute')}" for item in bonuses
    ) or "none"
    return (
        f"targets {weapon.get('targets')}, damage {weapon.get('damage')}, "
        f"attacks {weapon.get('attacks')}, range {weapon.get('range')}, "
        f"period {weapon.get('speed')}, bonus {bonus_text}, "
        f"base_dps {dps} (no armor, upgrades, splash or spells)"
    )


def _can_attack(weapons: Sequence[Mapping[str, Any]]) -> str:
    targets = {str(item.get("targets")) for item in weapons}
    ground = "Ground" in targets or "Any" in targets
    air = "Air" in targets or "Any" in targets
    if ground and air:
        return "ground_and_air"
    if ground:
        return "ground"
    if air:
        return "air"
    return "none"


def _form_text(row: Mapping[str, Any]) -> Dict[str, Any]:
    weapons = list(row.get("weapons") or [])
    return {
        "proto_name": row.get("name"),
        "unit_id": row.get("unit_id"),
        "movement_layer": _movement(str(row.get("name") or ""), bool(row.get("is_structure"))),
        "can_attack": _can_attack(weapons),
        "weapons": [_weapon_text(item) for item in weapons] or ["none"],
        "armor": row.get("armor") if row.get("armor") is not None else "unknown",
        "movement_speed": row.get("movement_speed") if row.get("movement_speed") is not None else "unknown",
        "sight_range": row.get("sight_range") if row.get("sight_range") is not None else "unknown",
        "attributes": list(row.get("attributes") or []),
    }


def _vital(value: Any) -> Any:
    if value is None:
        return "unknown"
    if float(value) == 0:
        return "none"
    return value


def attach(payload: Dict[str, Any], race: str, name: str) -> Dict[str, Any]:
    """Add client combat fields. Missing protobuf fields stay unknown until runtime stats exist."""
    row = primary_unit(race, name)
    forms = _forms(race, name)
    observed = _observable(name, race)
    behavior = _behavior_text(name)
    payload["availability"] = "observable_only" if observed else "controllable"
    payload["roles"] = _roles(name, forms, race)
    runtime = _runtime_row(int(row["unit_id"])) if row is not None else None
    payload["health"] = "unknown" if runtime is None else _vital(runtime.get("health_max"))
    payload["shields"] = "unknown" if runtime is None else _vital(runtime.get("shield_max"))
    payload["energy_max"] = "unknown" if runtime is None else _vital(runtime.get("energy_max"))
    payload["platform_behavior"] = (observed or {}).get("platform_behavior") or behavior or "not_applicable"
    if row is None:
        payload["forms"] = []
        payload["client_food_required"] = "unknown"
        return payload
    payload["client_food_required"] = row.get("food_required")
    payload["food_provided"] = food_provided(race, name)
    payload["client_time_seconds"] = row.get("build_time_seconds")
    payload["sight_range"] = row.get("sight_range")
    payload["armor"] = row.get("armor")
    payload["movement_speed"] = "not_applicable" if row.get("is_structure") else row.get("movement_speed")
    payload["attributes"] = list(row.get("attributes") or [])
    payload["forms"] = [_form_text(item) for item in forms]
    attacks = {_can_attack(list(item.get("weapons") or [])) for item in forms}
    payload["can_attack"] = next(iter(attacks)) if len(attacks) == 1 else "varies_by_form"
    if behavior and len(payload["forms"]) > 1:
        payload["form_note"] = behavior
    return payload


def names_for(race: str, kind: str) -> List[str]:
    """Canonical names a knowledge query of this kind can resolve."""
    from sc2bench_env.interface.action_catalog import get_catalog

    names = []
    for spec in get_catalog(race=race).targets:
        if kind == "unit" and spec.action == "train":
            names.append(spec.name)
        elif kind == "building" and spec.action in {"build", "upgrade"}:
            names.append(spec.name)
        elif kind == "research" and spec.action == "research":
            names.append(spec.name)
    for entry, row in (_platform().get("observable_only") or {}).items():
        if row.get("kind") == kind and row.get("race") == race:
            names.append(entry)
    return names


def close_names(race: str, query: str, kind: str) -> List[str]:
    pool = names_for(race, kind)
    return difflib.get_close_matches(str(query or "").strip().lower(), pool, n=5, cutoff=0.5)


def observable_groups(race: str) -> Dict[str, List[str]]:
    from sc2bench_env.interface.action_catalog import get_catalog

    controllable = {spec.name for spec in get_catalog(race=race).targets}
    mapping = ((_aliases().get("units") or {}).get(race) or {})
    units, buildings = [], []
    for canonical in sorted(set(mapping.values())):
        if canonical in controllable:
            continue
        row = _observable(canonical, race) or {}
        kind = row.get("kind")
        if kind is None:
            forms = _forms(race, canonical)
            kind = "building" if forms and all(item.get("is_structure") for item in forms) else "unit"
        if kind == "building":
            buildings.append(canonical)
        else:
            units.append(canonical)
    return {"units": units, "buildings": buildings}


def observable_payload(race: str, name: str, kind: str) -> Optional[Dict[str, Any]]:
    """A query hit for an observation alias that is not a production target."""
    canonical = str(name or "").strip().lower()
    forms = _forms(race, canonical)
    observed = _observable(canonical, race)
    listed = (_platform().get("observable_only") or {}).get(canonical)
    if isinstance(listed, Mapping) and listed.get("race") not in (None, "", race):
        return None
    if observed is None and not forms:
        return None
    from sc2bench_env.interface.action_catalog import get_target

    if get_target(canonical, race=race) is not None:
        return None
    resolved_kind = (observed or {}).get("kind")
    if resolved_kind is None:
        resolved_kind = "building" if forms and all(item.get("is_structure") for item in forms) else "unit"
    if resolved_kind != kind:
        return {"name": canonical, "error": f"not_a_{kind}_target"}
    row = primary_unit(race, canonical)
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

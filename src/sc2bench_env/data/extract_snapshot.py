"""Dump ResponsePing and RequestData into data/sc2/snapshots.

Run on the machine whose client should become a profile. This does not invent
numbers for a client that is not installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping

FRAMES_PER_SECOND = 22.4


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_root() -> Path:
    return _repo_root() / "data" / "sc2"


def _dump(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return str(value)


def _enum_name(enum_type: Any, number: int) -> str:
    try:
        return str(enum_type.Name(int(number)))
    except Exception:
        return str(number)


def _id_list(value: Any) -> List[int]:
    if isinstance(value, int):
        return [] if value == 0 else [int(value)]
    return [int(item) for item in value or []]


def _seconds(frames: float) -> float:
    return round(float(frames) / FRAMES_PER_SECOND, 4)
    return round(float(frames) / FRAMES_PER_SECOND, 4)


def _zerg_minerals(unit: Any, attribute_enum: Any) -> int:
    minerals = int(getattr(unit, "mineral_cost", 0) or 0)
    race = str(getattr(unit, "race", 0))
    # common_pb2.Race.Zerg is 2. Compare the enum name when the field is set.
    race_name = ""
    try:
        from s2clientprotocol import common_pb2

        race_name = common_pb2.Race.Name(int(unit.race))
    except Exception:
        race_name = str(race)
    attributes = [_enum_name(attribute_enum, item) for item in getattr(unit, "attributes", [])]
    if race_name == "Zerg" and "Structure" in attributes and minerals >= 50:
        minerals -= 50
    return minerals


def _incremental(unit: Any, by_id: Mapping[int, Any], attribute_enum: Any) -> tuple[int, int]:
    minerals = _zerg_minerals(unit, attribute_enum)
    vespene = int(getattr(unit, "vespene_cost", 0) or 0)
    aliases = [int(item) for item in getattr(unit, "tech_alias", [])]
    if not aliases:
        return minerals, vespene
    alias_rows = [by_id[item] for item in aliases if item in by_id]
    if not alias_rows:
        return minerals, vespene
    minerals -= max(_zerg_minerals(item, attribute_enum) for item in alias_rows)
    vespene -= max(int(getattr(item, "vespene_cost", 0) or 0) for item in alias_rows)
    return max(minerals, 0), max(vespene, 0)


def _weapon(weapon: Any, attribute_enum: Any, weapon_enum: Any) -> Dict[str, Any]:
    bonuses = []
    for bonus in getattr(weapon, "damage_bonus", []):
        bonuses.append({
            "attribute": _enum_name(attribute_enum, getattr(bonus, "attribute", 0)),
            "bonus": getattr(bonus, "bonus", 0),
        })
    return {
        "targets": _enum_name(weapon_enum, getattr(weapon, "type", 0)),
        "damage": getattr(weapon, "damage", 0),
        "attacks": int(getattr(weapon, "attacks", 0) or 0),
        "range": getattr(weapon, "range", 0),
        "speed": getattr(weapon, "speed", 0),
        "bonuses": bonuses,
    }


def _unit_row(unit: Any, by_id: Mapping[int, Any], data_pb: Any) -> Dict[str, Any]:
    attribute_enum = data_pb.Attribute
    attributes = [_enum_name(attribute_enum, item) for item in getattr(unit, "attributes", [])]
    minerals, vespene = _incremental(unit, by_id, attribute_enum)
    try:
        from s2clientprotocol import common_pb2

        race = common_pb2.Race.Name(int(unit.race))
    except Exception:
        race = "unknown"
    return {
        "unit_id": int(unit.unit_id),
        "name": str(getattr(unit, "name", "") or ""),
        "available": bool(getattr(unit, "available", False)),
        "race": race,
        "mineral_cost": int(getattr(unit, "mineral_cost", 0) or 0),
        "vespene_cost": int(getattr(unit, "vespene_cost", 0) or 0),
        "mineral_incremental": minerals,
        "vespene_incremental": vespene,
        "food_required": getattr(unit, "food_required", None),
        "food_provided": getattr(unit, "food_provided", None),
        "build_time_seconds": _seconds(getattr(unit, "build_time", 0) or 0),
        "sight_range": getattr(unit, "sight_range", None),
        "armor": getattr(unit, "armor", None),
        "movement_speed": getattr(unit, "movement_speed", None),
        "cargo_size": int(getattr(unit, "cargo_size", 0) or 0),
        "attributes": attributes,
        "is_structure": "Structure" in attributes,
        "tech_alias": _id_list(getattr(unit, "tech_alias", [])),
        "unit_alias": _id_list(getattr(unit, "unit_alias", 0)),
        "tech_requirement": _id_list(getattr(unit, "tech_requirement", [])),
        "weapons": [
            _weapon(item, attribute_enum, data_pb.Weapon.TargetType)
            for item in getattr(unit, "weapons", [])
        ],
    }


def _upgrade_row(upgrade: Any) -> Dict[str, Any]:
    return {
        "upgrade_id": int(upgrade.upgrade_id),
        "name": str(getattr(upgrade, "name", "") or ""),
        "available": bool(getattr(upgrade, "available", False)),
        "mineral_cost": int(getattr(upgrade, "mineral_cost", 0) or 0),
        "vespene_cost": int(getattr(upgrade, "vespene_cost", 0) or 0),
        "research_time_seconds": _seconds(getattr(upgrade, "research_time", 0) or 0),
        "ability_id": int(getattr(upgrade, "ability_id", 0) or 0),
    }


def _ability_row(ability: Any) -> Dict[str, Any]:
    return {
        "ability_id": int(ability.ability_id),
        "link_name": str(getattr(ability, "link_name", "") or ""),
        "button_name": str(getattr(ability, "button_name", "") or ""),
        "friendly_name": str(getattr(ability, "friendly_name", "") or ""),
        "available": bool(getattr(ability, "available", False)),
        "target": str(getattr(ability, "target", "") or ""),
        "is_building": bool(getattr(ability, "is_building", False)),
        "remaps_to_ability_id": int(getattr(ability, "remaps_to_ability_id", 0) or 0),
    }


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _hash_files(paths: List[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _aliases() -> Dict[str, Any]:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.ids.upgrade_id import UpgradeId

    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter
    from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
    from sc2bench_env.backends.sharpy.races.zerg import ZergAdapter

    _ensure_runtime_paths()
    adapters = {
        "terran": TerranAdapter(),
        "protoss": ProtossAdapter(),
        "zerg": ZergAdapter(),
    }
    units: Dict[str, Dict[str, str]] = {}
    upgrades: Dict[str, Dict[str, str]] = {}
    for race, adapter in adapters.items():
        unit_map: Dict[str, str] = {}
        for type_id in UnitTypeId:
            canonical = adapter.normalize_unit_name(type_id.name)
            if canonical:
                unit_map[type_id.name] = canonical
        units[race] = unit_map
        upgrade_map: Dict[str, str] = {}
        for upgrade_id in UpgradeId:
            canonical = adapter.normalize_upgrade_name(upgrade_id.name)
            if canonical:
                upgrade_map[upgrade_id.name] = canonical
        upgrades[race] = upgrade_map
    previous = _data_root() / "common" / "aliases.json"
    kept = {}
    if previous.is_file():
        kept = json.loads(previous.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "reason": "Observation names mapped onto canonical knowledge names. Not client costs.",
        "units": units,
        "upgrades": upgrades,
        "primary_forms": kept.get("primary_forms") or {},
    }


async def _dump_client() -> Dict[str, Any]:
    from s2clientprotocol import data_pb2 as data_pb
    from s2clientprotocol import sc2api_pb2 as sc_pb
    from sc2.sc2process import SC2Process

    from sc2 import maps
    from sc2.bot_ai import BotAI
    from sc2.client import Client
    from sc2.data import Difficulty, Race
    from sc2.player import Bot, Computer

    class _Idle(BotAI):
        async def on_step(self, iteration: int) -> None:
            return None

    async with SC2Process(fullscreen=False, resolution=(640, 480)) as controller:
        ping = (await controller.ping()).ping
        created = await controller.create_game(
            maps.get("KairosJunctionLE"),
            [Bot(Race.Terran, _Idle()), Computer(Race.Zerg, Difficulty.VeryEasy)],
            realtime=False,
        )
        if created.HasField("create_game") and created.create_game.HasField("error"):
            details = created.create_game.error_details if created.create_game.HasField("error_details") else ""
            raise RuntimeError(f"Could not create the data game: {created.create_game.error} {details}")
        client = Client(controller._ws)
        await client.join_game(race=Race.Terran)
        result = await client._execute(
            data=sc_pb.RequestData(
                ability_id=True,
                unit_type_id=True,
                upgrade_id=True,
                buff_id=True,
                effect_id=True,
            )
        )
        await client.leave()
        if not result.HasField("data"):
            raise RuntimeError(f"RequestData was rejected: {result.error or result.status}")
        by_id = {int(unit.unit_id): unit for unit in result.data.units}
        rows = [_unit_row(unit, by_id, data_pb) for unit in result.data.units]
        return {
            "game_version": str(ping.game_version),
            "data_version": str(ping.data_version),
            "base_build": int(ping.base_build),
            "data_build": int(ping.data_build),
            "units": rows,
            "upgrades": [_upgrade_row(item) for item in result.data.upgrades],
            "abilities": [_ability_row(item) for item in result.data.abilities],
        }


def main() -> None:
    os.environ.setdefault("SC2PATH", r"D:\StarCraft II")
    payload = asyncio.run(_dump_client())
    folder = _data_root() / "snapshots" / f"{payload['game_version']}_{payload['data_version']}"
    folder.mkdir(parents=True, exist_ok=True)
    units = [row for row in payload["units"] if not row["is_structure"]]
    buildings = [row for row in payload["units"] if row["is_structure"]]
    _write(folder / "units.json", {"units": units})
    _write(folder / "buildings.json", {"buildings": buildings})
    _write(folder / "upgrades.json", {"upgrades": payload["upgrades"]})
    _write(folder / "abilities.json", {"abilities": payload["abilities"]})
    _write(folder / "version_overrides.json", {
        "schema_version": 1,
        "ruleset": "native",
        "overrides": [],
        "reason": "No ruleset override is applied on top of this client dump.",
    })
    hashed = _hash_files([
        folder / "abilities.json",
        folder / "buildings.json",
        folder / "units.json",
        folder / "upgrades.json",
        folder / "version_overrides.json",
    ])
    manifest = {
        "schema_version": 1,
        "game_version": payload["game_version"],
        "data_version": payload["data_version"],
        "base_build": payload["base_build"],
        "data_build": payload["data_build"],
        "ruleset": "native",
        "frames_per_second": FRAMES_PER_SECOND,
        "snapshot_hash": hashed,
        "unit_count": len(units),
        "building_count": len(buildings),
        "upgrade_count": len(payload["upgrades"]),
        "missing_proto_fields": [
            "life",
            "shields",
            "energy",
            "weapon_name",
            "minimum_range",
            "target_restrictions",
            "movement_layer",
        ],
    }
    _write(folder / "manifest.json", manifest)
    profiles = _data_root() / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    _write(profiles / "windows_retail.json", {
        "profile_id": "windows-retail-" + ".".join(payload["game_version"].split(".")[:3]),
        "ruleset": "native",
        "snapshot_dir": folder.name,
        "reason": "Profile alias for the Windows retail client extracted on this machine.",
    })
    common = _data_root() / "common"
    common.mkdir(parents=True, exist_ok=True)
    _write(common / "aliases.json", {
        "schema_version": 1,
        "reason": "Observation names mapped onto canonical knowledge names. Not client costs.",
        "units": {"terran": {}, "protoss": {}, "zerg": {}},
        "upgrades": {"terran": {}, "protoss": {}, "zerg": {}},
    })
    _write(common / "aliases.json", _aliases())
    print(json.dumps({
        "snapshot": str(folder),
        "game_version": payload["game_version"],
        "data_version": payload["data_version"],
        "base_build": payload["base_build"],
        "hash": hashed,
        "units": len(units),
        "buildings": len(buildings),
    }, indent=2))


if __name__ == "__main__":
    main()

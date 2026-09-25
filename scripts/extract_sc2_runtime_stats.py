"""Read health, shields, energy and flying state from one live client.

This starts its own StarCraft II process and closes only that process.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _extract() -> dict:
    from s2clientprotocol import common_pb2 as common_pb
    from s2clientprotocol import debug_pb2 as debug_pb
    from s2clientprotocol import sc2api_pb2 as sc_pb
    from sc2 import maps
    from sc2.bot_ai import BotAI
    from sc2.client import Client
    from sc2.data import Difficulty, Race
    from sc2.player import Bot, Computer
    from sc2.sc2process import SC2Process

    aliases = json.loads((ROOT / "data" / "sc2" / "common" / "aliases.json").read_text(encoding="utf-8"))
    profile = json.loads((ROOT / "data" / "sc2" / "profiles" / "windows_retail.json").read_text(encoding="utf-8"))
    folder = ROOT / "data" / "sc2" / "snapshots" / profile["snapshot_dir"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rows = json.loads((folder / "units.json").read_text(encoding="utf-8"))["units"]
    rows += json.loads((folder / "buildings.json").read_text(encoding="utf-8"))["buildings"]
    by_name = {str(row["name"]).upper(): row for row in rows}
    wanted = []
    seen = set()
    for race_map in (aliases.get("units") or {}).values():
        for proto in race_map:
            row = by_name.get(str(proto).upper())
            if row is None or int(row["unit_id"]) in seen:
                continue
            seen.add(int(row["unit_id"]))
            wanted.append(row)

    class _Idle(BotAI):
        async def on_step(self, iteration: int) -> None:
            return None

    async with SC2Process(fullscreen=False, resolution=(640, 480)) as controller:
        ping = (await controller.ping()).ping
        if (
            str(ping.game_version) != str(manifest["game_version"])
            or str(ping.data_version) != str(manifest["data_version"])
            or int(ping.base_build) != int(manifest["base_build"])
        ):
            raise RuntimeError("Live client does not match the bound knowledge snapshot.")
        created = await controller.create_game(
            maps.get("KairosJunctionLE"),
            [Bot(Race.Terran, _Idle()), Computer(Race.Zerg, Difficulty.VeryEasy)],
            realtime=False,
        )
        if created.HasField("create_game") and created.create_game.HasField("error"):
            raise RuntimeError(f"Could not create the stat game: {created.create_game.error}")
        client = Client(controller._ws)
        await client.join_game(race=Race.Terran)
        info = await client._execute(game_info=sc_pb.RequestGameInfo())
        area = info.game_info.start_raw.playable_area
        origin_x = area.p0.x + 8
        origin_y = area.p0.y + 8
        center_x = (area.p0.x + area.p1.x) / 2
        center_y = (area.p0.y + area.p1.y) / 2
        by_id = {int(row["unit_id"]): row["name"] for row in rows}
        before = await client._execute(observation=sc_pb.RequestObservation())
        existing = {int(unit.tag) for unit in before.observation.observation.raw_data.units}
        found = {}
        failure = ""

        async def _spawn(commands, expected_ids):
            await client._execute(debug=sc_pb.RequestDebug(debug=commands))
            await client.step(4)
            observed = await client._execute(observation=sc_pb.RequestObservation())
            fresh = []
            spawned = set()
            for unit in observed.observation.observation.raw_data.units:
                tag = int(unit.tag)
                if tag in existing:
                    continue
                type_id = int(unit.unit_type)
                fresh.append(tag)
                existing.add(tag)
                if type_id not in expected_ids or type_id in found:
                    continue
                spawned.add(type_id)
                found[type_id] = {
                    "proto_name": by_id.get(type_id, str(type_id)),
                    "health_max": unit.health_max,
                    "shield_max": unit.shield_max,
                    "energy_max": unit.energy_max,
                    "is_flying": bool(unit.is_flying),
                }
            if fresh:
                await client._execute(debug=sc_pb.RequestDebug(debug=[
                    debug_pb.DebugCommand(kill_unit=debug_pb.DebugKillUnit(tag=fresh))
                ]))
                await client.step(2)
            return spawned

        try:
            for offset in range(0, len(wanted), 8):
                batch = wanted[offset:offset + 8]
                commands = []
                for index, row in enumerate(batch):
                    pos = common_pb.Point2D(
                        x=origin_x + (index % 4) * 4,
                        y=origin_y + ((offset // 8) % 8) * 4,
                    )
                    commands.append(debug_pb.DebugCommand(create_unit=debug_pb.DebugCreateUnit(
                        unit_type=int(row["unit_id"]), owner=1, pos=pos, quantity=1,
                    )))
                await _spawn(commands, {int(row["unit_id"]) for row in batch})
            missing = [row for row in wanted if int(row["unit_id"]) not in found]
            for index, row in enumerate(missing):
                pos = common_pb.Point2D(
                    x=center_x - 10 + (index % 5) * 5,
                    y=center_y - 10 + (index // 5) * 5,
                )
                try:
                    await _spawn([debug_pb.DebugCommand(create_unit=debug_pb.DebugCreateUnit(
                        unit_type=int(row["unit_id"]), owner=1, pos=pos, quantity=1,
                    ))], {int(row["unit_id"])})
                except Exception as exc:
                    failure = str(exc)
                    break
        except Exception as exc:
            failure = str(exc)
        try:
            await client.leave()
        except Exception:
            pass
    unobserved = [
        {"proto_name": row["name"], "unit_id": int(row["unit_id"]), "reason": "Debug create produced no unit."}
        for row in wanted if int(row["unit_id"]) not in found
    ]
    payload = {
        "game_version": str(ping.game_version),
        "data_version": str(ping.data_version),
        "base_build": int(ping.base_build),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extractor": "scripts/extract_sc2_runtime_stats.py",
        "extractor_version": 1,
        "units": {str(type_id): row for type_id, row in sorted(found.items())},
        "unobserved": unobserved,
    }
    if failure:
        payload["stopped_early"] = failure
    return payload


async def _fill_missing(existing: dict) -> dict:
    """Read units that were already on the map, then retry the ones still missing."""
    from s2clientprotocol import common_pb2 as common_pb
    from s2clientprotocol import debug_pb2 as debug_pb
    from s2clientprotocol import sc2api_pb2 as sc_pb
    from sc2 import maps
    from sc2.bot_ai import BotAI
    from sc2.client import Client
    from sc2.data import Difficulty, Race
    from sc2.player import Bot, Computer
    from sc2.sc2process import SC2Process

    profile = json.loads((ROOT / "data" / "sc2" / "profiles" / "windows_retail.json").read_text(encoding="utf-8"))
    folder = ROOT / "data" / "sc2" / "snapshots" / profile["snapshot_dir"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rows = json.loads((folder / "units.json").read_text(encoding="utf-8"))["units"]
    rows += json.loads((folder / "buildings.json").read_text(encoding="utf-8"))["buildings"]
    by_id = {int(row["unit_id"]): row["name"] for row in rows}
    missing_ids = {int(item["unit_id"]) for item in existing.get("unobserved") or []}
    found = {int(key): value for key, value in (existing.get("units") or {}).items()}

    class _Idle(BotAI):
        async def on_step(self, iteration: int) -> None:
            return None

    notes = []
    async with SC2Process(fullscreen=False, resolution=(640, 480)) as controller:
        ping = (await controller.ping()).ping
        if str(ping.game_version) != str(manifest["game_version"]) or int(ping.base_build) != int(manifest["base_build"]):
            raise RuntimeError("Live client does not match the bound knowledge snapshot.")
        await controller.create_game(
            maps.get("KairosJunctionLE"),
            [Bot(Race.Terran, _Idle()), Computer(Race.Zerg, Difficulty.VeryEasy)],
            realtime=False,
        )
        client = Client(controller._ws)
        await client.join_game(race=Race.Terran)
        info = await client._execute(game_info=sc_pb.RequestGameInfo())
        area = info.game_info.start_raw.playable_area
        center_x = (area.p0.x + area.p1.x) / 2
        center_y = (area.p0.y + area.p1.y) / 2
        observed = await client._execute(observation=sc_pb.RequestObservation())
        for unit in observed.observation.observation.raw_data.units:
            type_id = int(unit.unit_type)
            if type_id in missing_ids and type_id not in found:
                found[type_id] = {
                    "proto_name": by_id.get(type_id, str(type_id)),
                    "health_max": unit.health_max,
                    "shield_max": unit.shield_max,
                    "energy_max": unit.energy_max,
                    "is_flying": bool(unit.is_flying),
                }
        still = [type_id for type_id in sorted(missing_ids) if type_id not in found]
        for index, type_id in enumerate(still):
            pos = common_pb.Point2D(x=center_x - 8 + (index % 4) * 4, y=center_y)
            name = by_id.get(type_id, "")
            commands = []
            if name.endswith(("TechLab", "Reactor")):
                host_id = 27 if name.startswith("Factory") else 28 if name.startswith("Starport") else 21
                commands.append(debug_pb.DebugCommand(create_unit=debug_pb.DebugCreateUnit(
                    unit_type=host_id, owner=1, pos=pos, quantity=1,
                )))
                pos = common_pb.Point2D(x=pos.x + 2.5, y=pos.y - 0.5)
            commands.append(debug_pb.DebugCommand(create_unit=debug_pb.DebugCreateUnit(
                unit_type=type_id, owner=1, pos=pos, quantity=1,
            )))
            before = {int(unit.tag) for unit in (await client._execute(observation=sc_pb.RequestObservation())).observation.observation.raw_data.units}
            try:
                await client._execute(debug=sc_pb.RequestDebug(debug=commands))
                await client.step(8)
                after = await client._execute(observation=sc_pb.RequestObservation())
            except Exception as exc:
                notes.append({"unit_id": type_id, "proto_name": by_id.get(type_id, str(type_id)), "reason": str(exc)})
                break
            spawned = []
            for unit in after.observation.observation.raw_data.units:
                if int(unit.tag) in before:
                    continue
                spawned.append(int(unit.unit_type))
                if int(unit.unit_type) == type_id:
                    found[type_id] = {
                        "proto_name": by_id.get(type_id, str(type_id)),
                        "health_max": unit.health_max,
                        "shield_max": unit.shield_max,
                        "energy_max": unit.energy_max,
                        "is_flying": bool(unit.is_flying),
                    }
            if type_id not in found:
                names = [by_id.get(item, str(item)) for item in spawned]
                notes.append({
                    "unit_id": type_id,
                    "proto_name": by_id.get(type_id, str(type_id)),
                    "reason": "Debug create produced " + (", ".join(names) if names else "no unit") + ".",
                })
        try:
            await client.leave()
        except Exception:
            pass
    unobserved = [item for item in notes if item["unit_id"] not in found]
    unobserved += [
        item for item in (existing.get("unobserved") or [])
        if int(item["unit_id"]) not in found and int(item["unit_id"]) not in {item["unit_id"] for item in unobserved}
    ]
    return {
        **existing,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "units": {str(type_id): row for type_id, row in sorted(found.items())},
        "unobserved": unobserved,
    }


def main() -> None:
    os.environ.setdefault("SC2PATH", r"D:\StarCraft II")
    profile = json.loads((ROOT / "data" / "sc2" / "profiles" / "windows_retail.json").read_text(encoding="utf-8"))
    folder = ROOT / "data" / "sc2" / "snapshots" / profile["snapshot_dir"]
    target = folder / "runtime_stats.json"
    if "--fill-missing" in sys.argv and target.is_file():
        payload = asyncio.run(_fill_missing(json.loads(target.read_text(encoding="utf-8"))))
    else:
        payload = asyncio.run(_extract())
    profile = json.loads((ROOT / "data" / "sc2" / "profiles" / "windows_retail.json").read_text(encoding="utf-8"))
    folder = ROOT / "data" / "sc2" / "snapshots" / profile["snapshot_dir"]
    target = folder / "runtime_stats.json"
    _write(target, payload)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    _write(folder / "runtime_stats_manifest.json", {
        "schema_version": 1,
        "game_version": payload["game_version"],
        "data_version": payload["data_version"],
        "base_build": payload["base_build"],
        "runtime_stats_hash": digest,
        "unit_count": len(payload["units"]),
        "unobserved_count": len(payload["unobserved"]),
    })
    print(json.dumps({
        "units": len(payload["units"]),
        "unobserved": len(payload["unobserved"]),
        "stopped_early": payload.get("stopped_early", ""),
        "hash": digest,
    }, indent=2))


if __name__ == "__main__":
    main()

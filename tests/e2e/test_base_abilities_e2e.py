"""Real client checks for base energy casts and the combat casts that were only unit-tested.

Debug cheats grant resources, energy, and research. They do not cast Chrono,
Inject, creep tumors, or combat abilities. Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations


from tests.helpers.drive import drive
import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 2.0) -> dict:
    return {"name": "advance", "arguments": {"seconds": seconds}}


def _install(monkeypatch, collect):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

    _ensure_runtime_paths()
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    import sc2bench_env.backends.sharpy.races as races

    class Watch(ActBase):
        async def execute(self):
            await collect(self)
            return True

    original = races.get_adapter

    def wrapped(race):
        adapter = original(race)
        create = adapter.create_tactics

        def create_tactics():
            return BuildOrder([Watch(), create()])

        adapter.create_tactics = create_tactics
        return adapter

    monkeypatch.setattr(races, "get_adapter", wrapped)


def _has_buff(unit, buff) -> bool:
    return buff in (getattr(unit, "buffs", ()) or ())


def _order_text(unit) -> str:
    parts = []
    for order in getattr(unit, "orders", ()) or ():
        ability = getattr(order, "ability", None)
        parts.append(str(getattr(ability, "id", ability)))
    return " ".join(parts)


def _note(state, token: str) -> None:
    state["seen"].add(token)


def _mark_units(act, state, type_id, *tokens) -> None:
    for unit in act.ai.units(type_id):
        text = _order_text(unit)
        for token in tokens:
            if token in text:
                _note(state, token)
        for buff in getattr(unit, "buffs", ()) or ():
            _note(state, str(getattr(buff, "name", buff)))


async def _spawn_once(act, state, rows) -> None:
    if state.get("spawned"):
        return
    await act.ai.client.debug_create_unit(rows)
    state["spawned"] = True


def _home(act):
    gather = getattr(act.ai, "bench_home_gather", None)
    if gather is not None:
        return gather.home_point()
    return act.ai.start_location


def _foe(act) -> int:
    return 1 if getattr(act.ai, "player_id", 1) == 2 else 2


def _spot(act, origin, distance: float):
    """A pathable point about `distance` from origin, not the first nearby cell."""
    import math

    from sc2.position import Point2

    pathable = getattr(act.ai, "in_pathing_grid", None)
    candidates = []
    for angle in range(0, 360, 30):
        rad = math.radians(angle)
        candidates.append(origin + Point2((math.cos(rad) * distance, math.sin(rad) * distance)))
    goals = [
        act.ai.enemy_start_locations[0],
        act.ai.game_info.map_center,
        act.ai.start_location,
    ]
    for goal in goals:
        candidates.append(origin.towards(goal, distance))
    best = None
    best_error = 999.0
    for point in candidates:
        if pathable is not None and not pathable(point):
            continue
        gap = abs(point.distance_to(origin) - distance)
        if gap < best_error:
            best = point
            best_error = gap
    return best if best is not None else origin


async def _place(act, kind, origin):
    from sc2.position import Point2

    for dx, dy in (
        (5, 0), (-5, 0), (0, 5), (0, -5), (6, 3), (-6, 3),
        (4, -4), (-4, 4), (8, 1), (0, 8), (7, -3), (-3, 7),
    ):
        point = origin + Point2((dx, dy))
        try:
            if await act.ai.can_place(kind, point):
                return point
        except Exception:
            break
    return origin


def _far_zone(obs) -> str:
    """Enemy main, so a retreat actually has somewhere to come back from."""
    rows = list(getattr(obs, "zone_state", None) or [])
    for role in ("enemy_main", "enemy_natural"):
        for row in rows:
            if row.get("zone_role") == role and row.get("zone_id"):
                return row["zone_id"]
    own = {
        row.get("zone_id")
        for row in rows
        if row.get("zone_role") in {"own_main", "own_natural"}
    }
    for row in reversed(rows):
        zone_id = row.get("zone_id")
        if zone_id and zone_id not in own:
            return zone_id
    return (obs.zones or ["zone_0"])[-1]


def _keep(name: str, state) -> None:
    from pathlib import Path

    Path(__file__).with_name(f"_{name}_diag.txt").write_text(repr(state.get("diag")), encoding="utf-8")


def _ability_names(act, unit) -> list:
    ready = act.cd_manager.available_dict.get(unit.tag, []) if hasattr(act, "cd_manager") else []
    return [getattr(item, "name", str(item)) for item in ready]


@pytest.mark.parametrize("race,action,evidence", [
    ("protoss", "chrono_boost", "CHRONOBOOSTENERGYCOST"),
    ("zerg", "inject_larva", "QUEENSPAWNLARVATIMER"),
    ("zerg", "spawn_creep_tumor", "CREEPTUMOR"),
])
def test_e2e_base_energy_cast(race, action, evidence, monkeypatch) -> None:
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "spawned": False}

    async def collect(act):
        if race == "zerg":
            hatch = act.ai.townhalls.ready
            if hatch and not state["spawned"]:
                await act.ai.client.debug_create_unit(
                    [[UnitTypeId.QUEEN, 2, hatch.first.position, act.ai.player_id]]
                )
                state["spawned"] = True
            queens = list(act.ai.units(UnitTypeId.QUEEN))
            if queens:
                await act.ai.client.debug_set_unit_value([queen.tag for queen in queens], 1, 80)
        for structure in act.ai.structures:
            if _has_buff(structure, BuffId.CHRONOBOOSTENERGYCOST):
                _note(state, "CHRONOBOOSTENERGYCOST")
            if _has_buff(structure, BuffId.QUEENSPAWNLARVATIMER):
                _note(state, "QUEENSPAWNLARVATIMER")
        for kind in (UnitTypeId.CREEPTUMORQUEEN, UnitTypeId.CREEPTUMOR, UnitTypeId.CREEPTUMORBURROWED):
            if act.ai.structures(kind).exists:
                _note(state, "CREEPTUMOR")

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race=race, enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        assert_obs_consistent(obs)
        if race == "zerg":
            drive(env, [_wait(3)])
        _, feedback, terminated, _ = drive(env, [
            {"name": action, "arguments": {}},
            _wait(6),
        ])
        assert not terminated
        assert feedback.receipts[0].result == "accepted", feedback.receipts[0]
        for _ in range(6):
            if evidence in state["seen"]:
                break
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
        demands = [
            (demand.action, str(demand.state), demand.waiting_for, demand.failure_reason)
            for demand in env.task_manager.demands.values()
        ]
        assert evidence in state["seen"], (sorted(state["seen"]), demands)
    finally:
        env.close()


def test_e2e_terran_hellion_and_medivac_boost(monkeypatch) -> None:
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "enemies": False, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            armory_at = await _place(act, UnitTypeId.ARMORY, act.ai.start_location)
            await act.ai.client.debug_create_unit([
                [UnitTypeId.ARMORY, 1, armory_at, act.ai.player_id],
                [UnitTypeId.HELLION, 1, home, act.ai.player_id],
                [UnitTypeId.MEDIVAC, 1, home, act.ai.player_id],
            ])
            state["own"] = True
            state["diag"]["armory_at"] = (round(armory_at.x, 1), round(armory_at.y, 1))
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        hellions = act.ai.units(UnitTypeId.HELLION)
        if hellions and not state["enemies"] and any(unit.tag in tags for unit in hellions):
            spot = _spot(act, home, 4)
            await act.ai.client.debug_create_unit([
                [UnitTypeId.MARINE, 2, spot, _foe(act)],
                [UnitTypeId.VIKINGFIGHTER, 1, spot, _foe(act)],
            ])
            state["enemies"] = True
            state["diag"]["enemy_at"] = (round(spot.x, 1), round(spot.y, 1))
        if act.ai.units(UnitTypeId.HELLIONTANK).exists:
            _note(state, "HELLIONTANK")
        _mark_units(act, state, UnitTypeId.HELLION, "MORPH_HELLBAT")
        _mark_units(act, state, UnitTypeId.HELLIONTANK, "MORPH_HELLBAT")
        medivacs = act.ai.units(UnitTypeId.MEDIVAC)
        for medivac in medivacs:
            await act.ai.client.debug_set_unit_value([medivac.tag], 1, 200)
            if _has_buff(medivac, BuffId.MEDIVACSPEEDBOOST) or "AFTERBURNERS" in _order_text(medivac):
                _note(state, "MEDIVACSPEEDBOOST")
        if state.get("clear") and not state.get("hug"):
            hostiles = [unit for unit in act.ai.enemy_units if not getattr(unit, "is_structure", False)]
            if hostiles:
                await act.ai.client.debug_kill_unit([unit.tag for unit in hostiles])
        if medivacs:
            medivac = medivacs[0]
            state["diag"]["away"] = round(medivac.distance_to(home), 1)
            state["diag"]["home"] = (round(home.x, 1), round(home.y, 1))
            # Retreat micro never starts while the caster is already home.
            if state.get("hug"):
                close = [unit for unit in act.ai.enemy_units(UnitTypeId.VIKINGFIGHTER)
                         if unit.distance_to(medivac) <= 6]
                if not close:
                    await act.ai.client.debug_create_unit(
                        [[UnitTypeId.VIKINGFIGHTER, 1, medivac.position, _foe(act)]]
                    )
        sample = hellions[0] if hellions else None
        state["diag"].update({
            "hellion": hellions.amount,
            "hellbat": act.ai.units(UnitTypeId.HELLIONTANK).amount,
            "armory": act.ai.structures(UnitTypeId.ARMORY).amount,
            "marines": act.ai.enemy_units(UnitTypeId.MARINE).amount,
            "grouped": any(unit.tag in tags for unit in hellions),
            "abilities": _ability_names(act, sample) if sample is not None else [],
            "orders": _order_text(sample) if sample is not None else "",
            "medivac_orders": _order_text(medivacs[0]) if medivacs else "",
        })

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="terran", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        assert_obs_consistent(obs)
        for _ in range(8):
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
            if "HELLIONTANK" in state["seen"] or "MORPH_HELLBAT" in state["seen"]:
                break
        assert "HELLIONTANK" in state["seen"] or "MORPH_HELLBAT" in state["seen"], state["diag"]
        if obs.units.get("medivac", 0) >= 1:
            state["clear"] = True
            far = _far_zone(obs)
            _, feedback, _, _ = drive(env, [
                {"name": "combat", "arguments": {"style": "attack", "target": far, "units": {"medivac": 1}}},
                _wait(1),
            ])
            state["diag"]["combat"] = feedback.receipts[0].result
            state["diag"]["far"] = far
            group = feedback.receipts[0].group
            state["diag"]["group"] = group
            if feedback.receipts[0].result == "accepted" and group and group != "group_0":
                for _ in range(8):
                    if state["diag"].get("away", 0) > 14:
                        break
                    obs, _, terminated, _ = drive(env, [_wait(2)])
                    assert not terminated, obs.game
                if group:
                    state["hug"] = True
                    drive(env, [{"name": "retreat", "arguments": {"group": group}}, _wait(4)])
                    for _ in range(6):
                        if "MEDIVACSPEEDBOOST" in state["seen"]:
                            break
                        drive(env, [_wait(2)])
        if "MEDIVACSPEEDBOOST" not in state["seen"]:
            _keep("medivac", state)
        assert "MEDIVACSPEEDBOOST" in state["seen"], state["diag"]
    finally:
        env.close()


def test_e2e_protoss_stride_stasis_and_interceptors(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "enemies": False, "upgrades": 0, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            shrine_at = await _place(act, UnitTypeId.DARKSHRINE, act.ai.start_location)
            await act.ai.client.debug_create_unit([
                [UnitTypeId.DARKSHRINE, 1, shrine_at, act.ai.player_id],
                [UnitTypeId.DARKTEMPLAR, 1, home, act.ai.player_id],
                [UnitTypeId.CARRIER, 1, home, act.ai.player_id],
                [UnitTypeId.ORACLE, 1, home, act.ai.player_id],
            ])
            state["own"] = True
            state["diag"]["shrine_at"] = (round(shrine_at.x, 1), round(shrine_at.y, 1))
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        templar = act.ai.units(UnitTypeId.DARKTEMPLAR)
        if (templar and not state["enemies"] and any(unit.tag in tags for unit in templar)
                and act.ai.structures(UnitTypeId.DARKSHRINE).ready.exists):
            spot = _spot(act, templar[0].position, 8)
            await act.ai.client.debug_create_unit([[UnitTypeId.MARINE, 4, spot, _foe(act)]])
            state["enemies"] = True
            state["diag"]["enemy_at"] = (round(spot.x, 1), round(spot.y, 1))
        if state["upgrades"] < 6 and act.ai.structures(UnitTypeId.DARKSHRINE).ready.exists:
            await act.ai.client.debug_upgrade()
            state["upgrades"] += 1
        oracles = act.ai.units(UnitTypeId.ORACLE)
        for oracle in oracles:
            await act.ai.client.debug_set_unit_value([oracle.tag], 1, 100)
        if state.get("clear") and not state.get("hug"):
            hostiles = [unit for unit in act.ai.enemy_units if not getattr(unit, "is_structure", False)]
            if hostiles:
                await act.ai.client.debug_kill_unit([unit.tag for unit in hostiles])
        if oracles:
            oracle = oracles[0]
            away = round(oracle.distance_to(home), 1)
            state["diag"]["away"] = away
            state["diag"]["away_peak"] = max(float(state["diag"].get("away_peak", 0)), away)
            state["diag"]["home"] = (round(home.x, 1), round(home.y, 1))
            state["diag"]["oracle_abilities"] = _ability_names(act, oracle)
            if state.get("hug"):
                close = [unit for unit in act.ai.enemy_units(UnitTypeId.MARINE)
                         if unit.distance_to(oracle) <= 6]
                if len(close) < 3:
                    await act.ai.client.debug_create_unit(
                        [[UnitTypeId.MARINE, 4, oracle.position, _foe(act)]]
                    )
        if templar:
            templar_unit = templar[0]
            ready = any("SHADOWSTRIDE" in name for name in _ability_names(act, templar_unit))
            previous = state.get("dt_pos")
            current = (float(templar_unit.position.x), float(templar_unit.position.y))
            # The blink order is gone by the next frame. A ready ability, then a
            # jump larger than a walk, is the cast.
            if previous is not None and state.get("dt_ready"):
                jumped = ((current[0] - previous[0]) ** 2 + (current[1] - previous[1]) ** 2) ** 0.5
                state["diag"]["jump"] = round(max(float(state["diag"].get("jump", 0)), jumped), 2)
                # A short step toward a nearby marine is still a blink, and
                # larger than one walking frame.
                if jumped >= 3:
                    _note(state, "SHADOWSTRIDE")
            state["dt_ready"] = ready
            state["dt_pos"] = current
        _mark_units(act, state, UnitTypeId.DARKTEMPLAR, "SHADOWSTRIDE")
        _mark_units(act, state, UnitTypeId.CARRIER, "BUILD_INTERCEPTORS")
        if act.ai.units(UnitTypeId.INTERCEPTOR).exists:
            _note(state, "INTERCEPTOR")
        if act.ai.structures(UnitTypeId.ORACLESTASISTRAP).exists or act.ai.units(UnitTypeId.ORACLESTASISTRAP).exists:
            _note(state, "STASIS")
        _mark_units(act, state, UnitTypeId.ORACLE, "STASISTRAP")
        sample = templar[0] if templar else None
        state["diag"].update({
            "dt": templar.amount,
            "shrine": act.ai.structures(UnitTypeId.DARKSHRINE).amount,
            "marines": act.ai.enemy_units(UnitTypeId.MARINE).amount,
            "marine_peak": max(
                int(state["diag"].get("marine_peak", 0)),
                act.ai.enemy_units(UnitTypeId.MARINE).amount,
            ),
            "upgrades": state["upgrades"],
            "grouped": any(unit.tag in tags for unit in templar),
            "abilities": _ability_names(act, sample) if sample is not None else [],
            "orders": _order_text(sample) if sample is not None else "",
            "oracle_orders": _order_text(oracles[0]) if oracles else "",
        })

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="protoss", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        assert_obs_consistent(obs)
        for _ in range(10):
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
            if "SHADOWSTRIDE" in state["seen"] and (
                "BUILD_INTERCEPTORS" in state["seen"] or "INTERCEPTOR" in state["seen"]
            ):
                break
        if "SHADOWSTRIDE" not in state["seen"]:
            _keep("stride", state)
        assert "SHADOWSTRIDE" in state["seen"], state["diag"]
        assert "BUILD_INTERCEPTORS" in state["seen"] or "INTERCEPTOR" in state["seen"], state["diag"]
        if obs.units.get("oracle", 0) >= 1:
            state["clear"] = True
            far = _far_zone(obs)
            _, feedback, _, _ = drive(env, [
                {"name": "combat", "arguments": {"style": "attack", "target": far, "units": {"oracle": 1}}},
                _wait(1),
            ])
            state["diag"]["combat"] = feedback.receipts[0].result
            state["diag"]["far"] = far
            group = feedback.receipts[0].group
            state["diag"]["group"] = group
            if feedback.receipts[0].result == "accepted" and group and group != "group_0":
                for _ in range(8):
                    if state["diag"].get("away", 0) > 14:
                        break
                    obs, _, terminated, _ = drive(env, [_wait(2)])
                    assert not terminated, obs.game
                if group:
                    state["hug"] = True
                    drive(env, [{"name": "retreat", "arguments": {"group": group}}, _wait(4)])
                    for _ in range(6):
                        if "STASIS" in state["seen"] or "STASISTRAP" in state["seen"]:
                            break
                        drive(env, [_wait(2)])
        if "STASIS" not in state["seen"] and "STASISTRAP" not in state["seen"]:
            _keep("stasis", state)
        assert "STASIS" in state["seen"] or "STASISTRAP" in state["seen"], state["diag"]
    finally:
        env.close()


def test_e2e_zerg_overseer_contaminate_and_oversight(monkeypatch) -> None:
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "enemies": False, "diag": {}}

    async def collect(act):
        try:
            await _body(act)
        except Exception as exc:
            state["diag"]["error"] = f"{type(exc).__name__}: {exc}"
            _keep("overseer", state)
            raise

    async def _body(act):
        home = _home(act)
        if not state["own"]:
            await act.ai.client.debug_create_unit([
                [UnitTypeId.OVERSEER, 1, home, act.ai.player_id],
            ])
            state["own"] = True
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        overseers = list(act.ai.units(UnitTypeId.OVERSEER)) + list(act.ai.units(UnitTypeId.OVERSEERSIEGEMODE))
        grouped = any(unit.tag in tags for unit in overseers)
        if overseers and not state["enemies"] and grouped:
            spot = _spot(act, overseers[0].position, 4)
            await act.ai.client.debug_create_unit([[UnitTypeId.MARINE, 2, spot, _foe(act)]])
            state["enemies"] = True
            state["diag"]["enemy_at"] = (round(spot.x, 1), round(spot.y, 1))
        if overseers and grouped:
            overseer = overseers[0]
            nearby = [structure for structure in act.ai.enemy_structures
                      if structure.distance_to(overseer) <= 7]
            tries = state.get("depot_tries", 0)
            if not nearby and tries < 8:
                point = overseer.position if tries % 2 == 0 else _spot(act, overseer.position, 3)
                await act.ai.client.debug_create_unit(
                    [[UnitTypeId.BARRACKS, 1, point, _foe(act)]]
                )
                state["depot_tries"] = tries + 1
                state["diag"]["depot_at"] = (round(point.x, 1), round(point.y, 1))
            for structure in nearby:
                await act.ai.client.debug_set_unit_value(
                    [structure.tag], 2, max(float(structure.health_max), 400)
                )
        for overseer in overseers:
            state["diag"]["energy"] = round(float(getattr(overseer, "energy", 0) or 0), 1)
            await act.ai.client.debug_set_unit_value([overseer.tag], 1, 200)
            if overseer.type_id == UnitTypeId.OVERSEERSIEGEMODE:
                _note(state, "OVERSIGHT")
            text = _order_text(overseer)
            if "OVERSEERMODE" in text or "OVERSIGHT" in text:
                _note(state, "OVERSIGHT")
            if "CONTAMINATE" in text:
                _note(state, "CONTAMINATE")
        for structure in act.ai.enemy_structures:
            if _has_buff(structure, BuffId.CONTAMINATED):
                _note(state, "CONTAMINATE")
        sample = overseers[0] if overseers else None
        state["diag"].update({
            "overseer": len(overseers),
            "marines": act.ai.enemy_units(UnitTypeId.MARINE).amount,
            "depots": act.ai.enemy_structures(UnitTypeId.SUPPLYDEPOT).amount,
            "foreign": [structure.type_id.name for structure in act.ai.enemy_structures][:6],
            "grouped": grouped,
            "abilities": _ability_names(act, sample) if sample is not None else [],
            "orders": _order_text(sample) if sample is not None else "",
            "ov": (
                (round(sample.position.x, 1), round(sample.position.y, 1))
                if sample is not None else None
            ),
            "structs": [
                (
                    structure.type_id.name,
                    round(structure.distance_to(sample), 1) if sample is not None else None,
                    bool(getattr(structure, "is_visible", False)),
                )
                for structure in act.ai.enemy_structures
            ][:4],
        })

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        assert_obs_consistent(obs)
        for _ in range(10):
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
            if "OVERSIGHT" in state["seen"] and "CONTAMINATE" in state["seen"]:
                break
        assert "OVERSIGHT" in state["seen"], state["diag"]
        if "CONTAMINATE" not in state["seen"]:
            _keep("contaminate", state)
        assert "CONTAMINATE" in state["seen"], state["diag"]
    finally:
        env.close()

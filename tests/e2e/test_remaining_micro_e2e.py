"""Real-client checks for combat casts that had only unit tests.

Debug cheats grant energy, health, and research. They do not cast the skills.
Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations


from tests.helpers.drive import drive
import os

import pytest

from tests.e2e.test_base_abilities_e2e import (
    _ability_names, _far_zone, _foe, _has_buff, _home, _install, _note, _order_text, _spot, _wait,
)
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_e2e_protoss_observer_prism_oracle_and_time_warp(monkeypatch) -> None:
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "enemies": False, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            await act.ai.client.debug_create_unit([
                [UnitTypeId.OBSERVER, 1, home, act.ai.player_id],
                [UnitTypeId.WARPPRISM, 1, home, act.ai.player_id],
                [UnitTypeId.ORACLE, 1, home, act.ai.player_id],
                [UnitTypeId.MOTHERSHIP, 1, home, act.ai.player_id],
                [UnitTypeId.PHOENIX, 1, home, act.ai.player_id],
                [UnitTypeId.VOIDRAY, 1, home, act.ai.player_id],
            ])
            state["own"] = True
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        observers = list(act.ai.units(UnitTypeId.OBSERVER)) + list(act.ai.units(UnitTypeId.OBSERVERSIEGEMODE))
        prisms = list(act.ai.units(UnitTypeId.WARPPRISM)) + list(act.ai.units(UnitTypeId.WARPPRISMPHASING))
        ships = list(act.ai.units(UnitTypeId.MOTHERSHIP))
        ship_ready = bool(ships) and ships[0].tag in tags
        if ship_ready and act.ai.enemy_units(UnitTypeId.MARINE).amount < 4:
            spot = _spot(act, ships[0].position, 3)
            await act.ai.client.debug_create_unit([
                [UnitTypeId.MARINE, 4, spot, _foe(act)],
                [UnitTypeId.MARAUDER, 1, spot, _foe(act)],
            ])
            state["enemies"] = True
        for unit in list(act.ai.units(UnitTypeId.ORACLE)) + list(act.ai.units(UnitTypeId.MOTHERSHIP)) + list(act.ai.units(UnitTypeId.PHOENIX)):
            await act.ai.client.debug_set_unit_value([unit.tag], 1, 200)
        for unit in observers:
            if unit.type_id == UnitTypeId.OBSERVERSIEGEMODE or "SURVEILLANCE" in _order_text(unit):
                _note(state, "SURVEILLANCE")
        for unit in prisms:
            if unit.type_id == UnitTypeId.WARPPRISMPHASING or "PHASING" in _order_text(unit):
                _note(state, "PHASING")
        for oracle in act.ai.units(UnitTypeId.ORACLE):
            text = _order_text(oracle)
            if _has_buff(oracle, BuffId.ORACLEWEAPON) or "PULSARBEAMON" in text:
                _note(state, "BEAM")
            if "REVELATION" in text:
                _note(state, "REVELATION")
        for ship in act.ai.units(UnitTypeId.MOTHERSHIP):
            names = _ability_names(act, ship)
            if "TIMEWARP" in _order_text(ship):
                _note(state, "TIMEWARP")
            elif "EFFECT_TIMEWARP" in names:
                state["warp_ready"] = True
            elif state.get("warp_ready"):
                # The offer disappears once the cast goes on cooldown.
                _note(state, "TIMEWARP")
        for phoenix in act.ai.units(UnitTypeId.PHOENIX):
            if "GRAVITON" in _order_text(phoenix):
                _note(state, "GRAVITON")
        for enemy in act.ai.enemy_units:
            if _has_buff(enemy, BuffId.GRAVITONBEAM):
                _note(state, "GRAVITON")
        for ray in act.ai.units(UnitTypeId.VOIDRAY):
            if "PRISMATIC" in _order_text(ray) or _has_buff(ray, BuffId.VOIDRAYSWARMDAMAGEBOOST):
                _note(state, "PRISMATIC")
            state["diag"]["void_abilities"] = _ability_names(act, ray)
            state["diag"]["void_orders"] = _order_text(ray)
        sample = observers[0] if observers else None
        state["diag"].update({
            "observer": len(observers),
            "prism": len(prisms),
            "oracle": act.ai.units(UnitTypeId.ORACLE).amount,
            "mothership": act.ai.units(UnitTypeId.MOTHERSHIP).amount,
            "marines": act.ai.enemy_units(UnitTypeId.MARINE).amount,
            "grouped": any(unit.tag in tags for unit in observers),
            "ship_grouped": ship_ready,
            "abilities": _ability_names(act, sample) if sample is not None else [],
            "oracle_abilities": _ability_names(act, act.ai.units(UnitTypeId.ORACLE)[0])
            if act.ai.units(UnitTypeId.ORACLE) else [],
            "ship_abilities": _ability_names(act, act.ai.units(UnitTypeId.MOTHERSHIP)[0])
            if act.ai.units(UnitTypeId.MOTHERSHIP) else [],
            "prism_abilities": _ability_names(act, prisms[0]) if prisms else [],
            "seen": sorted(state["seen"]),
            "ship_orders": _order_text(ships[0]) if ships else "",
            "ship_energy": (round(ships[0].energy, 1), round(ships[0].energy_max, 1)) if ships else None,
        })

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="protoss", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        assert_obs_consistent(obs)
        for _ in range(12):
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
            if {"SURVEILLANCE", "PHASING", "BEAM", "TIMEWARP", "GRAVITON", "PRISMATIC"} <= state["seen"]:
                break
        assert "SURVEILLANCE" in state["seen"], state["diag"]
        assert "PHASING" in state["seen"], state["diag"]
        assert "BEAM" in state["seen"] or "REVELATION" in state["seen"], state["diag"]
        assert "TIMEWARP" in state["seen"], state["diag"]
        assert "GRAVITON" in state["seen"], state["diag"]
        assert "PRISMATIC" in state["seen"], state["diag"]
    finally:
        env.close()


def test_e2e_zerg_burrow_and_viper_consume(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "hurt": None, "ally": None, "upgrades": 0, "diag": {}}

    async def collect(act):
        home = _home(act)
        if state["upgrades"] < 3:
            await act.ai.client.debug_upgrade()
            state["upgrades"] += 1
        if not state["own"]:
            await act.ai.client.debug_create_unit([
                [UnitTypeId.ROACH, 2, home, act.ai.player_id],
                [UnitTypeId.BANELING, 1, home, act.ai.player_id],
                [UnitTypeId.VIPER, 1, home, act.ai.player_id],
            ])
            state["own"] = True
        roaches = list(act.ai.units(UnitTypeId.ROACH)) + list(act.ai.units(UnitTypeId.ROACHBURROWED))
        if roaches and state["hurt"] is None:
            state["hurt"] = roaches[0].tag
            if len(roaches) > 1:
                state["ally"] = roaches[1].tag
        for roach in roaches:
            if roach.tag == state["hurt"] and roach.type_id == UnitTypeId.ROACH:
                await act.ai.client.debug_set_unit_value([roach.tag], 2, 20)
            if roach.type_id == UnitTypeId.ROACHBURROWED and roach.tag == state["hurt"]:
                _note(state, "ROACHBURROW")
            text = _order_text(roach)
            if roach.tag == state["hurt"] and "BURROWDOWN_ROACH" in text:
                _note(state, "ROACHBURROW")
        for baneling in list(act.ai.units(UnitTypeId.BANELING)) + list(act.ai.units(UnitTypeId.BANELINGBURROWED)):
            if baneling.type_id == UnitTypeId.BANELINGBURROWED or "BURROWDOWN_BANELING" in _order_text(baneling):
                _note(state, "BANEBURROW")
            if state.get("hug"):
                state["diag"]["away"] = round(baneling.distance_to(home), 1)
        for viper in act.ai.units(UnitTypeId.VIPER):
            await act.ai.client.debug_set_unit_value([viper.tag], 1, 20)
            if "CONSUME" in _order_text(viper):
                _note(state, "CONSUME")
        ally = next((unit for unit in roaches if unit.tag == state["ally"]), None)
        if ally is not None and ally.type_id == UnitTypeId.ROACH:
            previous = state.get("ally_health")
            if previous is None:
                state["ally_health"] = ally.health
            elif previous - ally.health >= 40:
                _note(state, "CONSUME")
        sample = roaches[0] if roaches else None
        state["diag"].update({
            "roach": len(roaches),
            "baneling": act.ai.units(UnitTypeId.BANELING).amount + act.ai.units(UnitTypeId.BANELINGBURROWED).amount,
            "viper": act.ai.units(UnitTypeId.VIPER).amount,
            "upgrades": state["upgrades"],
            "abilities": _ability_names(act, sample) if sample is not None else [],
            "viper_abilities": _ability_names(act, act.ai.units(UnitTypeId.VIPER)[0])
            if act.ai.units(UnitTypeId.VIPER) else [],
            "roach_health": round(sample.health, 1) if sample is not None else None,
            "seen": sorted(state["seen"]),
        })

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=220,
        ))
        assert_obs_consistent(obs)
        for _ in range(10):
            obs, _, terminated, _ = drive(env, [_wait(2)])
            assert not terminated, obs.game
            if "ROACHBURROW" in state["seen"] and "CONSUME" in state["seen"]:
                break
        assert "ROACHBURROW" in state["seen"], state["diag"]
        if obs.units.get("baneling", 0) >= 1:
            far = _far_zone(obs)
            _, feedback, _, _ = drive(env, [
                {"name": "combat", "arguments": {"style": "attack", "target": far, "units": {"baneling": 1}}},
                _wait(1),
            ])
            group = feedback.receipts[0].group
            state["diag"]["combat"] = feedback.receipts[0].result
            state["diag"]["group"] = group
            if feedback.receipts[0].result == "accepted" and group and group != "group_0":
                for _ in range(8):
                    if state["diag"].get("away", 0) > 14:
                        break
                    obs, _, terminated, _ = drive(env, [_wait(2)])
                    assert not terminated, obs.game
                state["hug"] = True
                drive(env, [{"name": "retreat", "arguments": {"group": group}}, _wait(4)])
                for _ in range(6):
                    if "BANEBURROW" in state["seen"]:
                        break
                    drive(env, [_wait(2)])
        assert "BANEBURROW" in state["seen"], state["diag"]
    finally:
        env.close()

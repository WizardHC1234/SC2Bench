"""The three casts still missing after the last pass. Enable with SC2BENCH_E2E=1."""
from __future__ import annotations


from tests.helpers.drive import drive
import os

import pytest

from tests.e2e.test_base_abilities_e2e import (
    _ability_names, _foe, _home, _install, _note, _order_text, _spot, _wait,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _finish(env, state, need: set[str], steps: int = 8):
    for _ in range(steps):
        _obs, _, terminated, _ = drive(env, [_wait(1)])
        assert not terminated, _obs.game
        if need <= state["seen"]:
            return
    assert not (need - state["seen"]), (sorted(need - state["seen"]), state["diag"])


def test_e2e_immortal_barrier(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            await act.ai.client.debug_create_unit([[UnitTypeId.IMMORTAL, 1, home, act.ai.player_id]])
            state["own"] = True
        immortals = list(act.ai.units(UnitTypeId.IMMORTAL))
        for immortal in immortals:
            if float(getattr(immortal, "health", 0) or 0) < 80:
                await act.ai.client.debug_set_unit_value([immortal.tag], 2, 200)
            raw = [int(item) for item in getattr(immortal._proto, "buff_ids", [])]
            if raw:
                _note(state, "BARRIER")
            state["diag"]["buffs"] = raw
            state["diag"]["shield"] = round(float(getattr(immortal, "shield", 0) or 0), 1)
            state["diag"]["health"] = round(float(getattr(immortal, "health", 0) or 0), 1)
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        if immortals and immortals[0].tag in tags and act.ai.enemy_units(UnitTypeId.MARINE).amount < 4:
            await act.ai.client.debug_set_unit_value([immortals[0].tag], 3, 5)
            await act.ai.client.debug_create_unit(
                [[UnitTypeId.MARINE, 6, _spot(act, immortals[0].position, 2), _foe(act)]]
            )
        state["diag"]["seen"] = sorted(state["seen"])

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(race="protoss", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=60))
        _finish(env, state, {"BARRIER"})
    finally:
        env.close()


def test_e2e_cloud_and_unburrow(monkeypatch) -> None:
    from sc2.ids.effect_id import EffectId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "cleared": False, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            await act.ai.client.debug_create_unit([
                [UnitTypeId.VIPER, 1, home, act.ai.player_id],
                [UnitTypeId.LURKERMP, 1, home, act.ai.player_id],
            ])
            state["own"] = True
        for viper in act.ai.units(UnitTypeId.VIPER):
            await act.ai.client.debug_set_unit_value([viper.tag], 1, 200)
            await act.ai.client.debug_set_unit_value([viper.tag], 2, 150)
            if "BLINDING" in _order_text(viper):
                _note(state, "CLOUD")
            state["diag"]["viper"] = _ability_names(act, viper)
            state["diag"]["viper_orders"] = _order_text(viper)
        lurkers = list(act.ai.units(UnitTypeId.LURKERMP)) + list(act.ai.units(UnitTypeId.LURKERMPBURROWED))
        for lurker in lurkers:
            await act.ai.client.debug_set_unit_value([lurker.tag], 2, 200)
            if lurker.type_id == UnitTypeId.LURKERMPBURROWED or "BURROWDOWN_LURKER" in _order_text(lurker):
                _note(state, "LURKER")
            if state["cleared"] and (lurker.type_id == UnitTypeId.LURKERMP or "BURROWUP" in _order_text(lurker)):
                _note(state, "UNBURROW")
        for effect in act.ai.state.effects:
            if effect.id == EffectId.BLINDINGCLOUDCP:
                _note(state, "CLOUD")
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        vipers = list(act.ai.units(UnitTypeId.VIPER))
        ready = bool(vipers) and vipers[0].tag in tags and bool(lurkers) and lurkers[0].tag in tags
        marines = list(act.ai.enemy_units(UnitTypeId.MARINE))
        if ready and len(marines) < 8 and not state["cleared"]:
            await act.ai.client.debug_create_unit(
                [[UnitTypeId.MARINE, 12, _spot(act, home, 6), _foe(act)]]
            )
        if {"LURKER", "CLOUD"} <= state["seen"] and marines and not state["cleared"]:
            await act.ai.client.debug_kill_unit([unit.tag for unit in marines])
            state["cleared"] = True
        state["diag"].update({"seen": sorted(state["seen"]), "ready": ready, "marines": len(marines)})

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=70))
        _finish(env, state, {"CLOUD", "UNBURROW"})
    finally:
        env.close()


def test_e2e_parasitic_bomb(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"seen": set(), "own": False, "diag": {}}

    async def collect(act):
        home = _home(act)
        if not state["own"]:
            await act.ai.client.debug_create_unit([[UnitTypeId.VIPER, 1, home, act.ai.player_id]])
            state["own"] = True
        vipers = list(act.ai.units(UnitTypeId.VIPER))
        tags = set(getattr(act.ai, "bench_group0_tags", set()) or ())
        for viper in vipers:
            await act.ai.client.debug_set_unit_value([viper.tag], 1, 200)
            await act.ai.client.debug_set_unit_value([viper.tag], 2, 150)
            if "PARASITIC" in _order_text(viper) or "BOMB" in _order_text(viper):
                _note(state, "BOMB")
            names = _ability_names(act, viper)
            state["diag"]["has_bomb"] = any("PARASITIC" in name or "BOMB" in name for name in names)
            state["diag"]["names"] = [name for name in names if "STOP" not in name and "MOVE" not in name and "PATROL" not in name and "HOLD" not in name]
        if vipers and vipers[0].tag in tags and act.ai.enemy_units(UnitTypeId.VIKINGFIGHTER).amount < 4:
            await act.ai.client.debug_create_unit(
                [[UnitTypeId.VIKINGFIGHTER, 6, _spot(act, vipers[0].position, 2), _foe(act)]]
            )
        vikings = list(act.ai.enemy_units(UnitTypeId.VIKINGFIGHTER))
        state["diag"]["vikings"] = len(vikings)
        state["diag"]["near"] = round(vipers[0].distance_to(vikings[0]), 1) if vipers and vikings else None
        state["diag"]["seen"] = sorted(state["seen"])
        state["diag"]["vikings"] = act.ai.enemy_units(UnitTypeId.VIKINGFIGHTER).amount
        state["diag"]["grouped"] = bool(vipers) and vipers[0].tag in tags

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=60))
        _finish(env, state, {"BOMB"})
    finally:
        env.close()

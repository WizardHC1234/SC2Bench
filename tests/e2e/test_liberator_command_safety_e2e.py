"""Actual engine regression for defender-mode commands from the Qwen batch."""
import os
import json
from pathlib import Path

import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2bench_env import Environment, EpisodeConfig
from tests.e2e.test_expanded_micro_e2e import _install_debug_adapter
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(os.environ.get('SC2BENCH_E2E') != '1', reason='real SC2 opt-in')


@pytest.mark.parametrize('repeat', range(2))
def test_liberators_deploy_fire_and_relocate_without_native_errors(monkeypatch, tmp_path, repeat):
    async def setup(act):
        if getattr(act, 'spawned_safety_units', False):
            # This is a weapon-execution fixture, not an opponent AI test.
            # Keep its ground targets in place: Blizzard AI otherwise flees
            # the no-anti-air matchup, making kills depend on spawn side.
            for target in act.ai.enemy_units:
                if target.type_id == U.MARAUDER:
                    target.stop()
            return
        act.spawned_safety_units = True
        await act.ai.client.debug_control_enemy()
        home = act.ai.start_location
        forward = home.towards(act.ai.enemy_start_locations[0], 9)
        await act.ai.client.debug_create_unit([
            [U.LIBERATOR, 2, forward, 1],
            [U.MARAUDER, 6, forward.towards(home, -7), 2],
        ])

    adapter = _install_debug_adapter(monkeypatch, setup)
    env = Environment('sharpy', record_dir=tmp_path)
    forms = set()
    warnings = []
    enemy_counts = []
    trace = []
    try:
        obs = env.reset(EpisodeConfig(opponent='veryeasy', game_time_limit_seconds=90))
        for _ in range(20):
            obs, feedback, done, _ = drive(env, [{'name': 'advance', 'arguments': {'seconds': 2}}])
            ai = adapter.debug_setup.ai
            forms.update(u.type_id for u in ai.units if u.type_id in {U.LIBERATOR, U.LIBERATORAG})
            enemy_counts.append(sum(u.type_id == U.MARAUDER for u in ai.enemy_units))
            micro = ai.bench_home_gather._micro_rules.unit_micros[U.LIBERATOR]
            trace.append(dict(seconds=float(ai.time),
                own=[dict(tag=u.tag, form=u.type_id.name, position=list(u.position),
                          orders=[dict(ability=str(o.ability.id), target=str(o.target)) for o in u.orders])
                     for u in ai.units if u.type_id in {U.LIBERATOR, U.LIBERATORAG}],
                enemies=[dict(position=list(u.position), health=float(u.health)) for u in ai.enemy_units if u.type_id == U.MARAUDER],
                deployed_points={str(k): list(v) for k, v in micro.deployed_points.items()}))
            warnings.extend(e for e in feedback.events if e['type'] == 'execution_warning')
            if done:
                break
        Path('analysis/platform_audit/liberator_native_trace_' + str(repeat) + '.json').write_text(
            json.dumps(dict(trace=trace, warnings=warnings), indent=2), encoding='utf-8')
        assert U.LIBERATORAG in forms, forms
        assert enemy_counts[0] > 0 and enemy_counts[-1] < enemy_counts[0], enemy_counts
        before = {u.tag: u.position for u in ai.units if u.type_id in {U.LIBERATOR, U.LIBERATORAG}}
        assert obs.own_forces.free.get('liberator', 0) >= 2
        obs, feedback, done, _ = drive(env, [
            {'name': 'dispatch_group', 'arguments': {'source': 'group_0', 'units': {'liberator': 2},
                                                    'order': 'defend', 'target': 'zone_1'}},
            {'name': 'advance', 'arguments': {'seconds': 12}},
        ])
        assert feedback.receipts[0].result == 'accepted'
        warnings.extend(e for e in feedback.events if e['type'] == 'execution_warning')
        moved = [u for u in ai.units if u.tag in before and u.distance_to(before[u.tag]) > 5]
        assert len(moved) == 2
        assert not [e for e in warnings if e['unit_type'] in {'liberator', 'liberatorag'}], warnings
    finally:
        env.close()

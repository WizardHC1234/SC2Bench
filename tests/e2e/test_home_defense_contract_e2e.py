"""Home troops must actually fire without a model dispatching an army group."""
import json
import os
from pathlib import Path
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2bench_env import Environment, EpisodeConfig
from tests.e2e.test_expanded_micro_e2e import _install_debug_adapter
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(os.environ.get('SC2BENCH_E2E') != '1', reason='real SC2 opt-in')


def test_group0_kills_attackers_without_army_orders(monkeypatch, tmp_path):
    async def setup(act):
        if getattr(act, 'spawned_defense_units', False) or not hasattr(act.ai, 'bench_home_gather'):
            return
        act.spawned_defense_units = True
        point = act.ai.bench_home_gather.home_point()
        await act.ai.client.debug_create_unit([
            [U.MARINE, 12, point, 1],
            [U.ZERGLING, 8, point.towards(act.ai.enemy_start_locations[0], 7), 2],
        ])

    adapter = _install_debug_adapter(monkeypatch, setup)
    env = Environment('sharpy', record_dir=tmp_path)
    enemy_counts, warnings, contact = [], [], False
    try:
        env.reset(EpisodeConfig(opponent='veryeasy', game_time_limit_seconds=90))
        for _ in range(20):
            obs, feedback, done, _ = drive(env, [{'name': 'advance', 'arguments': {'seconds': 2}}])
            ai = adapter.debug_setup.ai
            enemy_counts.append(sum(u.type_id == U.ZERGLING for u in ai.enemy_units))
            contact |= bool(ai.bench_group0_engaged)
            warnings.extend(e for e in feedback.events if e['type'] == 'execution_warning')
            if done:
                break
        evidence = dict(enemy_counts=enemy_counts, contact=contact, warnings=warnings,
                        own_marines=sum(u.type_id == U.MARINE for u in ai.units),
                        record_directory=str(env.record_path))
        Path('analysis/platform_audit/home_defense_contract.json').write_text(
            json.dumps(evidence, indent=2), encoding='utf-8')
        assert max(enemy_counts) > 0 and enemy_counts[-1] == 0, evidence
        assert evidence['own_marines'] > 0, evidence
        assert not warnings, evidence
    finally:
        env.close()

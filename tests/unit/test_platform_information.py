"""Model information is objective, complete enough and does not invent state."""
import copy
from types import SimpleNamespace as NS

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.state_reader import _production_capacity, _identified_enemy_race, read_snapshot
from sc2bench_env.backends.sharpy.races.terran_production import read_production_capacity
from sc2bench_env.interface.action_catalog import render_system_prompt, targets_for_action
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.recording.context import platform_messages


ADAPTER = NS(race_name="terran", normalize_unit_name=lambda name: name.lower())
ADAPTER.townhall_targets = ("command_center", "orbital_command", "planetary_fortress")
ADAPTER.read_production_capacity = lambda ai: read_production_capacity(ai, ADAPTER)


def building(name, tag, *, addon=0, orders=(), ready=True, flying=False):
    return NS(type_id=NS(name=name), tag=tag, is_ready=ready,
              build_progress=1 if ready else .5, is_flying=flying,
              add_on_tag=addon, orders=list(orders), is_structure=True)


def capacity(*structures):
    return {row['facility']: row for row in _production_capacity(NS(structures=structures), ADAPTER)}


def test_capacity_counts_attached_ready_addons_and_shared_slots():
    rows = capacity(building('barracks', 1, addon=11, orders=[1]),
                    building('barracks_reactor', 11),
                    building('barracks', 2, addon=12),
                    building('barracks_techlab', 12),
                    building('barracks', 3, orders=[1, 2, 3]),
                    building('barracks_techlab', 13))  # detached: not a host
    assert rows['barracks'] == dict(facility='barracks', ready_grounded=3,
        techlab_hosts=1, reactor_hosts=1, capacity=4, occupied_slots=2,
        free_slots=2, free_techlab_slots=1)
    assert rows['factory']['free_slots'] == 0


def test_capacity_excludes_unfinished_flying_and_unfinished_addons():
    rows = capacity(building('starport', 1, flying=True),
                    building('starport', 2, ready=False),
                    building('starport', 3, addon=13, orders=[1]),
                    building('starport_reactor', 13, ready=False))
    assert rows['starport']['ready_grounded'] == 1
    assert rows['starport']['capacity'] == 1
    assert rows['starport']['free_slots'] == 0


def test_townhall_variants_share_scv_capacity_and_morph_orders_occupy_it():
    rows = capacity(building('command_center', 1, orders=[1]),
                    building('orbital_command', 2), building('planetary_fortress', 3))
    assert rows['command_center']['capacity'] == 3
    assert rows['command_center']['free_slots'] == 2


def test_unknown_flags_or_orders_do_not_invent_capacity():
    parent = building('factory', 1)
    parent.is_flying = None
    assert capacity(parent)['factory']['free_slots'] is None
    parent.is_flying = False
    parent.orders = None
    assert capacity(parent)['factory']['capacity'] is None
    parent.orders = []
    del parent.add_on_tag
    assert capacity(parent)['factory']['free_slots'] is None
    assert _production_capacity(NS(structures=[]), NS(race_name='zerg')) is None


@pytest.mark.parametrize('race', ['terran', 'protoss', 'zerg'])
def test_identified_random_enemy_race_updates_observation(race):
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(enemy_race='random'))
        snapshot = env.backend.snapshot()
        assert env._build_observation(snapshot).enemy_race == 'unknown'
        snapshot.info['identified_enemy_race'] = _identified_enemy_race(NS(enemy_race=NS(name=race.title())))
        assert env._build_observation(snapshot).game.enemy_race == race
        assert _identified_enemy_race(NS(enemy_race=NS(name='Random'))) is None
    finally:
        env.close()


def test_worker_inventory_ignores_food_counter_and_excludes_mules():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        snapshot = env.backend.snapshot()
        snapshot.units = {'scv': 14, 'mule': 2}
        snapshot.info['supply_workers'] = 17
        obs = env._build_observation(snapshot)
        assert obs.economy.worker_count == obs.own_forces.workers['scv'] == 14
        assert obs.own_forces.workers['mule'] == 2
    finally:
        env.close()


def test_production_capacity_reaches_structured_and_text_views_without_guessing():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        initial = env.reset()
        assert initial.production is None
        assert '[Production Capacity]\nunknown' in render_observation_text(initial.to_dict())
        snapshot = env.backend.snapshot()
        snapshot.info['production'] = list(capacity(building('barracks', 1)).values())
        obs = env._build_observation(snapshot)
        assert obs.to_dict()['production'][1]['free_slots'] == 1
        assert 'Free Tech Lab slots' in render_observation_text(obs.to_dict())
    finally:
        env.close()


def test_context_deduplicates_only_matching_events_and_keeps_raw_inputs():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        obs = env.reset().to_dict()
        event = {'type': 'train_completed', 'target': 'marine', 'count': 3}
        other = dict(event, count=4)
        obs['recent_events'] = [event]
        feedback = {'receipts': [{'action': 'train', 'result': 'accepted'}],
                    'events': [event, other]}
        originals = copy.deepcopy((obs, feedback))
        user = platform_messages('rules', obs, feedback)[1]['content']
        assert user.count('train_completed') == 2
        assert 'Count: 3' in user and 'Count: 4' in user
        assert 'train: accepted' in user
        assert (obs, feedback) == originals
        feedback['events'] = [event, event, event]
        user = platform_messages('rules', obs, feedback)[1]['content']
        assert user.count('train_completed') == 3  # preserve equal event occurrences
    finally:
        env.close()


def test_prompt_covers_every_train_unit_without_prescribing_a_composition():
    prompt = render_system_prompt()
    capabilities = prompt.split('Terran capabilities')[1].split('Target notes:')[0]
    for spec in targets_for_action('train'):
        assert spec.name in capabilities
    assert 'Backend decides Medivac loading/transport/unloading' in prompt
    assert 'Free Tech Lab slots' not in prompt or 'subset' in prompt
    assert 'shared/unreserved' in prompt
    assert 'not a recommended production target' in prompt


def test_real_snapshot_reader_wires_capacity_and_race_into_environment():
    class Units(list):
        @property
        def ready(self):
            return Units(unit for unit in self if unit.is_ready)
    ai = NS(structures=Units([building('barracks', 1, addon=11),
                             building('barracks_techlab', 11)]),
            units=Units(), townhalls=Units(), gas_buildings=Units(),
            enemy_race=NS(name='Zerg'))
    snapshot, *_ = read_snapshot(ai, ADAPTER)
    assert snapshot.info['identified_enemy_race'] == 'zerg'
    assert snapshot.info['production'][1]['free_techlab_slots'] == 1
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(enemy_race='random'))
        obs = env._build_observation(snapshot)
        assert obs.game.enemy_race == 'zerg'
        text = platform_messages(render_system_prompt(), obs.to_dict())[1]['content']
        assert 'Enemy race: zerg' in text
        assert 'barracks | 1 | 1 | 0 | 1 | 0 | 1 | 1' in text
    finally:
        env.close()

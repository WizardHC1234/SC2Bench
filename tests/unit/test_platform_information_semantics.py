"""Information contract: intended orders/contact cannot masquerade as outcomes."""
from copy import deepcopy
import pytest
from sc2bench_env.interface.observation_text import brief_combat_lines, combat_lines, brief_available_target_lines
from sc2bench_env.adapters.llm.tool_results import _render_army_state
from sc2bench_env.adapters.llm.observation import platform_messages
from sc2bench_env.interface.platform_prompt import default_prompt_parts, render_prompt
from sc2bench_env import Environment


def groups(phase='engaging'):
    return {'group_0': dict(style='defend', target='zone_1', phase=phase,
                            status='active', alive={'medivac': 2}, assigned=False)}


def test_brief_full_and_query_views_separate_rally_from_actual_location():
    data = groups('guarding')
    original = deepcopy(data)
    for text in ['\n'.join(brief_combat_lines(data)), '\n'.join(combat_lines(data)),
                 _render_army_state({'groups': data, 'own_forces': {}, 'outbound_group_count': 0})]:
        assert 'rally target' in text.lower()
        assert 'not' in text and 'location' in text
        assert 'no enemy contact reported' in text
        assert 'guarding' not in text
    assert data == original


def test_support_only_contact_is_not_claimed_to_be_firing_in_model_input():
    env = Environment('fake', record_trajectory=False)
    try:
        observation = env.reset().to_dict()
    finally:
        env.close()
    observation['combat'] = groups()
    observation['own_forces'] = {'army': {'medivac': 2}, 'free': {'medivac': 2}}
    text = platform_messages('contract', observation)[1]['content']
    assert 'enemy contact' in text
    assert 'engaging' not in text and 'fighting' not in text
    assert 'medivac 2' in text


def test_tech_only_qualifier_appears_next_to_the_available_list():
    text = '\n'.join(brief_available_target_lines({'build': ['factory_reactor']}))
    assert 'Technology checklist only' in text
    assert 'producer' in text and 'placement' in text
    assert 'factory_reactor' in text


def test_empty_home_pool_is_explicit_even_when_other_groups_have_troops():
    data = groups('guarding')
    data['group_0']['alive'] = {}
    data['group_3'] = dict(style='defend', target='zone_2', phase='executing', alive={'marine': 9})
    text = '\n'.join(brief_combat_lines(data))
    assert 'Free: none' in text and 'Living: marine 9' in text


def test_actual_model_input_keeps_zero_home_count_after_compaction():
    env = Environment('fake', record_trajectory=False)
    try:
        observation = env.reset().to_dict()
    finally:
        env.close()
    assert observation['own_forces']['free'] == {}
    text = platform_messages('contract', observation)[1]['content']
    assert 'Free: none' in text
    assert 'Free: unknown' not in text


def test_actual_model_input_includes_army_zone_without_order_or_enemy_contact():
    env = Environment('fake', record_trajectory=False)
    try:
        observation = env.reset().to_dict()
    finally:
        env.close()
    observation['zone_state'].extend([
        dict(zone_id='zone_90', zone_role='other_expansion', known_owner='unconfirmed',
             own_contents={'units': {'marine': 13, 'viking': 4, 'hellion': 1}}),
        dict(zone_id='zone_91', zone_role='other_expansion', known_owner='unconfirmed',
             own_contents={'units': {'scv': 1}}),
    ])
    text = platform_messages('contract', observation)[1]['content']
    assert 'zone_90' in text and 'marine 13' in text and 'viking 4' in text
    assert 'zone_91' not in text


def test_failed_home_dispatch_does_not_make_existing_group_disappear():
    from sc2bench_env import ToolCall
    from sc2bench_env.adapters.llm import LLMAdapter
    from tests.helpers.drive import drive
    env = Environment('fake', record_trajectory=False)
    try:
        env.reset()
        env.backend.units['marine'] = 9
        turn = env.begin_tool_turn()
        created = turn.call(ToolCall('dispatch_group', dict(
            source='group_0', units={'marine': 9}, order='defend', target='zone_2')))
        group = created.data['effect']['group']
        turn.call(ToolCall('advance', {'seconds': 1}))
        env.step(turn.finish())
        turn = env.begin_tool_turn()
        call = ToolCall('dispatch_group', dict(source='group_0', units={'marine': 9},
                                              order='defend', target='zone_0'))
        rejected = turn.call(call)
        assert rejected.status == 'rejected'
        assert rejected.data['combat_state']['other_groups'][0]['group'] == group
        text = LLMAdapter().render_tool_result(call, rejected)
        assert 'not a complete army listing' in text
        assert 'staged no group changes' in text
        assert group + ': defend zone_2; allocated marine 9' in text
        assert 'Home available after staging: none' in text
    finally:
        env.close()


@pytest.mark.parametrize('race', ['terran', 'protoss', 'zerg'])
def test_live_system_prompt_explains_control_scope_and_queue_completion(race):
    text = render_prompt(default_prompt_parts(race))
    assert 'Local combat micro executes existing orders' in text
    assert 'not proof that its units are there' in text
    assert 'Enemy contact does not establish firing' in text
    assert 'building can still be unfinished' in text
    assert 'upgrade can still be in progress' in text

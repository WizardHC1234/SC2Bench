"""Provider encoding recovery retains intent, validation and original evidence."""
import json
from copy import deepcopy

import pytest
from agents.llm_agent.client import _parse_tool_calls
from agents.llm_agent.tools import tool_schemas
from agents.llm_agent.agent import _model_call
from agents.react_agent import create_agent
from agents.llm_agent.cli import run_episode
from sc2bench_env import Environment, EpisodeConfig


def parse(name, arguments):
    item = {'id': 'call', 'function': {'name': name, 'arguments': arguments}}
    original = deepcopy(item)
    result = _parse_tool_calls({'tool_calls': [item]}, tool_schemas())[0]
    assert item == original and result['raw'] == original
    return result


@pytest.mark.parametrize('value', ['["zone_1", "zone_15"]', '[\\"zone_1\\", \\"zone_15\\"]',
                                  '```json\n["zone_1", "zone_15"]\n```'])
def test_route_array_transport_encodings_are_repaired(value):
    result = parse('scout', json.dumps({'route': value}))
    assert result['arguments'] == {'route': ['zone_1', 'zone_15']}
    assert result['argument_repairs']['paths'] == ['$.route']


@pytest.mark.parametrize('wrap', [lambda s: '```json\n' + s + '\n```', json.dumps,
                                lambda s: s[:-1] + ',}'])
def test_outer_object_encodings_are_repaired(wrap):
    result = parse('train', wrap('{"target":"marine","count":2}'))
    assert result['arguments'] == {'target': 'marine', 'count': 2}
    assert result['argument_repairs']


def test_units_object_is_repaired_without_changing_names_or_counts():
    result = parse('dispatch_group', json.dumps({'source': 'group_0', 'order': 'attack',
                                                'target': 'zone_15', 'units': '{"marine":3}'}))
    assert result['arguments']['units'] == {'marine': 3}


@pytest.mark.parametrize('value', ['all', '"all"', 'null', 'true', '3', 'prefix ["zone_1"]'])
def test_scalar_strings_and_prose_are_not_inferred(value):
    result = parse('scout', json.dumps({'route': value}))
    assert result['arguments']['route'] == value
    assert 'argument_repairs' not in result


def test_damaged_or_ambiguous_json_is_left_invalid():
    result = parse('train', '{"target":"marine","count":')
    assert '_raw' in result['arguments']
    result = parse('train', '{"target":"marine","count":"2"}')
    assert result['arguments']['count'] == '2'
    result = parse('build', '{"target":"marine,}"}')
    assert result['arguments']['target'] == 'marine,}'


@pytest.mark.parametrize('calls', [None, ['bad'], {'name': 'train'}])
def test_bad_tool_envelopes_do_not_break_call_accounting(calls):
    assert _model_call({'tool_calls': calls})['status'] == 'ok'


def test_repaired_actions_and_original_arguments_are_recorded(tmp_path):
    results = iter([parse('scout', '{"route":"[\\"zone_1\\"]"}'),
                    parse('advance', '{"seconds":5}')])
    agent = create_agent(call_llm=lambda messages, **kwargs: {'content': '', 'model': 'test',
        'tool_calls': [next(results)]}, verbose=False)
    env = Environment('fake', record_dir=tmp_path)
    try:
        info = run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=5))
        path = env.record_path
    finally:
        env.close()
    assert info['end_reason'] == 'time_limit'
    metrics = json.loads((path/'metrics.json').read_text(encoding='utf-8'))
    repairs = metrics['model_calls'][0]['tool_argument_repairs']
    assert repairs[0]['original_arguments'] == '{"route":"[\\"zone_1\\"]"}'
    assert repairs[0]['repaired_arguments'] == {'route': ['zone_1']}


def test_repeat_queries_get_frozen_state_reminder_without_automatic_actions(tmp_path):
    seen = []
    names = iter(['query_army_state', 'query_army_state', 'advance'])
    def provider(messages, **kwargs):
        seen.append(deepcopy(messages))
        name = next(names)
        return {'content': '', 'tool_calls': [{'id': str(len(seen)), 'name': name,
                 'arguments': {'seconds': 5} if name == 'advance' else {}}]}
    env = Environment('fake', record_dir=tmp_path)
    try:
        run_episode(env, create_agent(call_llm=provider, verbose=False),
                    EpisodeConfig(game_time_limit_seconds=5))
    finally:
        env.close()
    assert 'Frozen decision snapshot' in seen[2][-1]['content']
    assert all('dispatch_group' not in m.get('content', '') for m in seen[2] if m['role'] == 'tool')

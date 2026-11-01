"""Strategic profiles distinguish force use without rewarding a preferred style."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest
from sc2bench_env.recording.engine import CompactEngineState
from sc2bench_env.recording.engine import observation_sample
from sc2bench_env.backends.base import BackendSnapshot
from s2clientprotocol import sc2api_pb2
from types import SimpleNamespace as NS
from tests.unit.test_engine_recording import sample

path=Path(__file__).resolve().parents[2]/'analysis'/'metric_design'/'force_usage.py'
spec=importlib.util.spec_from_file_location('force_usage_analysis',path)
usage=importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def group(tags,*,target='enemy',style='attack',relation='away',phase='executing'):
    return dict(member_tags=set(tags),membership_known=True,assigned=True,
                target=target,style=style,target_relation=relation,phase=phase)


def test_same_army_different_target_choice_changes_only_concentration():
    army={1:100,2:100,3:100,4:100,5:100}
    focused={'a':group([1,2]),'b':group([3],target='home',style='defend',relation='home')}
    split={'a':group([1]),'c':group([2],target='enemy_natural'),'b':focused['b']}
    a=usage.force_profile(focused,army);b=usage.force_profile(split,army)
    assert a['role_shares']==b['role_shares']
    assert a['role_shares']['outward_attack']==.4
    assert a['role_shares']['defense']==.2
    assert a['role_shares']['other_control']==.4
    assert sum(a['role_shares'].values())==pytest.approx(1)
    assert a['outward_target_concentration']==1
    assert b['outward_target_concentration']==.5
    split['c']['target']='enemy'
    assert usage.force_profile(split,army)['outward_target_concentration']==1


def test_home_clearing_withdrawal_and_no_attack_have_explicit_meanings():
    a=usage.force_profile({'a':group([1],relation='home')},{1:100})
    assert a['role_shares']['defense']==1
    assert a['outward_target_concentration'] is None
    a=usage.force_profile({'a':group([1],phase='withdrawing')},{1:100})
    assert a['role_shares']['withdrawal']==1
    assert a['outward_target_concentration'] is None


def test_deaths_are_not_misread_as_transfers_and_duplicate_membership_is_invalid():
    groups={'a':group([1,2])}
    # Tag 2 died according to replay; stale executor bookkeeping adds no value.
    a=usage.force_profile(groups,{1:100,3:100})
    assert a['role_shares']['outward_attack']==.5
    assert usage.deployment_share(groups['a'],{1:100,3:100})==.5
    assert usage.deployment_share(groups['a'],{'1':100,'3':100})==.5
    groups['b']=group([1],target='another')
    assert usage.force_profile(groups,{1:100})['reason']=='duplicate_group_membership'
    assert not usage.force_profile({},None)['available']
    assert not usage.force_profile({},{} )['available']
    assert not usage.force_profile({}, {1:100},trace_available=False)['available']
    assert not usage.ForceTrace().profile({1:100})['available']


def test_compact_membership_deltas_reconstruct_exactly_without_repeating_tags():
    recorder=CompactEngineState();trace=usage.ForceTrace()
    def frame(t,members,**kwargs):
        row=sample(t)
        row['combat_groups']={'a':dict(member_tags=members,assigned=True,style='attack',
                                     target='enemy',target_relation='away',phase='executing',**kwargs)}
        return row
    initial=recorder.update(frame(0,[1,2]),100)
    trace.apply(initial)
    assert trace.available and trace.groups['a']['member_tags']=={1,2}
    assert recorder.update(frame(1,[1,2]),100) is None
    change=recorder.update(frame(2,[2,3]),100)
    update=change['combat_updates'][0]
    assert update['members_added']==[3] and update['members_removed']==[1]
    assert 'member_tags' not in update
    trace.apply(change)
    assert trace.groups['a']['member_tags']=={2,3}
    paced=sample(3);paced['combat_groups']=None
    row=recorder.update(paced,100)
    assert row is None or 'combat_groups_removed' not in row
    assert trace.groups['a']['member_tags']=={2,3}
    closed=sample(4);closed['combat_groups']={}
    row=recorder.update(closed,100)
    trace.apply(row)
    assert trace.groups=={}


def test_missing_membership_is_not_inferred_from_requested_unit_counts():
    recorder=CompactEngineState();trace=usage.ForceTrace()
    first=sample(0)
    first['combat_groups']={'a':dict(member_tags=[1],assigned=True,style='attack',target='enemy')}
    trace.apply(recorder.update(first,100))
    unknown=deepcopy(first);unknown['game_time_seconds']=1;unknown['game_loop']=22
    unknown['combat_groups']['a'].pop('member_tags')
    trace.apply(recorder.update(unknown,100))
    assert not trace.groups['a']['membership_known']
    assert not usage.force_profile(trace.groups,{1:100})['available']


def test_extraction_keeps_membership_and_target_relation_without_full_state():
    response=sc2api_pb2.ResponseObservation()
    response.observation.game_loop=10
    bot=NS(state=NS(response_observation=response),bridge=NS(),player_id=1,_macro_tasks=[])
    info={'zone_state':[dict(zone_id='home',known_owner='self',zone_role='own_main')],
          'combat_progress':{'a':dict(style='attack',target='home',assigned=True,
                                     phase='executing',member_tags=[7,8],
                                     visible_enemy_nearby={'units':5},nearest_zone='home')}}
    snapshot=BackendSnapshot(game_time_seconds=1,info=info)
    assert observation_sample(bot,snapshot)['combat_groups'] is None
    row=observation_sample(bot,snapshot,include_combat_membership=True)
    group=row['combat_groups']['a']
    assert group['member_tags']==[7,8] and group['target_relation']=='home'
    assert 'visible_enemy_nearby' not in group and 'nearest_zone' not in group
    paced=observation_sample(bot,BackendSnapshot(game_time_seconds=2,info={'sample_phase':'realtime_pacing'}),response=response,include_combat_membership=True)
    assert paced['combat_groups'] is None

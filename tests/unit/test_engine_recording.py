"""Compact recording, sampling and branch checks without running StarCraft II."""
import asyncio
from types import SimpleNamespace as NS
import pytest
from s2clientprotocol import sc2api_pb2, raw_pb2
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.recording.engine import observation_sample, iter_engine_samples
from sc2bench_env.recording.trajectory import TrajectoryRecorder


def sample(seconds=0, *, full=False, visible=True, waiting='supply', bank=50, collected=152):
    response=sc2api_pb2.ResponseObservation()
    response.observation.game_loop=round(seconds*22.4)
    details=response.observation.score.score_details
    details.collected_minerals=collected
    details.collected_vespene=0
    details.spent_minerals=100
    common=response.observation.player_common
    common.minerals=bank
    common.vespene=0
    common.food_used=15 if full else 14
    common.food_cap=15
    if visible:
        unit=response.observation.raw_data.units.add()
        unit.tag=12345
        unit.unit_type=48
        unit.alliance=raw_pb2.Enemy
        unit.display_type=raw_pb2.Visible
    bot=NS(state=NS(game_loop=response.observation.game_loop,response_observation=response),player_id=1,
           bridge=NS(zone_registry=NS(enemies=NS(_records={}))),
           _macro_tasks=[dict(task_id='t1',action='train',target='marine',_waiting_for=waiting,_factory=lambda: None)])
    return observation_sample(bot,BackendSnapshot(game_time_seconds=seconds))


def test_periodic_counters_and_event_changes_keep_one_small_file(tmp_path):
    rec=TrajectoryRecorder(directory=tmp_path)
    for t in range(13):rec.record_engine_sample(sample(t))
    rec.finalize('Victory')
    path=tmp_path/'evaluation.jsonl.gz'
    rows=list(iter_engine_samples(path))
    values=[r for r in rows if 'counters' in r]
    assert [r['game_time_seconds'] for r in values]==list(range(13))
    assert sum('enemy_updates' in r for r in rows)==1
    assert sum('task_updates' in r for r in rows)==1
    assert {f.name for f in tmp_path.iterdir()}=={'evaluation.jsonl.gz','episode.txt','session.json','metrics.json'}
    for row in rows:
        assert not {'protobuf_base64','platform_state','bank','supply_used','supply_cap'} & set(row)
    # Native spent is not silently substituted for actual cash spending.
    assert values[0]['counters']['net_spent_minerals']==0
    assert values[0]['cash_baseline']['minerals']=={'bank':50,'collected':152}


def test_supply_duration_uses_every_observation_not_the_write_interval(tmp_path):
    rec=TrajectoryRecorder(directory=tmp_path,evaluation_interval_seconds=10)
    rec.record_engine_sample(sample(0))
    rec.record_engine_sample(sample(1,full=True))
    rec.record_engine_sample(sample(2,full=True))
    rec.record_engine_sample(sample(3,bank=100,collected=252))
    rec.finalize('Victory')
    values=[r for r in iter_engine_samples(tmp_path/'evaluation.jsonl.gz') if 'counters' in r]
    assert len(values)==2
    assert values[-1]['counters']['supply_exhausted_seconds']==2
    assert values[-1]['counters']['supply_eligible_seconds']==3
    assert values[-1]['counters']['net_spent_minerals']==50


def test_recording_interval_is_configurable(tmp_path):
    rec=TrajectoryRecorder(directory=tmp_path,evaluation_interval_seconds=2)
    for t in range(4):rec.record_engine_sample(sample(t))
    rows=list(iter_engine_samples(tmp_path/'evaluation.jsonl.gz'))
    assert [r['game_time_seconds'] for r in rows if 'counters' in r]==[0,2]


def test_shared_match_passes_interval_to_both_recorders(tmp_path):
    from sc2bench_env import EpisodeConfig
    from sc2bench_env.versus import VersusMatch
    match=VersusMatch(backend='fake',record_dir=tmp_path)
    try:
        match.reset(EpisodeConfig())
        assert all(side.recorder.evaluation_interval_seconds==1 for side in match._sides)
    finally:match.close()


def test_missing_supply_is_not_silently_treated_as_measured_zero(tmp_path):
    rec=TrajectoryRecorder(directory=tmp_path)
    first=sample(0);first['supply_used']=None
    rec.record_engine_sample(first)
    rec.record_engine_sample(sample(2))
    rec.finalize('Victory')
    values=[r for r in iter_engine_samples(tmp_path/'evaluation.jsonl.gz') if 'counters' in r]
    assert values[-1]['counters']['supply_unobserved_seconds']==2
    assert values[-1]['counters']['supply_eligible_seconds']==0


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),True])
def test_invalid_sampling_interval_is_rejected_before_backend_start(value):
    from sc2bench_env import Environment
    with pytest.raises(ValueError):Environment(evaluation_interval_seconds=value)


def test_changes_and_restored_branches_are_isolated(tmp_path):
    rec=TrajectoryRecorder()
    directory=rec.start(tmp_path,prompt='test',backend='fake',folder_name='episode')
    try:
        rec.record_engine_sample(sample(0))
        rec.record_engine_sample(sample(1,waiting='minerals'))
        cursor=rec.capture_cursor()
        trunk=directory/'evaluation.jsonl.gz'
        count=len(list(iter_engine_samples(trunk)))
        rec.suspend_writes()
        rec.record_engine_sample(sample(9))
        assert len(list(iter_engine_samples(trunk)))==count
        rec.restore_cursor(cursor)
        branch=rec.open_branch('b',metadata={})
        rec.record_engine_sample(sample(2,visible=False))
        rows=list(iter_engine_samples(branch/'evaluation.jsonl.gz'))
        assert rows[0]['branch_id']=='b'
        assert rows[0]['counters']['supply_eligible_seconds']==2
        assert len(list(iter_engine_samples(trunk)))==count
    finally:rec.stop_console_log()


def test_structured_tool_result_kept_without_duplicate_observation(tmp_path):
    rec=TrajectoryRecorder(directory=tmp_path,config={'race':'terran'})
    rec.record_tool_event(kind='read',call={'name':'query_map_overview','arguments':{}},
                          result={'status':'ok','data':{'zones':[{'zone_id':'z1'}]}},
                          game_time_seconds=5,decision_index=0,turn_id='turn0')
    rows=list(iter_engine_samples(tmp_path/'evaluation.jsonl.gz'))
    event=rows[0]['event']
    assert event['result']['data']['zones'][0]['zone_id']=='z1'
    assert event['turn_id']=='turn0'
    assert not {'data','rendered_result','call','observation'} & set(event)
    rec.finalize('Victory')
    assert list(iter_engine_samples(tmp_path/'evaluation.jsonl.gz'))[-1]['event']['type']=='end'


def test_empty_replay_export_remains_retryable(tmp_path):
    from sc2bench_env.backends.sharpy.bot import BenchBot
    path=tmp_path/'replay.SC2Replay'
    class Client:
        async def save_replay(self,target):path.write_bytes(b'')
    bot=object.__new__(BenchBot)
    bot.bridge=NS(replay_path=path)
    bot._client=Client();bot._replay_saved=False
    asyncio.run(bot._save_replay_once('before leave'))
    assert not bot._replay_saved
    async def valid_save(target):path.write_bytes(b'nonempty-replay-test')
    bot._client.save_replay=valid_save
    asyncio.run(bot._save_replay_once('on game end'))
    assert bot._replay_saved


def test_binding_a_new_sink_does_not_replay_a_previous_episode():
    from sc2bench_env.backends.sharpy.backend import SharpyBackend
    backend=SharpyBackend();backend._bridge.last_evaluation_sample=sample()
    records=[];backend.set_evaluation_sink(records.append)
    assert records==[]
    backend.record_current_evaluation()
    assert len(records)==1

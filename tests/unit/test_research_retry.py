"""Research retry uses current game facts rather than historical queue entry."""
import pytest
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import TaskManager
from tests.helpers.drive import submit_calls


@pytest.mark.parametrize('race,target',[
    ('terran','infantry_weapons_1'),('protoss','ground_weapons_1'),('zerg','melee_attacks_1')])
@pytest.mark.parametrize('status',['lost','queued','finished'])
def test_research_retry_distinguishes_lost_queue_and_duplicates(race,target,status):
    manager=TaskManager(race=race)
    calls=[{'name':'research','arguments':{'target':target}}, {'name':'advance','arguments':{'seconds':1}}]
    submit_calls(manager,calls,game_time=0,race=race)
    manager.active_demands()[0].state=DemandState.COMPLETED
    assert manager.research_summary([],[])=={target:'interrupted'}
    receipt=submit_calls(manager,calls,game_time=5,race=race,
        known_upgrades={target} if status=='finished' else set(),
        researching={target} if status=='queued' else set())[0]
    assert receipt.result==('accepted' if status=='lost' else 'idempotent_noop')
    summary=manager.research_summary([target] if status=='finished' else [],[target] if status=='queued' else [])
    assert summary[target]=={'lost':'waiting_to_start','queued':'in_progress','finished':'completed'}[status]
    if status!='lost':assert receipt.reason==('already_researched' if status=='finished' else 'research_already_queued')

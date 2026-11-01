"""Compare placement query variants against actual native Queen tumor creation."""
import json
import os
from collections import Counter
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_tumor_query_matches_a_successful_native_cast(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.data import ActionResult
    from sc2.position import Point2
    from s2clientprotocol import query_pb2 as q
    from sharpy.managers.core.roles import UnitTask
    state={'phase':0,'finish':False,'queries':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if state['phase']==0:
            await ai.client.debug_create_unit([[U.QUEEN,1,ai.start_location.towards(ai.game_info.map_center,6),ai.player_id]])
            state['phase']=1;return
        queens=ai.units(U.QUEEN)
        if not queens:return
        queen=queens.first;act.roles.set_task(UnitTask.Reserved,queen)
        if state['phase']==1:
            await ai.client.debug_set_unit_value([queen.tag],1,100);state['phase']=2;return
        if state['phase']==2:
            points=[Point2((int(queen.position.x)+x+.5,int(queen.position.y)+y+.5))
                for x in range(-5,6) for y in range(-5,6) if x*x+y*y>=4]
            points=[p for p in points if ai.has_creep(p) and ai.in_pathing_grid(p)
                and all(u.distance_to(p)>u.radius+.6 for u in ai.all_units if not u.is_flying)]
            assert points,'scene has no clear creep point'
            exact=A.BUILD_CREEPTUMOR_QUEEN.value
            mapped=ai._game_data.abilities[exact].id.value
            successful=[]
            for ability in sorted({exact,mapped}):
                for tag in (0,queen.tag):
                    request=[q.RequestQueryBuildingPlacement(ability_id=ability,target_pos=p.as_Point2D,placing_unit_tag=tag) for p in points]
                    result=await ai.client._execute(query=q.RequestQuery(placements=request,ignore_resource_requirements=True))
                    good=[p for p,r in zip(points,result.query.placements) if r.result==1]
                    state['queries'].append(dict(ability=ability,caster=bool(tag),counts=dict(Counter(ActionResult(r.result).name for r in result.query.placements)),valid=len(good)))
                    if ability==exact and tag:successful=good
            point=successful[0] if successful else points[0]
            state.update(point=list(point),energy_before=float(queen.energy),cast_time=float(ai.time))
            queen(A.BUILD_CREEPTUMOR_QUEEN,point);state['phase']=3
        tumors=ai.structures.of_type({U.CREEPTUMOR,U.CREEPTUMORQUEEN,U.CREEPTUMORBURROWED})
        state['created']=len(tumors)
        if tumors:state['tumor_positions']=[list(t.position) for t in tumors]
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/tumor_query')
    try:
        env.reset(EpisodeConfig(race='zerg',opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=60))
        for _ in range(14):drive(env,[{'name':'advance','arguments':{'seconds':1}}])
        Path('analysis/platform_audit/tumor_query_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('created',0)>0,state
        state['finish']=True;drive(env,[{'name':'advance','arguments':{'seconds':2}}])
    finally:
        env.close()

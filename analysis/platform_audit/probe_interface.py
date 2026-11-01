"""Read-only interface checks during a normal, complete ReAct episode.

Additional queries run locally, never enter model context or episode tool logs.
Only check counts and discrepancies are saved, not another observation stream.
"""
import json
import sys
from collections import Counter, OrderedDict
from copy import deepcopy
from pathlib import Path

from sc2bench_env import Environment
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.tools import ToolCall
from sc2bench_env.runtime.query_tools import run_query
from sc2bench_env.runtime.tool_turn import ToolTurn
from tests.helpers.obs_invariants import collect_obs_mismatches


def expected_native_supply(ai, common):
    """Match python-sc2's Zerg half-supply correction, not the rounded raw field."""
    from sc2.data import Race
    from sc2.ids.unit_typeid import UnitTypeId as U
    half_supply = {U.ZERGLING,U.ZERGLINGBURROWED,U.BANELING,U.BANELINGBURROWED,U.BANELINGCOCOON}
    correction = sum(u.type_id in half_supply for u in ai.units) % 2 if ai.race == Race.Zerg else 0
    return common.food_used + correction


class InterfaceProbe:
    def __init__(self):
        self.counts = Counter()
        self.issues = []
        self.raw = OrderedDict()
        self.tank_states = {}
        self.tank_changes = {}
        self.submission_failures = []
        self.last_time = 0.0
        self.unit_types = {}
        self.failure_examples = Counter()
        self.attack_centers = {}
        self.placement_failures = []
        self.placement_examples = Counter()
        self.current_ai = None
        self.destination = None
        self.pending_site_queries = []
        self.cast_failures = []
        self.cast_commands = {}
        self.tank_release_causes = {}
        self.delayed_failures = []
        self.delayed_examples = Counter()
        self.seen_native_errors = set()

    def checkpoint(self):
        if self.destination is None:
            return
        try:
            self.destination.write_text(json.dumps(dict(running=True,seconds=self.last_time,
                checks=dict(self.counts),issues=self.issues,submission_failures=self.submission_failures,
                placement_failures=self.placement_failures,cast_failures=self.cast_failures,
                delayed_failures=self.delayed_failures),ensure_ascii=False,indent=2),encoding='utf8')
        except (OSError,TypeError) as error:
            self.issues.append(dict(kind='diagnostic_checkpoint',reason=repr(error)))

    def command_context(self, action):
        """Bounded rejection evidence; never enters the model's observation."""
        from sc2.ids.ability_id import AbilityId
        ai = self.current_ai
        if ai is None or action is None:
            return {}
        units = {u.tag: u for u in list(ai.units) + list(ai.structures) + list(ai.all_enemy_units)}
        raw_units = {u.tag:u for u in ai.state.response_observation.observation.raw_data.units}
        def unit_state(unit):
            if unit is None:
                return None
            available = getattr(getattr(ai.knowledge, 'cooldown_manager', None), 'available_dict', {})
            return dict(tag=unit.tag, type=unit.type_id.name, position=list(unit.position),
                owner=int(unit.owner_id),
                flying=unit.is_flying, ready=unit.is_ready, memory=unit.is_memory,
                health=float(unit.health),shield=float(unit.shield),
                buffs=sorted(b.name for b in unit.buffs),
                abilities=[a.name for a in available.get(unit.tag, [])],
                orders=[dict(ability=o.ability.id.name,
                    target=[o.target.x,o.target.y] if hasattr(o.target, 'x') else o.target) for o in unit.orders])
        target_raw=raw_units.get(action.target_unit_tag) if action.HasField('target_unit_tag') else None
        context = dict(queue=action.queue_command,
            actors=[unit_state(units.get(tag)) for tag in action.unit_tags],
            target_unit=unit_state(units.get(action.target_unit_tag)) if action.HasField('target_unit_tag') else None,
            target_raw=dict(type_id=target_raw.unit_type,owner=int(target_raw.owner),flying=target_raw.is_flying,
                display_type=target_raw.display_type,cloak=target_raw.cloak,progress=target_raw.build_progress,
                health=float(target_raw.health) if target_raw.HasField('health') else None,
                shields=float(target_raw.shield) if target_raw.HasField('shield') else None)
                if target_raw is not None else None,
            target_position=[action.target_world_space_pos.x,action.target_world_space_pos.y]
                if action.HasField('target_world_space_pos') else None)
        ability = AbilityId._value2member_map_.get(action.ability_id)
        if (action.ability_id == 1442 or 'UNLOADALLAT_' in getattr(ability, 'name', '')) and action.HasField('target_world_space_pos'):
            from sc2.position import Point2
            point=Point2((action.target_world_space_pos.x,action.target_world_space_pos.y))
            nearby=sorted([u for u in ai.all_units if not u.is_flying and u.distance_to(point)<4],
                          key=lambda u:u.distance_to(point))[:12]
            context['landing']=dict(visible=bool(ai.is_visible(point)),walkable=bool(ai.in_pathing_grid(point)),
                terrain=ai.get_terrain_height(point),nearby=[dict(tag=u.tag,type=u.type_id.name,
                    position=list(u.position),radius=u.radius,distance=u.distance_to(point)) for u in nearby])
        return context

    def check(self, ok, kind, time, **details):
        self.counts[kind] += 1
        if not ok:
            self.issues.append(dict(kind=kind, seconds=time, **details))

    def observation(self, env, obs):
        data = obs.to_dict()
        now = obs.game_time_seconds
        for error in collect_obs_mismatches(obs):
            self.check(False, "observation_invariants", now, reason=error)
        self.counts["observations"] += 1
        expected = self.raw.get(round(now * 22.4))
        if expected:
            for key in ("minerals", "vespene", "supply_used", "supply_cap"):
                self.check(getattr(obs.resources, key) == expected[key], "native_economy", now,
                           field=key, observed=getattr(obs.resources, key), native=expected[key])
        for field in ("minerals", "vespene", "supply_used", "supply_cap"):
            self.check(getattr(obs.economy, field) == getattr(obs.resources, field), "economy_projection", now, field=field)
        army = obs.own_forces.army
        totals = Counter()
        for name, row in obs.combat.items():
            totals.update(row.get("alive") or {})
        self.check(all(n <= army.get(name, 0) for name, n in totals.items()), "group_inventory", now,
                   grouped=dict(totals), inventory=army)
        for row in obs.production or []:
            capacity, free = row.get("capacity"), row.get("free_slots")
            if capacity is not None and free is not None:
                self.check(0 <= free <= capacity, "production_slots", now, row=row)
        queries = [("query_map_overview", {}), ("query_zone_state", {}),
                   ("query_production_state", {}), ("query_army_state", {})]
        queries += [("query_production_state", {"targets": ["unknown_audit_target"]}),
                    ("query_army_state", {"groups": ["unknown_audit_group"]}),
                    ("query_zone_state", {"zone_ids": ["unknown_audit_zone"]})]
        queries += [("query_zone_state", {"zone_ids": [z]}) for z in obs.zones]
        queries += [("query_army_state", {"groups": [g]}) for g in obs.combat]
        queries += [("query_production_state", {"actions": [row["action"]], "targets": [row.get("to") or row["target"]]})
                    for row in obs.production_priority]
        topology = {z["zone_id"]: z for z in obs.map_topology.get("zones", [])}
        if topology:
            first = next(iter(topology))
            queries += [("query_route", {"from_zone": first, "to_zone": z}) for z in topology]
        before = deepcopy(data)
        for name, args in queries:
            result = run_query(name, args, observation=data, race=obs.race)
            self.counts["query:" + name] += 1
            self.check(result.get("as_of_game_time") == now, "query_time", now, tool=name)
            text = LLMAdapter(race=obs.race).render_tool_result(ToolCall(name, args), result)
            self.check(bool(text.strip()) and "Traceback" not in text, "query_rendering", now, tool=name)
            if name == "query_zone_state":
                wanted = args.get("zone_ids", obs.zones)
                self.check(result["zones"] == [r for r in obs.zone_state if r["zone_id"] in wanted],
                           "zone_query_projection", now, arguments=args)
            if name == "query_army_state":
                wanted = args.get("groups", list(obs.combat))
                total = sum(g != "group_0" for g in obs.combat)
                self.check(result.get("outbound_group_count") == total and f"Outbound groups: {total}" in text,
                           "army_query_total", now, arguments=args)
                for group in wanted:
                    if group not in obs.combat:
                        self.check(group in result.get("unknown_groups", []), "unknown_group", now)
                        continue
                    self.check(result["groups"][group].get("alive") == obs.combat[group].get("alive"),
                               "army_query_projection", now, group=group)
            if name == "query_production_state":
                wanted = args.get("targets", [])
                if "unknown_audit_target" in wanted:
                    self.check(result.get("unknown_targets") == wanted and "Unknown production targets:" in text,
                               "unknown_production_target", now)
                action_filter = args.get("actions", [])
                expected_work = [r for r in obs.production_priority
                                 if (not action_filter or r["action"] in action_filter)
                                 and (not wanted or r["target"] in wanted or r.get("to") in wanted)]
                self.check(result["persistent_work"] == expected_work, "production_query_projection", now,
                           arguments=args)
        self.check(data == before, "query_read_only", now)

    def install(self):
        from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
        _ensure_runtime_paths()
        from sc2bench_env.backends.sharpy import bot
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.ids.ability_id import AbilityId
        from sc2.client import Client
        from sc2bench_env.backends.sharpy.acts import ActCombatMission
        from sc2bench_env.backends.sharpy.acts import _ObservedAbilityAct
        queue_cast = _ObservedAbilityAct._queue_cast
        def queued(act,caster,*args,**kwargs):
            self.cast_commands[caster.tag]=[]
            result=queue_cast(act,caster,*args,**kwargs)
            if act._pending_cast is None:self.cast_commands.pop(caster.tag,None)
            return result
        _ObservedAbilityAct._queue_cast=queued
        send_command=bot.BenchBot.do
        def send(ai,action,*args,**kwargs):
            result=send_command(ai,action,*args,**kwargs)
            actor=getattr(action,'unit',None)
            if actor is not None and not result and getattr(action,'ability',None) in {
                    AbilityId.ATTACK,AbilityId.ATTACK_ATTACK}:
                available=getattr(getattr(ai.knowledge,'cooldown_manager',None),'available_dict',{})
                if actor.tag in available and not {
                        AbilityId.ATTACK,AbilityId.ATTACK_ATTACK}.intersection(available[actor.tag]):
                    self.counts['unavailable_native_attack_held']+=1
            unit=getattr(action,'unit',None)
            if result and unit is not None and unit.tag in self.cast_commands:
                target=action.target
                row=dict(seconds=float(ai.time),ability=action.ability.name,
                    role=int(ai.knowledge.roles.unit_role(unit)),
                    target=getattr(target,'tag',None) if hasattr(target,'tag') else
                        [target.x,target.y] if hasattr(target,'x') else target)
                from sc2bench_env.backends.sharpy.combat_styles import visible_combat_enemies
                allies=[u for u in ai.units if u.tag!=unit.tag and unit.distance_to(u)<=7]
                row['local_contact']=[dict(tag=e.tag,type=e.type_id.name,distance=unit.distance_to(e))
                    for e in visible_combat_enemies(ai,unit,allies,unit_values=ai.knowledge.unit_values)][:12]
                self.cast_commands[unit.tag].append(row)
                del self.cast_commands[unit.tag][:-16]
            return result
        bot.BenchBot.do=send
        observe_cast = _ObservedAbilityAct._observe_cast
        def cast_observed(act):
            pending=act._pending_cast
            result=observe_cast(act)
            if pending is not None and act.failure_reason and len(self.cast_failures)<12:
                def state(tag):
                    unit=next((u for u in act.ai.all_own_units if u.tag==tag),None)
                    if unit is None:return None
                    return dict(tag=unit.tag,type=unit.type_id.name,position=list(unit.position),
                        energy=float(unit.energy),progress=float(unit.build_progress),
                        buffs=[b.name for b in unit.buffs],orders=[dict(ability=o.ability.id.name,
                        target=[o.target.x,o.target.y] if hasattr(o.target,'x') else o.target) for o in unit.orders])
                self.cast_failures.append(dict(seconds=float(act.ai.time),ability=pending['ability'].name,
                    reason=act.failure_reason,queued_at=pending['time'],energy_before=pending['energy'],
                    caster=state(pending['caster']),target=state(pending['target']),
                    nearby_ground=[dict(tag=u.tag,type=u.type_id.name,position=list(u.position),radius=float(u.radius),
                        role=int(act.ai.knowledge.roles.unit_role(u)))
                        for u in act.ai.all_own_units if not u.is_flying
                        and any(u.distance_to(p.position)<13 for p in act.ai.all_own_units
                                if p.tag==pending['caster'])][:60],
                    commands=self.cast_commands.get(pending['caster'],[])))
                self.checkpoint()
            if pending is not None and act._pending_cast is None:
                self.cast_commands.pop(pending['caster'],None)
            return result
        _ObservedAbilityAct._observe_cast=cast_observed
        from sharpy.combat.group_combat_manager import GroupCombatManager
        from sharpy.plans.acts.build_location_recovery import BuildLocationRecovery
        original_rejection=BuildLocationRecovery._observe_build_rejection
        def observe_rejection(act):
            order=act._last_build_order
            result=original_rejection(act)
            if result and order is not None:
                errors=[e for e in act.ai.state.action_errors
                        if e.unit_tag==order['worker'] and e.ability_id in order['abilities']]
                key=(act.unit_type.name,'native_rejection')
                if self.placement_examples[key]<3:
                    worker=act.cache.by_tag(order['worker'])
                    scouts=[t.get('task_id') for t in getattr(act.ai,'_macro_tasks',())
                            if t.get('action')=='scout' and getattr(t.get('_act'),'_scout_tag',None)==order['worker']]
                    row=dict(seconds=float(act.ai.time),type=act.unit_type.name,position=list(order['point']),
                        native_errors=[int(e.result) for e in errors],worker=order['worker'],
                        worker_position=list(worker.position) if worker is not None else None,
                        worker_orders=[dict(ability=o.ability.id.name,
                            target=[o.target.x,o.target.y] if hasattr(o.target,'x') else o.target)
                            for o in worker.orders] if worker is not None else [],scout_tasks=scouts)
                    if worker is not None:
                        nearby = sorted((u for u in act.ai.all_units if u.tag != worker.tag
                                         and not u.is_flying and u.distance_to(worker) < 14),
                                        key=lambda u:u.distance_to(worker))[:20]
                        row['nearby_ground'] = [dict(tag=u.tag,type=u.type_id.name,
                            position=list(u.position),radius=float(u.radius),
                            owner=int(u.owner_id),structure=bool(u.is_structure)) for u in nearby]
                    self.placement_failures.append(row)
                    ability=act.ai._game_data.units[act.unit_type.value].creation_ability
                    self.pending_site_queries.append((row,worker.position if worker else None,order['point'],ability))
                    self.placement_examples[key]+=1
            return result
        BuildLocationRecovery._observe_build_rejection=observe_rejection
        original_position = BuildLocationRecovery.checked_position
        async def checked_position(act, position, **kwargs):
            result = await original_position(act, position, **kwargs)
            if result is None and self.placement_examples[act.unit_type.name] < 3:
                ability = act.ai._game_data.units[act.unit_type.value].creation_ability
                details = await act.ai.client.query_building_placement(ability, [position], ignore_resources=True)
                self.placement_failures.append(dict(seconds=float(act.ai.time), type=act.unit_type.name,
                    position=list(position), results=[r.name for r in details],
                    visible=bool(act.ai.is_visible(position)), creep=bool(act.ai.has_creep(position))))
                self.placement_examples[act.unit_type.name] += 1
                self.checkpoint()
            return result
        BuildLocationRecovery.checked_position = checked_position
        original_drive=ActCombatMission._drive_combat
        async def drive(mission,units,target,move_type):
            if mission.style=='attack':
                _,zone=mission._resolve_zone()
                if zone is not None:
                    self.check(target.distance_to(zone.center_location)<.01,'attack_uses_zone_center',mission.ai.time)
                    self.attack_centers[mission.zone_id]=dict(zone_center=[float(v) for v in zone.center_location],
                        target=[float(v) for v in target],seconds=float(mission.ai.time))
            return await original_drive(mission,units,target,move_type)
        ActCombatMission._drive_combat=drive
        original_enemy_groups=GroupCombatManager.group_enemy_units
        def enemy_groups(combat):
            groups=original_enemy_groups(combat)
            self.check(all(u.is_visible and not u.is_memory and not u.is_snapshot
                           for group in groups for u in group.units),'combat_centers_are_current',combat.ai.time)
            return groups
        GroupCombatManager.group_enemy_units=enemy_groups
        original_execute=Client._execute
        async def execute(client,**kwargs):
            response=await original_execute(client,**kwargs)
            request=kwargs.get('action')
            if request is not None:
                actions=list(request.actions)
                results=list(response.action.result)
                for index,result in enumerate(results):
                    self.counts['native_submission_results']+=1
                    if result==1:
                        continue
                    from sc2.data import ActionResult
                    action=actions[index].action_raw.unit_command if len(results)==len(actions) else None
                    ability=action.ability_id if action is not None else None
                    label=AbilityId(ability).name if ability in AbilityId._value2member_map_ else str(ability)
                    self.counts['native_rejection:'+label+':'+ActionResult(result).name]+=1
                    types=sorted({self.unit_types.get(tag,'unknown') for tag in action.unit_tags}) if action is not None else []
                    key=(label,ActionResult(result).name,tuple(types))
                    if self.failure_examples[key]<3:
                        try:
                            context = self.command_context(action)
                        except Exception as error:
                            context = dict(diagnostic_error=repr(error))
                        self.submission_failures.append(dict(seconds=self.last_time,ability_id=ability,
                            result=int(result),unit_tags=list(action.unit_tags) if action is not None else [],types=types,
                            context=context))
                        self.failure_examples[key]+=1
                        self.checkpoint()
            return response
        Client._execute=execute
        import os
        if os.environ.get('SC2BENCH_AUDIT_NORMAL_SPEED')=='1':
            import asyncio
            import time
            original_step_request=Client.step
            async def paced_step(client,step_size=None):
                frames=int(client.game_step if step_size is None else step_size)
                started=time.perf_counter()
                result=await original_step_request(client,step_size)
                await asyncio.sleep(max(0,frames/22.4-(time.perf_counter()-started)))
                self.counts['normal_speed_steps']+=1
                return result
            Client.step=paced_step
        if os.environ.get('SC2BENCH_AUDIT_CAMERA')=='1':
            original_step=bot.BenchBot._run_platform_step
            last_camera=[-100]
            async def camera_step(ai,iteration):
                await original_step(ai,iteration)
                if ai.time-last_camera[0]<8:
                    return
                army=[u for u in ai.units if u.type_id not in {UnitTypeId.SCV,UnitTypeId.PROBE,UnitTypeId.DRONE} and u.can_attack]
                if army:
                    enemies=[e for e in ai.all_enemy_units if e.is_visible and not e.is_memory and not e.is_snapshot]
                    contact=[u for u in army if any(u.distance_to(e)<14 for e in enemies)]
                    pool=contact or [u for u in army if u.tag in getattr(ai,'bench_combat_tags',set())] or army
                    from sc2.position import Point2
                    center=Point2((sum(u.position.x for u in pool)/len(pool),sum(u.position.y for u in pool)/len(pool)))
                    anchor=min(pool,key=lambda u:u.distance_to(center))
                    await ai.client.move_camera(anchor.position)
                    last_camera[0]=ai.time
            bot.BenchBot._run_platform_step=camera_step
        original_diagnostic_step=bot.BenchBot._run_platform_step
        async def diagnostic_step(ai,iteration):
            await original_diagnostic_step(ai,iteration)
            pending,self.pending_site_queries=self.pending_site_queries,[]
            for row,start,end,ability in pending:
                try:
                    row['placement_results']=[r.name for r in await ai.client.query_building_placement(ability,[end],ignore_resources=True)]
                    row['path_distance']=await ai.client.query_pathing(start,end) if start is not None else None
                except Exception as error:
                    row['diagnostic_error']=repr(error)
            if pending:self.checkpoint()
        bot.BenchBot._run_platform_step=diagnostic_step
        from sharpy.combat import NoAction
        from sc2bench_env.backends.sharpy.micro import MissionMicroRules, _form_change_pending
        original_guard=MissionMicroRules.guard_action
        def guard(rules,unit,command,*,group_units=()):
            actual=original_guard(rules,unit,command,group_units=group_units)
            from sc2bench_env.backends.sharpy.combat_styles import visible_combat_enemies
            if actual.ability==AbilityId.UNSIEGE_UNSIEGE:
                held=rules._builder_yields.get(unit.tag)
                self.tank_release_causes[unit.tag]=dict(seconds=float(rules.ai.time),
                    position=list(unit.position),station=list(rules.return_point) if rules.return_point is not None else None,
                    yield_actor=held[0] if held is not None else None,
                    contact=[e.tag for e in visible_combat_enemies(rules.ai,unit,group_units,unit_values=rules.unit_values)],
                    home=unit.tag in getattr(rules.ai,'bench_group0_tags',()))
            if not _form_change_pending(unit) and visible_combat_enemies(
                    rules.ai,unit,group_units,unit_values=rules.unit_values):
                self.check(actual is command,'visible_combat_priority',rules.ai.time)
            if rules.hold_position and rules.boundary is not None and rules.return_point is not None:
                self.counts['defend_micro_commands']+=1
                inside=rules.boundary(unit.position)
                from sc2bench_env.backends.sharpy.holding import overlaps_townhall_site
                site=getattr(rules,'reserved_base_center',None)
                clearing=(site is not None and not unit.is_flying
                          and overlaps_townhall_site(unit.position,site,float(getattr(unit,'radius',.75))))
                passage = getattr(rules, '_builder_yields', {}).get(unit.tag)
                yielding = passage is not None and not visible_combat_enemies(
                    rules.ai,unit,group_units,unit_values=rules.unit_values)
                if yielding:
                    self.counts['builder_passage_commands'] += 1
                    if actual.position is not None:
                        self.check(actual.position.distance_to(passage[1]) < .01,
                                   'builder_passage_destination',rules.ai.time)
                if clearing and not visible_combat_enemies(rules.ai,unit,group_units,unit_values=rules.unit_values):
                    self.counts['station_site_clearance']+=1
                settled=getattr(rules,'_settled_at',{}).get(unit.tag)
                keep_idle=(not getattr(rules,'compact_station',False) or getattr(unit,'is_flying',False)
                           or (settled is not None and unit.distance_to(settled)<=.5 and rules._stationary(unit)))
                if isinstance(command,NoAction) and inside and not clearing and not yielding and keep_idle:
                    self.check(isinstance(actual,NoAction),'defend_no_action_preserved',rules.ai.time)
                target=getattr(command,'target',None)
                if (not _form_change_pending(unit) and command.is_attack and target is not None
                        and hasattr(target,'type_id') and not target.is_structure):
                    attack_range=rules.unit_values.real_range(unit,target)
                    if attack_range>0 and unit.distance_to(target)<=attack_range:
                        self.check(actual is command,'defend_in_range_shot_preserved',rules.ai.time)
                if actual.ability==AbilityId.HOLDPOSITION_HOLD:
                    self.counts['defend_hold_orders']+=1
            return actual
        MissionMicroRules.guard_action=guard
        original_read = bot.read_snapshot
        def read(ai, *args, **kwargs):
            result = original_read(ai, *args, **kwargs)
            self.current_ai = ai
            self.last_time=result[0].game_time_seconds
            self.unit_types={u.tag:u.type_id.name for u in list(ai.units)+list(ai.structures)}
            from s2clientprotocol.raw_pb2 import ActionRawUnitCommand
            for error in ai.state.action_errors:
                identity=(float(ai.time),int(error.unit_tag),int(error.ability_id),int(error.result))
                if identity in self.seen_native_errors:continue
                self.seen_native_errors.add(identity)
                unit=next((u for u in ai.all_own_units if u.tag==error.unit_tag),None)
                kind=unit.type_id.name if unit is not None else 'unknown'
                key=(int(error.ability_id),int(error.result),kind)
                if self.delayed_examples[key]>=3:continue
                command=ActionRawUnitCommand(ability_id=error.ability_id,unit_tags=[error.unit_tag])
                context=self.command_context(command)
                if unit is not None:
                    context.update(terrain=ai.get_terrain_height(unit.position),walkable=bool(ai.in_pathing_grid(unit.position)),
                        nearby_ground=[dict(tag=u.tag,type=u.type_id.name,position=list(u.position),radius=float(u.radius),
                            structure=bool(u.is_structure)) for u in ai.all_own_units
                            if u.tag!=unit.tag and not u.is_flying and u.distance_to(unit)<5])
                self.delayed_failures.append(dict(seconds=float(ai.time),ability_id=int(error.ability_id),
                    result=int(error.result),context=context))
                self.delayed_examples[key]+=1
                self.checkpoint()
            from sharpy.managers.core.roles import UnitTask
            scout_owners={}
            for task in getattr(ai,'_macro_tasks',()):
                act=task.get('_act')
                if (task.get('action')=='scout' and act is not None and not act._done
                        and not act.failure_reason and act._scout_tag is not None):
                    scout_owners[act._scout_tag]=task.get('task_id')
                    worker=ai.workers.find_by_tag(act._scout_tag)
                    if worker is not None:
                        role=ai.knowledge.roles.unit_role(worker)
                        details={}
                        if role != UnitTask.Scouting:
                            details=dict(role=getattr(role,'name',str(role)),position=list(worker.position),task_id=task.get('task_id'),
                                orders=[dict(ability=o.ability.id.name,
                                    target=[o.target.x,o.target.y] if hasattr(o.target,'x') else o.target) for o in worker.orders],
                                builders=[dict(target=t.get('target'),task_id=t.get('task_id'))
                                    for t in getattr(ai,'_macro_tasks',())
                                    if getattr(t.get('_act'),'builder_tag',None)==worker.tag],
                                nearby_enemies=[dict(type=e.type_id.name,position=list(e.position))
                                    for e in ai.all_enemy_units if e.is_visible and not e.is_memory
                                    and not e.is_snapshot and worker.distance_to(e)<12])
                        self.check(role==UnitTask.Scouting,
                            'scout_worker_reserved',ai.time,worker=worker.tag,**details)
            for task in getattr(ai,'_macro_tasks',()):
                builder=getattr(task.get('_act'),'builder_tag',None)
                if task.get('action')=='build' and builder is not None:
                    self.check(builder not in scout_owners,'exclusive_worker_ownership',ai.time,worker=builder)
                    worker=ai.workers.find_by_tag(builder)
                    if worker is not None:
                        role=ai.knowledge.roles.unit_role(worker)
                        self.check(role!=UnitTask.Defending,'builder_not_reassigned_to_defense',
                            ai.time,worker=builder,role=int(role),target=task.get('target'))
            pool=getattr(ai,'bench_group0_tags',set())
            station=getattr(ai,'bench_group0_home_point',None)
            from sc2bench_env.backends.sharpy.holding import high_ground_side
            home_zone=getattr(ai,'bench_group0_home_zone',None)
            active={}
            for unit in ai.units:
                if unit.tag not in pool or unit.type_id not in {UnitTypeId.SIEGETANK,UnitTypeId.SIEGETANKSIEGED} or station is None:
                    continue
                previous=self.tank_states.get(unit.tag)
                if previous is None:
                    self.tank_changes[unit.tag]=[(ai.time,unit.type_id,unit.position,station)]
                if previous is not None and previous['type']!=unit.type_id:
                    self.counts['home_tank_form_changes']+=1
                    changes=self.tank_changes.setdefault(unit.tag,[])
                    changes.append((ai.time,unit.type_id,unit.position,station))
                    del changes[:-3]
                    if (len(changes)==3 and changes[0][1]==changes[2][1]
                            and changes[2][0]-changes[0][0]<10
                            and all(p.distance_to(s)<=5 for _,_,p,s in changes)
                            and all(high_ground_side(ai,home_zone,s,p) for _,_,p,s in changes)
                            and all(s.distance_to(station)<1 for _,_,_,s in changes)):
                        self.check(False,'home_tank_rapid_station_cycle',ai.time,tag=unit.tag,
                                   times=[c[0] for c in changes])
                    if (unit.type_id==UnitTypeId.SIEGETANK and unit.distance_to(station)<=5
                            and high_ground_side(ai,home_zone,station,unit.position)
                            and previous['station'].distance_to(station)<1):
                        enemies=[e for e in ai.all_enemy_units if e.is_visible and not e.is_snapshot and not e.is_memory
                                 and unit.distance_to(e)<=15]
                        self.check(bool(enemies),'home_tank_idle_unsiege',ai.time,tag=unit.tag,
                                   position=list(unit.position),station=list(station),
                                   command_context=self.tank_release_causes.get(unit.tag))
                active[unit.tag]=dict(type=unit.type_id,station=station)
            self.tank_states=active
            response = getattr(getattr(ai, "state", None), "response_observation", None)
            if response is not None:
                common = response.observation.player_common
                self.raw[round(result[0].game_time_seconds * 22.4)] = dict(
                    minerals=common.minerals, vespene=common.vespene,
                    supply_used=expected_native_supply(ai,common), supply_cap=common.food_cap)
                while len(self.raw) > 128:
                    self.raw.popitem(last=False)
            return result
        bot.read_snapshot = read
        original_build = Environment._build_observation
        def build(env, snapshot):
            obs = original_build(env, snapshot)
            self.observation(env, obs)
            return obs
        Environment._build_observation = build
        original_render = LLMAdapter.render_input
        def render(adapter, request):
            text = original_render(adapter, request)
            self.counts["model_inputs"] += 1
            events = request.feedback.events if request.feedback else []
            for event in events:
                if event.get("event_id") is not None and event.get("type") != "demand_accepted":
                    self.check(f"[#{event['event_id']} @" in text, "feedback_event_visible",
                               request.observation.game_time_seconds, event=event)
            return text
        LLMAdapter.render_input = render
        original_call = ToolTurn.call
        def call(turn, tool_call):
            result = original_call(turn, tool_call)
            self.counts["actual_tool:" + tool_call.name + ":" + result.status] += 1
            if result.status in {"staged", "no_op", "rejected"}:
                self.check(result.data.get("execution") != "completed", "receipt_not_completion", turn._game_time,
                           tool=tool_call.name, status=result.status)
            return result
        ToolTurn.call = call


def main():
    destination = Path(sys.argv[1])
    probe = InterfaceProbe()
    probe.destination = destination
    probe.install()
    from agents.react_agent.cli import main as run
    code = 1
    try:
        code = run(sys.argv[2:])
        return code
    finally:
        destination.write_text(json.dumps(dict(exit_code=code, checks=dict(probe.counts), issues=probe.issues,
                                               submission_failures=probe.submission_failures,attack_centers=probe.attack_centers,
                                               placement_failures=probe.placement_failures,cast_failures=probe.cast_failures,
                                               delayed_failures=probe.delayed_failures),
                                         ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

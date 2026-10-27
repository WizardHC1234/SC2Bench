"""Compact supplemental data; replay remains the asset event source."""
from __future__ import annotations
import gzip
import json
import math
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ENGINE_SCHEMA_VERSION = '2'

def validate_interval(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('evaluation_interval_seconds must be a positive finite number')
    return float(value)

def _optional(message, name):
    return getattr(message, name) if message.HasField(name) else None

def observation_sample(bot, snapshot, *, response=None, include_combat_membership=False):
    if response is None:
        response = getattr(getattr(bot, 'state', None), 'response_observation', None)
    if response is None or not hasattr(response, 'SerializeToString'):
        return None
    obs = response.observation
    score = obs.score.score_details
    common = obs.player_common
    tracker = getattr(getattr(bot.bridge, 'zone_registry', None), 'enemies', None)
    records = getattr(tracker, '_records', {})
    visible = {int(u.tag): u for u in obs.raw_data.units
               if u.alliance == 4 and u.display_type == 1 and not u.is_hallucination
               and (not u.HasField('health') or u.health > 0)}
    sightings = []
    for tag in sorted(set(records) | set(visible)):
        previous = records.get(tag)
        unit = visible.get(tag)
        sightings.append(dict(tag=int(tag), visible=unit is not None,
            type_id=int(unit.unit_type) if unit is not None else None,
            zone_id=getattr(previous, 'zone_id', None), name=getattr(previous, 'name', None),
            seen_at=float(snapshot.game_time_seconds) if unit is not None else getattr(previous, 'seen_at', None)))
    keys = ('task_id', 'action', 'target', 'to', 'to_count', 'order_index',
            'command_revision', 'membership_revision', 'units', 'group',
            'style', 'withdrawing', 'merge_pending_into',
            '_waiting_for', '_error', '_execution_error', '_disabled')
    tasks = [{k: deepcopy(t[k]) for k in keys if k in t} for t in getattr(bot, '_macro_tasks', [])]
    # During realtime pacing the controller is not refreshed. Absence there
    # means unknown, not that all existing groups disappeared.
    groups = None
    if include_combat_membership and snapshot.info.get('sample_phase') != 'realtime_pacing':
        keep = ('style', 'target', 'assigned', 'phase', 'end_reason', 'member_tags')
        groups = {str(task_id): {k: deepcopy(row[k]) for k in keep if k in row}
                  for task_id, row in (snapshot.info.get('combat_progress') or {}).items()}
        zones = {str(row.get('zone_id')): row for row in (snapshot.info.get('zone_state') or [])}
        for group in groups.values():
            zone = zones.get(str(group.get('target')))
            if zone is None:
                group['target_relation'] = 'unknown'
            else:
                owner, role = zone.get('known_owner'), str(zone.get('zone_role') or '')
                group['target_relation'] = 'home' if owner == 'self' or role.startswith('own_') else 'away'
    return dict(game_loop=int(obs.game_loop), game_time_seconds=float(snapshot.game_time_seconds),
        player_id=getattr(bot, 'player_id', None),
        counters={k: _optional(score, k) for k in ('collected_minerals', 'collected_vespene', 'idle_production_time', 'idle_worker_time')},
        bank={k: _optional(common, k) for k in ('minerals', 'vespene')},
        supply_used=_optional(common, 'food_used'), supply_cap=_optional(common, 'food_cap'),
        enemy_sightings=sightings, macro_tasks=tasks, combat_groups=groups,
        action_errors=[dict(unit_tag=int(e.unit_tag), ability_id=int(e.ability_id), result=int(e.result)) for e in response.action_errors])

@dataclass
class CompactEngineState:
    last_sample: Any = None
    last_emitted_at: Any = None
    cash_baseline: dict = field(default_factory=dict)
    contacts: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    combat_groups: dict = field(default_factory=dict)
    combat_trace_seen: bool = False
    eligible_seconds: float = 0.0
    exhausted_seconds: float = 0.0
    unobserved_seconds: float = 0.0
    origin_seconds: Any = None
    next_sample_at: Any = None

    def _counter_values(self, sample):
        values = dict(sample['counters'])
        for resource in ('minerals', 'vespene'):
            collected = values.get('collected_' + resource)
            bank = sample['bank'].get(resource)
            if resource not in self.cash_baseline:
                self.cash_baseline[resource] = (bank, collected)
            start_bank, start_collected = self.cash_baseline[resource]
            valid = all(x is not None for x in (bank, collected, start_bank, start_collected))
            values['net_spent_' + resource] = start_bank + collected - start_collected - bank if valid else None
        values['supply_eligible_seconds'] = self.eligible_seconds
        values['supply_exhausted_seconds'] = self.exhausted_seconds
        if self.unobserved_seconds:
            values['supply_unobserved_seconds'] = self.unobserved_seconds
        return values

    def update(self, sample, interval):
        now = sample['game_time_seconds']
        if self.last_sample is not None:
            dt = max(0.0, now - self.last_sample['game_time_seconds'])
            used, cap = self.last_sample['supply_used'], self.last_sample['supply_cap']
            if used is not None and cap is not None and 0 < cap < 200:
                self.eligible_seconds += dt
                if used >= cap:
                    self.exhausted_seconds += dt
            elif used is None or cap is None:
                self.unobserved_seconds += dt
        row = dict(kind='engine_delta', schema_version=ENGINE_SCHEMA_VERSION,
                   game_loop=sample['game_loop'], game_time_seconds=now, player_id=sample['player_id'])
        current = {x['tag']: x for x in sample['enemy_sightings']}
        changed = []
        for tag, item in current.items():
            old = self.contacts.get(tag)
            signature = {k: v for k, v in item.items() if k != 'seen_at'}
            if old is None or signature != {k: v for k, v in old.items() if k != 'seen_at'}:
                changed.append(item)
        if changed:
            row['enemy_updates'] = changed
        removed = sorted(set(self.contacts) - set(current))
        if removed:
            row['enemy_removed_from_history'] = removed
        current_tasks = {x['task_id']: x for x in sample['macro_tasks']}
        changed_tasks = [x for tag, x in current_tasks.items() if self.tasks.get(tag) != x]
        if changed_tasks:
            row['task_updates'] = changed_tasks
        removed_tasks = sorted(set(self.tasks) - set(current_tasks))
        if removed_tasks:
            row['tasks_removed'] = removed_tasks
        groups = sample.get('combat_groups')
        if groups is not None:
            if not self.combat_trace_seen:
                row['combat_trace'] = True
                self.combat_trace_seen = True
            group_updates = []
            for task_id, group in groups.items():
                previous = self.combat_groups.get(task_id, {})
                fields = {k: v for k, v in group.items()
                          if k != 'member_tags' and (k not in previous or previous[k] != v)}
                cleared = sorted(set(previous) - set(group) - {'member_tags'})
                if cleared:
                    fields['fields_removed'] = cleared
                if 'member_tags' in group:
                    before = set(previous.get('member_tags') or [])
                    after = set(group['member_tags'] or [])
                    added = sorted(after - before)
                    removed_members = sorted(before - after)
                    if 'member_tags' not in previous:
                        fields['membership_known'] = True
                    if added:
                        fields['members_added'] = added
                    if removed_members:
                        fields['members_removed'] = removed_members
                elif 'member_tags' in previous:
                    fields['membership_known'] = False
                    if previous['member_tags']:
                        fields['members_removed'] = sorted(previous['member_tags'])
                if fields:
                    group_updates.append({'task_id': task_id, **fields})
            if group_updates:
                row['combat_updates'] = group_updates
            closed = sorted(set(self.combat_groups) - set(groups))
            if closed:
                row['combat_groups_removed'] = closed
            self.combat_groups = deepcopy(groups)
        if sample['action_errors'] and (self.last_sample is None or sample['game_loop'] != self.last_sample['game_loop'] or sample['action_errors'] != self.last_sample['action_errors']):
            row['action_errors'] = sample['action_errors']
        counters = self._counter_values(sample)
        if self.last_sample is None:
            row['cash_baseline'] = {key: {'bank': value[0], 'collected': value[1]}
                                    for key, value in self.cash_baseline.items()}
        if self.origin_seconds is None:
            self.origin_seconds = now
        if self.last_emitted_at is None or self.next_sample_at is None or now >= self.next_sample_at:
            row['counters'] = counters
            self.last_emitted_at = now
            self.next_sample_at = self.origin_seconds + (math.floor((now-self.origin_seconds)/interval)+1)*interval
        self.last_sample = deepcopy(sample)
        self.contacts, self.tasks = deepcopy(current), deepcopy(current_tasks)
        return row if len(row) > 5 else None

    def flush(self):
        if self.last_sample is None or self.last_emitted_at == self.last_sample['game_time_seconds']:
            return None
        sample = self.last_sample
        self.last_emitted_at = sample['game_time_seconds']
        return dict(kind='engine_delta', schema_version=ENGINE_SCHEMA_VERSION,
            game_loop=sample['game_loop'], game_time_seconds=sample['game_time_seconds'],
            player_id=sample['player_id'], counters=self._counter_values(sample))

def compact_event(entry):
    omitted = {'observation', 'brief_observation', 'agent_input', 'rendered_result',
               'observation_char_count', 'brief_observation_char_count', 'agent_input_char_count',
               'submitted_decision', 'call', 'data', 'agent_context'}
    if isinstance(entry.get('validation'), dict) and not entry['validation'].get('accepted', True):
        omitted.discard('submitted_decision')
    return dict(kind='platform_event', schema_version=ENGINE_SCHEMA_VERSION,
                event={k: v for k, v in entry.items() if k not in omitted})

def append_engine_sample(path, sample):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(sample, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf8')
    with path.open('ab') as stream:
        stream.write(gzip.compress(data, compresslevel=3, mtime=0))

def iter_engine_samples(path: str | Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        for line in stream:
            yield json.loads(line)

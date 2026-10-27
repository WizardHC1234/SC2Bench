# Compact evaluation recording

Replay remains the primary source for births, construction completion, deaths,
transformations, upgrades, and available command/selection events. Decode with
Python `mpyq` and the replay's own `s2protocol` build; do not launch the game.

## One supplemental file

Recording adds only `evaluation.jsonl.gz` to each active episode/branch.
It contains two record kinds:

* `engine_delta`: periodic cumulative counters, changed enemy sightings, changed
  task state, and actual native action errors.
* `platform_event`: tool arguments/structured results, decision feedback and
  errors. Full observations, briefing text, model input, duplicate tool result
  text and duplicate data fields are omitted. Existing session and Codex files
  remain the source for actual model text and provider usage.

The default counter interval is **1 game second**. The eight regular counters
are collected minerals/gas, net spent minerals/gas, cumulative production/worker
idle time, and cumulative supply eligible/exhausted seconds. Unsupported fields
remain null. An additional missing-supply duration is emitted only if observation
coverage is missing. Cash baselines use the first native observation; net spending
is opening bank + collected delta - closing bank, assuming no external transfers.
The first counter record stores that cash baseline once for auditability.

Balances and population are used in memory for these calculations. They are not
saved as another state time series. Workers, asset scores, all entity positions,
health, orders, visibility grids, complete protobuf messages and full platform
snapshots are not copied into the supplemental file. This mode supports the
specified metrics rather than future arbitrary low-level reconstruction.

Enemy updates contain the stable tag, visible flag, type, zone and last-seen time.
Continuously visible entities do not produce a row each step. Appearance,
disappearance, zone/type changes and changes in retained history are recorded.
A removal from history is not proof of asset death; use replay truth for that.
Task state is emitted only when it changes. These are executor-reported states,
not a complete audit of every prerequisite.

The optional combat membership prototype is **disabled by default**. When
explicitly used for research validation, it emits changed order attributes and member tag
additions/removals. A full membership list is not repeated at every observation.
The fields are task ID, attack/defend intent, target and its home/away/unknown
relation, assignment status, controller phase, end reason and membership-known
status. Realtime pacing leaves this controller state unchanged. A new
`combat_trace` marker distinguishes supported traces from older files with no
group telemetry; missing telemetry must not be interpreted as unused army.

The task-allocation and target-concentration functions are experimental.
The confirmed offline profiles are attack-start frequency and commitment share
in fixed game-time windows (default five minutes), using the same attack-start
events; see force_preference_definitions.json and aggregate_force_windows.py.
These use committed action semantics and army values, not the experimental
time-allocation/concentration functions. First outward order time and initial
force fraction remain excluded as standalone whole-game indicators.
No extra member-tag recording is enabled for those excluded candidates.
If evaluating the optional prototype, combine membership changes with replay values.
Groups at the same target are combined for concentration. Assignment is not
enemy contact; the controller phase `executing` is not proof of fighting.
Unassigned units are under other/default control, not assumed idle. Membership
removal is not automatically a retreat: join deaths and transfers from replay
and task events. See `analysis/metric_design/force_usage_definitions.json` and
the reusable reconstruction/statistic functions in `force_usage.py`.

## Configure the supplemental interval

```python
from sc2bench_env import Environment
from sc2bench_env.versus import VersusMatch

# Default: 1 game second. Larger intervals reduce periodic logging volume.
env = Environment('sharpy', evaluation_interval_seconds=1.0)
match = VersusMatch(evaluation_interval_seconds=1.0)
```

This controls logging only. It does not change model decisions, simulation step
size or replay tracker frequency. Sampling targets are aligned to the first
observation; each target uses the next actual bot observation. Actual times are
saved, so an interval shorter than the bot observation step cannot add precision.
End/savepoint events also flush the latest counters. Supply durations are
integrated at every actual observation step independently of write frequency,
using the preceding observed supply state between successive observations.

The standard replay save API has no tracker-period parameter. In the audited
SC2 4.10 replays, ordinary stats intervals are 160 loops, or 160/22.4 = about
7.14 game seconds. Changing the bot's `game_step` or parsing/interpolating more
points does not change that stream. Existing replay events cannot be made denser
retroactively. The installed SDK already enables raw and score interfaces.

## Read without the game

```python
from sc2bench_env.recording.engine import iter_engine_samples

for row in iter_engine_samples('records/episode/evaluation.jsonl.gz'):
    if row['kind'] == 'engine_delta' and 'counters' in row:
        print(row['game_time_seconds'], row['counters'])
```

Each gzip member contains one JSON line and is closed after writing. A truncated
last write should be reported as incomplete, not silently accepted. Scope asset
IDs by episode, branch, player and tag. Replay tracker tags are
`(m_unitTagIndex << 18) + m_unitTagRecycle`.

## Scope and validation

Recording is evaluation-only and is not added to model observations. Link native
sighting changes to actual model inputs and query delivery to compute EIC. Do not
assume every native sighting reached the model. Branches are isolated; parked
savepoints do not write. Sampling accumulators are restored with the recorder
cursor. With `record_trajectory=False`, no supplemental file is written.

Replay export must produce a nonempty file before it is marked successful.
Offline completeness checks still need MPQ/stream decoding, player mapping,
engine version and endpoint coverage. QuickLoad stops replay recording: a saved
trunk plus a partial-view branch log does not provide full enemy branch truth.

Full multi-cause blocking analysis and unique high-level-task/payment/order
attribution remain separate work. The compact mode intentionally does not claim
to provide arbitrary entity queues or every unmet condition. Actual disk size,
wall-clock overhead and terminal coverage require subsequent normal games;
regression checks here use synthetic/native messages and mock backends only.

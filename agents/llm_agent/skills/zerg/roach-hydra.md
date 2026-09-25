---
name: roach-hydra
race: zerg
kind: strategy
---

# Roach Hydra

## Objective

Execute the `Roach Hydra` plan through the cumulative targets and conditions below.

## Count semantics

- Every unit, worker, building and expansion count is a cumulative target, not an amount to add in one decision.
- Compare living, unfinished and queued counts before issuing the next action. Request only the missing difference.
- `continuous` means the original plan used its unbounded default target. Continue that production only after earlier gated targets are satisfied.
- Different plan components may run concurrently. Do not force unrelated tracks into a single serial build order.

## Procedure

1. Find the earliest unmet checkpoint whose stated condition is observable and satisfied.
2. Query any missing cost, prerequisite or unit capability needed for that checkpoint.
3. Issue only the actions needed to close the gap to the checkpoint totals and end the decision with `advance`.
4. Use only the attack condition written below. An army-power value is not a unit count and must not be converted into one.
5. If a step requires unsupported exact placement or unit-level control, do not invent a substitute composition or timing.

## Roach Hydra Build setup

### Economy and structures

- Build `roach_warren` to a cumulative total of 1. activate only when vespene reaches 100.
- When game time reaches 240 seconds, build `extractor` to a cumulative total of 2. Skip this step when vespene reaches 100.
- When total hydralisk_den count reaches 1, build `extractor` to a cumulative total of 3. Skip this step when vespene reaches 50.
- When worker supply reaches 60, build `extractor` to a cumulative total of 4. Skip this step when vespene reaches 25.
- When minerals reach 749, build `extractor` to a cumulative total of 6. Skip this step when vespene reaches 25.
- When minerals reach 1000, build `extractor` to a cumulative total of 8. Skip this step when vespene reaches 25.
- When total drone count reaches 14, train `overlord` to a cumulative total of 2.
- When supply used reaches 16, expand to a cumulative total of 2 `hatchery` structures.
- When supply used reaches 18, build `spawning_pool` to a cumulative total of 1.
- When supply used reaches 20, build `extractor` to a cumulative total of 1.
- When total drone count reaches 24, including lost units, including queued units, expand to a cumulative total of 3 `hatchery` structures.
- Morph eligible `hatchery` structures into `lair`. skip this step when total hive count reaches 1.
- When total drone count reaches 30, including lost units, expand to a cumulative total of 4 `hatchery` structures.
- When ready lair count reaches 1, build `hydralisk_den` to a cumulative total of 1.
- When supply used reaches 100, expand to a cumulative total of 5 `hatchery` structures.
- Train `drone` to a cumulative total of 23.
- Train `drone` to a cumulative total of 28.
- Train `drone` to a cumulative total of 35.
- Train `drone` to a cumulative total of 45.
- Train `drone` to a cumulative total of 50.
- Train `drone` to a cumulative total of 70.

### Army production

- Train `queen` to a cumulative total of 2. activate only when total spawning_pool count reaches 1.
- Train `queen` to a cumulative total of 3.
- Morph `overseer` to a cumulative total of 1.
- Train `queen` to a cumulative total of 4.
- Train `queen` to a cumulative total of 10. activate only when minerals reach 500.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 4.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 12.
- Train `queen` to a cumulative total of 1.
- Train `zergling` to a cumulative total of 16. This target is issued only once.
- Train `roach` to a cumulative total of 4. This target is issued only once. activate only when vespene reaches 25.
- Train `zergling` to a cumulative total of 100. This target is issued only once. activate only when minerals reach 750.
- Train `hydralisk` to a cumulative total of 7. skip this step when ready hydralisk_den count reaches 1.
- Train `zergling` to a cumulative total of 24. This target is issued only once.
- Train `roach` to a cumulative total of 10. activate only when vespene reaches 25.
- Train `roach` to a cumulative total of continuous. skip this step when ready hydralisk_den count reaches 1.
- Train `hydralisk` to a cumulative total of continuous.

### Research

- When total hatchery count reaches 2, research `metabolic_boost`. activate only when vespene reaches 100.

## Roach Hydra plan

### Tactical behavior

- Scout with one worker. skip this step when the map has exactly one possible enemy start; activate only when supply used reaches 20.
- Distribute workers.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Gather free army before attacking.
- Defend owned zones.
- Spread creep.
- Inject Larva.
- Use `20` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

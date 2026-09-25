---
name: mutalisk
race: zerg
kind: strategy
---

# Mutalisk

## Objective

Execute the `Mutalisk` plan through the cumulative targets and conditions below.

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

## Mutalisk Build setup

### Economy and structures

- When total hatchery count reaches 2, build `extractor` to a cumulative total of 1.
- Build `roach_warren` to a cumulative total of 1. activate only when vespene reaches 100.
- When game time reaches 240 seconds, build `extractor` to a cumulative total of 2.
- When total lair count reaches 1, build `extractor` to a cumulative total of 3.
- Build `extractor` to a cumulative total of 5. Skip this step when vespene reaches 100.
- When supply used reaches 50, build `extractor` to a cumulative total of 8.
- When total drone count reaches 14, train `overlord` to a cumulative total of 2.
- When supply used reaches 16, expand to a cumulative total of 2 `hatchery` structures.
- When total extractor count reaches 1, build `spawning_pool` to a cumulative total of 1.
- When ready spawning_pool count reaches 1, build `spine_crawler` defensively to a cumulative total of 1.
- Morph eligible `hatchery` structures into `lair`. skip this step when total hive count reaches 1.
- When total drone count reaches 30, including lost units, expand to a cumulative total of 3 `hatchery` structures.
- When total lair count reaches 1, build `spire` to a cumulative total of 1.
- When total spire count reaches 1, expand to a cumulative total of 4 `hatchery` structures.
- When total mutalisk count reaches 10, including lost units, build `infestation_pit` to a cumulative total of 1.
- When ready infestation_pit count reaches 1, morph eligible `lair` structures into `hive`.
- Train `drone` to a cumulative total of 20.
- Train `drone` to a cumulative total of 25.
- Train `drone` to a cumulative total of 30.
- Train `drone` to a cumulative total of 45.
- Train `drone` to a cumulative total of 65.

### Army production

- Train `queen` to a cumulative total of 2. activate only when total spawning_pool count reaches 1.
- Train `queen` to a cumulative total of 3.
- Morph `overseer` to a cumulative total of 1.
- Train `queen` to a cumulative total of 5.
- Train `corruptor` to a cumulative total of 3. activate only when ready greater_spire count reaches 1.
- Morph `brood_lord` to a cumulative total of 5.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 4.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 12.
- Train `queen` to a cumulative total of 1.
- Train `zergling` to a cumulative total of 16. This target is issued only once.
- Train `roach` to a cumulative total of 4. This target is issued only once. activate only when vespene reaches 25.
- Train `mutalisk` to a cumulative total of 4. activate only when ready spire count reaches 1.
- Train `zergling` to a cumulative total of 16. This target is issued only once.
- Train `roach` to a cumulative total of 10. skip this step when ready spire count reaches 1; activate only when vespene reaches 25.
- Train `zergling` to a cumulative total of 40. activate only when (ready spire count reaches 1 and minerals reach 300).
- Train `roach` to a cumulative total of continuous. skip this step when ready spire count reaches 1.
- Train `zergling` to a cumulative total of 100. activate only when (ready spire count reaches 1 and minerals reach 500).
- Train `mutalisk` to a cumulative total of continuous.

### Research

- Research `metabolic_boost`. activate only when vespene reaches 100.
- Research `flyer_attacks_1`.
- Research `flyer_attacks_2`.
- Research `flyer_attacks_3`.

## Mutalisk Bot plan

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

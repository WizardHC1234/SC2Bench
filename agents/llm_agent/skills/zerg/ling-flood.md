---
name: ling-flood
race: zerg
kind: strategy
---

# Lings

## Objective

Execute the `Lings` plan through the cumulative targets and conditions below.

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

## Ling Speed Build setup

### Economy and structures

- When total hatchery count reaches 2, build `extractor` to a cumulative total of 1.
- Build `roach_warren` to a cumulative total of 1. activate only when vespene reaches 100.
- When total drone count reaches 14, train `overlord` to a cumulative total of 2.
- Expand to a cumulative total of 2 `hatchery` structures.
- When total extractor count reaches 1, build `spawning_pool` to a cumulative total of 1.
- When total drone count reaches 24, including lost units, expand to a cumulative total of 3 `hatchery` structures.
- When total drone count reaches 30, including lost units, expand to a cumulative total of 4 `hatchery` structures.
- Train `drone` to a cumulative total of 20.
- Train `drone` to a cumulative total of 30.
- Train `drone` to a cumulative total of 40.

### Army production

- Train `queen` to a cumulative total of 2. activate only when total spawning_pool count reaches 1.
- Train `queen` to a cumulative total of 3.
- Train `queen` to a cumulative total of 4.
- When minerals reach 500, train `queen` to a cumulative total of 10.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 12.
- Train `queen` to a cumulative total of 1.
- Train `zergling` to a cumulative total of 16. This target is issued only once.
- Train `roach` to a cumulative total of 4. This target is issued only once. activate only when vespene reaches 25.
- Train `zergling` to a cumulative total of 16.
- Train `roach` to a cumulative total of 10. activate only when vespene reaches 25.
- Train `zergling` to a cumulative total of continuous.

### Research

- Research `metabolic_boost`. activate only when vespene reaches 100.

## Ling Flood Build setup

### Economy and structures

- When total hatchery count reaches 2, build `extractor` to a cumulative total of 1.
- Build `spawning_pool` to a cumulative total of 1.
- When total zergling count reaches 4, including lost units, expand to a cumulative total of 2 `hatchery` structures.
- When total drone count reaches 24, including lost units, expand to a cumulative total of 3 `hatchery` structures.
- When total drone count reaches 30, including lost units, expand to a cumulative total of 4 `hatchery` structures.
- Train `drone` to a cumulative total of 35.
- Morph eligible `hatchery` structures into `lair`.
- Train `drone` to a cumulative total of 40.
- Build `extractor` to a cumulative total of 3.
- Build `spire` to a cumulative total of 1.
- When total spawning_pool count reaches 1, train `overlord` to a cumulative total of 2. skip this step when total overlord count reaches 2.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 14. skip this step when total drone count reaches 14.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 20.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 30.

### Army production

- Train `queen` to a cumulative total of 2.
- Train `queen` to a cumulative total of 3.
- Train `queen` to a cumulative total of 4.
- Train `mutalisk` to a cumulative total of 10.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 4.
- When total spawning_pool count reaches 1, train `queen` to a cumulative total of 1. skip this step when total queen count reaches 1.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 12. This target is issued only once.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of continuous.

### Research

- Research `metabolic_boost`. activate only when vespene reaches 100.

## Ling Flood.macro

### Tactical behavior

- Scout with one worker. activate only when the map has more than one possible enemy start.
- Distribute workers.
- Clear blocked base minerals.
- Spread creep.
- Inject Larva.

## Ling Flood.aggressive

### Tactical behavior

- Scout with one worker. activate only when the map has more than one possible enemy start.
- Clear blocked base minerals.
- Spread creep.
- Inject Larva.
- Distribute workers with parameter `3`. skip this step when (stop_gas or end_game).
- Distribute workers with parameter `0`. activate only when stop_gas; skip this step when end_game.
- Distribute workers with parameter `None`. activate only when end_game.

## Attack behavior

- Defend the first owned zone found under attack. Against a ground presence, send Zerglings, Roaches, Queens and Mutalisks; when only enemy air units are present, send Queens.
- Start an all-out attack when idle Zerglings reach 6 before 4:00, 12 from 4:00 to 6:59, 20 from 7:00 to 9:59, or 25 from 10:00 onward.
- During an all-out attack, send Zerglings, Roaches and Mutalisks. End the attack state when no Zerglings remain.
- Attack the known enemy structure closest to the own start. If no structure is known, use the enemy start, an enemy-owned expansion, or an expansion selected from scout timestamps by the source routine.

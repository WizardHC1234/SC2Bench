---
name: macro-roach
race: zerg
kind: strategy
---

# Macro Roach

## Objective

Execute the `Macro Roach` plan through the cumulative targets and conditions below.

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

## Macro Roach plan

### Economy and structures

- Expand to a cumulative total of 2 `hatchery` structures.
- When ready spawning_pool count reaches 1, expand to a cumulative total of 3 `hatchery` structures.
- When supply used reaches 80, morph eligible `hatchery` structures into `lair`.
- Expand to a cumulative total of 4 `hatchery` structures.
- When supply used reaches 100, build `evolution_chamber` to a cumulative total of 2.
- When total hatchery count reaches 2, including queued units, build `spawning_pool` to a cumulative total of 1.
- When total queen count reaches 2, build `roach_warren` to a cumulative total of 1.
- When one spawning_pool is at least 50% complete, build `extractor` to a cumulative total of 1.
- When ready roach_warren count reaches 1, build `extractor` to a cumulative total of 2.
- When total hatchery count reaches 3, build `extractor` to a cumulative total of 3.
- When ready hatchery count reaches 3, build `extractor` to a cumulative total of 4.
- When total overlord count reaches 10, build `extractor` to a cumulative total of 5.
- When total overlord count reaches 20, build `extractor` to a cumulative total of 6.
- Train `drone` to a cumulative total of 70.
- Maintain Overlord supply as production grows.

### Army production

- When total spawning_pool count reaches 1, train `queen` to a cumulative total of 2.
- When total hatchery count reaches 2, train `zergling` to a cumulative total of 6. This target is issued only once.
- When total hatchery count reaches 2, including queued units, train `queen` to a cumulative total of 3.
- When total hatchery count reaches 3, including queued units, train `queen` to a cumulative total of 4.
- When total hatchery count reaches 2, train `roach` to a cumulative total of 4.
- Train `zergling` to a cumulative total of 4.
- When total hatchery count reaches 3, including queued units, train `roach` to a cumulative total of continuous.
- When ready roach_warren count reaches 1, morph `ravager` to a cumulative total of 5. activate only when vespene reaches 200.
- When ready roach count reaches 10, morph `ravager` to a cumulative total of 50. activate only when vespene reaches 300.

### Research

- When total roach_warren count reaches 1, research `glial_reconstitution`.
- When total evolution_chamber count reaches 1, research `missile_attacks_1`.
- Research `ground_carapace_1`.
- Research `missile_attacks_2`.
- Research `ground_carapace_2`.

### Tactical behavior

- Use `120` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Spread creep.
- Inject Larva.
- Distribute workers.
- Defend owned zones.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

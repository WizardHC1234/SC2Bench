---
name: twelve-pool
race: zerg
kind: strategy
---

# Twelve Pool

## Objective

Execute the `Twelve Pool` plan through the cumulative targets and conditions below.

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

## Twelve Pool plan

### Economy and structures

- Build `spawning_pool` to a cumulative total of 1. skip this step when total spawning_pool count reaches 1.
- When a flying enemy structure exists and supply used is above 30, build `extractor` to a cumulative total of 2.
- Expand to a cumulative total of 2 `hatchery` structures.
- Morph eligible `hatchery` structures into `lair`.
- Build `extractor` to a cumulative total of 4.
- Build `spire` to a cumulative total of 1.
- When total spawning_pool count reaches 1, train `overlord` to a cumulative total of 2. skip this step when total overlord count reaches 2.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 14. skip this step when total drone count reaches 14.
- When free supply is at most 0, maintain Overlord supply as production grows.
- Maintain Overlord supply as production grows.

### Army production

- Train `mutalisk` to a cumulative total of 10.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of continuous.

### Tactical behavior

- Distribute workers.
- Inject Larva.
- Defend owned zones.
- Gather free army before attacking.
- Use `2` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

## Explicit conditional rules

- When the attack starts, the source selects the 10 Drones closest to the enemy start and sets the retreat multiplier to 0.
- Drones cannot be dispatched by the available combat action, so this worker-attack part is not executable and must not be replaced with an invented army count.

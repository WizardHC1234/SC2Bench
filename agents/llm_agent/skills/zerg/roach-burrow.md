---
name: roach-burrow
race: zerg
kind: strategy
---

# Roach Burrow

## Objective

Execute the `Roach Burrow` plan through the cumulative targets and conditions below.

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

## Roach Burrow Build setup

### Economy and structures

- When (worker supply reaches 13 or free supply is at most 0), maintain Overlord supply as production grows.
- When total drone count reaches 13, train `overlord` to a cumulative total of 2.
- When total drone count reaches 16, expand to a cumulative total of 2 `hatchery` structures.
- Build `extractor` to a cumulative total of 1.
- When supply used reaches 17, build `spawning_pool` to a cumulative total of 1.
- When supply used reaches 24, build `roach_warren` to a cumulative total of 1.
- Build `extractor` to a cumulative total of 2.
- Train `drone` to a cumulative total of 25.

### Army production

- Morph Ravagers according to the plan's automatic Roach conversion rule. activate only when worker supply reaches 20.
- When supply used reaches 20, train `queen` to a cumulative total of 1.
- When supply used reaches 20, train `zergling` to a cumulative total of 6.
- When supply used reaches 24, train `queen` to a cumulative total of 2.
- When supply used reaches 27, train `roach` to a cumulative total of 5.
- Train `roach` to a cumulative total of 999.

### Research

- When vespene reaches 100, research `burrow`.

## Roach Burrow Bot plan

### Tactical behavior

- Use `8` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Distribute workers.
- Inject Larva.
- Defend owned zones.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

## Burrow and Ravager behavior

- A Roach burrows when below 40% health and the burrow ability is ready. A burrowed Roach unburrows after rising above 70% health.
- The Ravager target equals the observed enemy Siege Tank count plus the observed enemy Photon Cannon count.
- The attack uses retreat multiplier 0.01.
- Burrow state changes and the Ravager conversion rule run below the high-level action interface.

---
name: two-base-tanks
race: terran
kind: strategy
---

# Two Base Tanks

## Objective

Execute the `Two Base Tanks` plan through the cumulative targets and conditions below.

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

## Two Base Tanks plan

### Economy and structures

- Train `scv` to a cumulative total of 22. skip this step when total command_center count reaches 2.
- Train `scv` to a cumulative total of 44.
- When supply used reaches 13, build `supply_depot` to a cumulative total of 1.
- When one supply_depot is at least 95% complete, build `barracks` to a cumulative total of 1.
- When supply used reaches 16, build `refinery` to a cumulative total of 1.
- Expand to a cumulative total of 2 `command_center` structures.
- When supply used reaches 16, build `supply_depot` to a cumulative total of 2.
- When total marine count reaches 1, including queued units, build `refinery` to a cumulative total of 2.
- Build `factory` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 1. activate only when ready factory count reaches 1.
- When supply used reaches 28, build `supply_depot` to a cumulative total of 4.
- Build `factory` to a cumulative total of 2.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 2.
- When supply used reaches 38, build `supply_depot` to a cumulative total of 5.
- Expand to a cumulative total of 3 `command_center` structures. activate only when the total surplus harvesters across owned bases is greater than 5.
- Expand to a cumulative total of 4 `command_center` structures. activate only when (the total surplus harvesters across owned bases is greater than 5 and ready command_center count reaches 3).
- Build `refinery` to a cumulative total of 3.
- When supply used reaches 45, build `supply_depot` to a cumulative total of 8.
- Build `barracks` to a cumulative total of 2.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 1.
- Build `refinery` to a cumulative total of 4.
- When supply used reaches 75, build `supply_depot` to a cumulative total of 10.
- Build `barracks` to a cumulative total of 5.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 3.
- Build `factory` to a cumulative total of 3.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 3.
- When supply used reaches 85, build `supply_depot` to a cumulative total of 14.
- Morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 99.

### Army production

- When ready factory_techlab count reaches 1, train `siege_tank` to a cumulative total of 20.
- When ready barracks count reaches 1, train `marine` to a cumulative total of 2.
- When minerals reach 250, train `marine` to a cumulative total of 100.

### Research

- Research `combat_shield`.

### Tactical behavior

- Scout with one worker. activate only when total barracks count reaches 1.
- Use `60` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Lower Supply Depots.
- Defend owned zones.
- Scan enemy territory with parameter `120`.
- Call MULEs.
- Distribute workers.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

## Explicit conditional rules

- The third-base condition sums `surplus_harvesters` over owned town halls and becomes true only when that sum is greater than 5.

---
name: one-base-turtle
race: terran
kind: strategy
---

# One Base Turtle

## Objective

Execute the `One Base Turtle` plan through the cumulative targets and conditions below.

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

## One Base Turtle plan

### Economy and structures

- Train `scv` to a cumulative total of 13.
- When supply used reaches 13, build `supply_depot` to a cumulative total of 1.
- Train `scv` to a cumulative total of 15.
- When one supply_depot is at least 95% complete, build `barracks` to a cumulative total of 1.
- Train `scv` to a cumulative total of 16.
- Build `refinery` to a cumulative total of 1.
- When supply used reaches 16, build `supply_depot` to a cumulative total of 2.
- Train `scv` to a cumulative total of 18.
- Morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Build `factory` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- Train `scv` to a cumulative total of 20.
- When supply used reaches 20, build `supply_depot` to a cumulative total of 3.
- Build `bunker` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- Build `refinery` to a cumulative total of 2.
- Build `factory` to a cumulative total of 2.
- Train `scv` to a cumulative total of 22.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 2. activate only when ready factory count reaches 1.
- When supply used reaches 28, build `supply_depot` to a cumulative total of 4.
- Build `barracks` to a cumulative total of 3.
- When supply used reaches 38, build `supply_depot` to a cumulative total of 5.

### Army production

- When ready factory_techlab count reaches 1, train `siege_tank` to a cumulative total of 20.
- When ready barracks count reaches 1, train `marine` to a cumulative total of 100.

### Tactical behavior

- Use `4` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Lower Supply Depots.
- Defend owned zones.
- Call MULEs.
- Distribute workers.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

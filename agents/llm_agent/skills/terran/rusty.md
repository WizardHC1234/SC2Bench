---
name: rusty
race: terran
kind: strategy
---

# Rusty

## Objective

Execute the `Rusty` plan through the cumulative targets and conditions below.

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

## Build Tanks setup

### Economy and structures

- Morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Train `scv` to a cumulative total of 22. skip this step when total command_center count reaches 2.
- Train `scv` to a cumulative total of 44.
- Build `engineering_bay` to a cumulative total of 1.
- Build `missile_turret` defensively to a cumulative total of 2.
- Build `missile_turret` defensively to a cumulative total of None.
- Build `starport` to a cumulative total of 2.
- Build `starport_techlab` on `starport` to a cumulative add-on total of 1.
- When supply used reaches 13, build `supply_depot` to a cumulative total of 1.
- When supply used reaches 16, build `refinery` to a cumulative total of 1.
- When total supply_depot count reaches 1, build `barracks` to a cumulative total of 1.
- When one barracks is at least 25% complete, build `supply_depot` to a cumulative total of 2.
- When supply used reaches 18, build `refinery` to a cumulative total of 1.
- When total marine count reaches 1, expand to a cumulative total of 2 `command_center` structures.
- When supply used reaches 20, build `refinery` to a cumulative total of 2.
- Build `factory` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- Build `factory` to a cumulative total of 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 1.
- When total siege_tank count reaches 1, including lost units, build `factory` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 4.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 2.
- Build `barracks` to a cumulative total of 2.
- Build `starport` to a cumulative total of 1.
- Build `barracks` to a cumulative total of 5.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 1.
- Build `starport_reactor` on `starport` to a cumulative add-on total of 1.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 3.
- Expand to a cumulative total of 3 `command_center` structures.

### Army production

- When ready starport count reaches 1, train `raven` to a cumulative total of 2.
- When total factory count reaches 1, train `hellion` to a cumulative total of 2. skip this step when ready factory_techlab count reaches 1.
- When ready factory_techlab count reaches 1, train `siege_tank` to a cumulative total of 20.
- When ready starport count reaches 1, train `medivac` to a cumulative total of 2.
- Train `viking` to a cumulative total of 1.
- Train `viking` to a cumulative total of 3. activate only when at least 1 enemy Colossus, Medivac, Raven, Void Ray, Carrier, Tempest or Brood Lord units have been observed.
- When ready starport count reaches 1, train `medivac` to a cumulative total of 4.
- Train `viking` to a cumulative total of 10. activate only when at least 4 enemy Colossus, Medivac, Raven, Void Ray, Carrier, Tempest or Brood Lord units have been observed.
- When ready starport count reaches 1, train `medivac` to a cumulative total of 6.
- When ready barracks count reaches 1, train `marine` to a cumulative total of 2.
- When minerals reach 250, train `marine` to a cumulative total of 100.

### Research

- Research `combat_shield`.

## Rusty plan

### Tactical behavior

- Use `a random integer from 50 through 80` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Scout with one worker. activate only when total supply_depot count reaches 1.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Lower Supply Depots.
- Defend owned zones.
- Call MULEs with parameter `100`.
- Scan enemy territory.
- Distribute workers.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

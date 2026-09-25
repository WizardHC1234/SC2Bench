---
name: terran-silver-bio
race: terran
kind: strategy
---

# Terran Silver Bio

## Objective

Execute the `Terran Silver Bio` plan through the cumulative targets and conditions below.

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

## Build Bio setup

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
- Build `barracks` to a cumulative total of 1.
- When supply used reaches 15, build `refinery` to a cumulative total of 1.
- Expand to a cumulative total of 2 `command_center` structures. activate only when (no rush is detected or total siege_tank count reaches 2, including lost units).
- Build `barracks` to a cumulative total of 2. activate only when a rush is detected.
- Build `supply_depot` to a cumulative total of 2.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 1.
- Build `barracks` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 2.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 1.
- Build `barracks` to a cumulative total of 3.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 2.
- When worker supply reaches 40, expand to a cumulative total of 3 `command_center` structures.
- Build `barracks` to a cumulative total of 5.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 3.
- Build `refinery` to a cumulative total of 4.
- When minerals reach 400, build `barracks` to a cumulative total of 8.
- When minerals reach 500, build `barracks_reactor` on `barracks` to a cumulative add-on total of 6.

### Army production

- Train `marine` to a cumulative total of 2. activate only when an enemy worker rush is detected during the first 120 seconds.
- When ready starport count reaches 1, train `raven` to a cumulative total of 2.
- Train `reaper` to a cumulative total of 1. This target is issued only once.
- When total reaper count reaches 1, including lost units, train `marine` to a cumulative total of 2.
- Train `marauder` to a cumulative total of 20.
- Train `marine` to a cumulative total of 20.
- When minerals reach 250, train `marine` to a cumulative total of 100.

### Research

- Research `concussive_shells`.
- Research `combat_shield`.

### Tactical behavior

- Cancel `command_center` construction under the listed condition. skip this step when (no rush is detected or total siege_tank count reaches 2, including lost units).

### Steps without a direct action

- The plan requires an exact position for `bunker`; this step has no direct high-level action equivalent.

## Terran Silver Bio setup

### Tactical behavior

- Use `30` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.

## Terran Silver Bio plan

### Tactical behavior

- Clear blocked base minerals.
- Lower Supply Depots.
- Call MULEs with parameter `0`.
- Distribute workers.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

## Rush response

- During the first 120 seconds, mark a worker rush when an enemy worker is closer to the own start than to the enemy start.
- When that worker-rush flag is set, produce the 2-Marine response from the build and use the exact bunker position at the own main ramp defined by the source.
- The source's general rush detector controls whether the second Command Center proceeds or is cancelled and whether the second Barracks is enabled.

---
name: lurkers
race: zerg
kind: strategy
---

# Lurkers

## Objective

Execute the `Lurkers` plan through the cumulative targets and conditions below.

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

## Lings And Roaches setup

### Army production

- Train `roach` to a cumulative total of continuous.
- Train `zergling` to a cumulative total of continuous.

## Lings And Roaches And Hydras setup

### Army production

- Train `hydralisk` to a cumulative total of continuous.
- Train `roach` to a cumulative total of continuous.
- Train `zergling` to a cumulative total of continuous.

## Roaches And Hydras And Lurkers setup

### Army production

- Train `lurker` to a cumulative total of continuous.
- Train `hydralisk` to a cumulative total of continuous.
- Train `roach` to a cumulative total of continuous.

## Lurker Build setup

### Economy and structures

- Build `extractor` to a cumulative total of 2. skip this step when vespene reaches 200; activate only when supply used reaches 25.
- Build `extractor` to a cumulative total of 3. skip this step when vespene reaches 200; activate only when supply used reaches 40.
- Build `extractor` to a cumulative total of 4. skip this step when vespene reaches 200; activate only when supply used reaches 50.
- When minerals reach 1000, build `extractor` to a cumulative total of 6. skip this step when vespene reaches 200; activate only when supply used reaches 50.
- When minerals reach 2000, build `extractor` to a cumulative total of 8. skip this step when vespene reaches 200; activate only when supply used reaches 50.
- Build `extractor` to a cumulative total of 2. skip this step when vespene reaches 300; activate only when supply used reaches 20.
- Build `extractor` to a cumulative total of 3. skip this step when vespene reaches 300; activate only when supply used reaches 30.
- Build `extractor` to a cumulative total of 4. skip this step when vespene reaches 300; activate only when supply used reaches 40.
- Build `extractor` to a cumulative total of 5. skip this step when vespene reaches 300; activate only when supply used reaches 50.
- Build `extractor` to a cumulative total of 6. skip this step when vespene reaches 300; activate only when supply used reaches 60.
- Build `extractor` to a cumulative total of 7. skip this step when vespene reaches 300; activate only when supply used reaches 65.
- Build `extractor` to a cumulative total of 8. skip this step when vespene reaches 300; activate only when supply used reaches 70.
- When total drone count reaches 13, train `overlord` to a cumulative total of 2.
- Maintain Overlord supply as production grows.
- When supply used reaches 16, expand to a cumulative total of 2 `hatchery` structures.
- When supply used reaches 20, build `extractor` to a cumulative total of 1.
- When game time reaches 180 seconds, expand to a cumulative total of 3 `hatchery` structures.
- When worker supply reaches 40, expand to a cumulative total of 4 `hatchery` structures.
- Train `drone` to a cumulative total of 20.
- When no owned base is under attack and the army-survival analysis is positive, train `drone` to a cumulative total of 80. skip this step when worker supply reaches the sum of 1 plus the ideal workers of ready bases and gas buildings, plus 8 per unfinished base and 3 per unfinished gas building.
- When enemy cloak has been detected, morph eligible `hatchery` structures into `lair`.
- Morph eligible `hatchery` structures into `lair`.
- Build `hydralisk_den` to a cumulative total of 1.
- Build `lurkerdenmp` to a cumulative total of 1.

### Army production

- Train `zergling` to a cumulative total of 4.
- Train `queen` to a cumulative total of 2.
- Train `queen` to a cumulative total of 4.
- When ready lair count reaches 1, morph `overseer` to a cumulative total of 2.
- Train `zergling` to a cumulative total of continuous.

### Research

- When vespene reaches 90, research `metabolic_boost`.

### Steps without a direct action

- When supply used reaches 18, the plan requires an exact position for `spawning_pool`; this step has no direct high-level action equivalent.
- When ready spawning_pool count reaches 1, the plan requires an exact position for `roach_warren`; this step has no direct high-level action equivalent.

## Lurker Bot plan

### Tactical behavior

- Clear blocked base minerals.
- Defend owned zones.
- Cancel unsafe or invalid construction.
- Gather free army before attacking.
- Scout with one worker. activate only when supply used reaches 20.
- Spread creep.
- Inject Larva.
- Distribute workers.
- Use `20` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

### Steps without a direct action

- Scout with Zerglings. activate only when game time reaches 240 seconds.
- Scout with Zerglings. activate only when game time reaches 480 seconds.

## Army ratio and worker conditions

- Before Hydralisks, the Roach target is half the current Zergling count.
- After Hydralisks are available, the Hydralisk target is current Roaches plus 2 and the Roach target remains half the current Zergling count.
- After Lurkers are available, the Lurker target is current Hydralisks divided by 3 plus 1, and the Hydralisk target is current Roaches plus 1.
- Continue toward 80 Drones only while no owned base is under attack and the army-survival analysis is positive.
- Stop adding workers when worker supply reaches 1 plus the ideal workers for each ready base and gas building, plus 8 for each unfinished base and 3 for each unfinished gas building.

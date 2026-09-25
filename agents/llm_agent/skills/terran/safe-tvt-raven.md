---
name: safe-tvt-raven
race: terran
kind: strategy
---

# Safe TvT Raven

## Objective

Execute the `Safe TvT Raven` plan through the cumulative targets and conditions below.

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

## Terran Safe Tv T plan

### Economy and structures

- Train `scv` to a cumulative total of 14. skip this step when total supply_depot count reaches 1.
- Train `scv` to a cumulative total of 15. skip this step when ready supply_depot count reaches 1.
- Train `scv` to a cumulative total of 19. skip this step when ready barracks count reaches 1.
- When ready barracks count reaches 1, morph eligible `command_center` structures into `orbital_command`.
- Train `scv` to a cumulative total of 22. skip this step when total command_center count reaches 2.
- Train `scv` to a cumulative total of 66.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 5. activate only when total barracks count reaches 2.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 2. activate only when total barracks count reaches 2.
- Build `starport_reactor` on `starport` to a cumulative add-on total of 1. activate only when total barracks count reaches 2.
- When supply used reaches 14, build `supply_depot` to a cumulative total of 1.
- Build `refinery` to a cumulative total of 1.
- Build `barracks` to a cumulative total of 1.
- Build `refinery` to a cumulative total of 2.
- Build `factory` to a cumulative total of 1.
- Build `supply_depot` to a cumulative total of 2.
- Expand to a cumulative total of 2 `command_center` structures.
- Build `supply_depot` to a cumulative total of 3.
- Build `starport` to a cumulative total of 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 1.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 1. skip this step when Once(Any(UnitReady(TECHLAB), UnitReady(FACTORYTECHLAB))).
- Build `refinery` to a cumulative total of 3.
- When game time reaches 260 seconds, expand to a cumulative total of 3 `command_center` structures.
- When total raven count reaches 2, including queued units, including lost units, build `barracks_reactor` on `barracks` to a cumulative add-on total of 1. skip this step when total barracks_techlab count reaches 1.
- When total viking count reaches 2, including queued units, including lost units, build `barracks` to a cumulative total of 3.
- When total viking count reaches 4, including queued units, including lost units, build `engineering_bay` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 6.
- When total viking count reaches 6, including queued units, build `barracks` to a cumulative total of 5.
- When combat_shield research is at least 60% complete, build `factory` to a cumulative total of 2.
- When infantry_weapons_1 research is at least 60% complete, build `armory` to a cumulative total of 1.

### Army production

- When total factory count reaches 1, including queued units, train `reaper` to a cumulative total of 3. This target is issued only once.
- When total factory count reaches 1, including queued units, train `hellion` to a cumulative total of 2. This target is issued only once.
- Train `cyclone` to a cumulative total of 1. This target is issued only once.
- Train `raven` to a cumulative total of 2. This target is issued only once.
- When total cyclone count reaches 1, including lost units, train `siege_tank` to a cumulative total of 5. This target is issued only once.
- When total raven count reaches 2, including lost units, train `viking` to a cumulative total of 6. This target is issued only once.
- Train `marine` to a cumulative total of 100.
- Train `siege_tank` to a cumulative total of 10.
- Train `medivac` to a cumulative total of 4.
- Train `viking` to a cumulative total of 6.
- Train `liberator` to a cumulative total of 2.
- Train `viking` to a cumulative total of 20.

### Research

- Research `stimpack`.
- Research `infantry_weapons_1`.
- Research `infantry_armor_1`.
- Research `combat_shield`.
- Research `vehicle_weapons_1`.
- Research `infantry_weapons_2`.
- Research `infantry_armor_2`.
- Research `vehicle_weapons_2`.
- Research `infantry_weapons_3`.
- Research `infantry_armor_3`.
- Research `vehicle_weapons_3`.
- Research `ship_weapons_1`.
- Research `ship_weapons_2`.
- Research `ship_weapons_3`.

### Tactical behavior

- Distribute workers with parameter `6`. skip this step when ready barracks count reaches 1.
- Distribute workers with parameter `4`. activate only when ready barracks count reaches 1; skip this step when total command_center count reaches 2.
- Distribute workers. activate only when total command_center count reaches 2.
- Manage Terran add-on swaps. skip this step when total command_center count reaches 3, including lost units, including queued units.
- Manage Terran add-on swaps. activate only when total raven count reaches 2, including lost units; skip this step when total barracks count reaches 5, including lost units.
- Manage Terran add-on swaps. activate only when total barracks count reaches 5, including lost units; skip this step when combat_shield research is complete.
- Manage Terran add-on swaps. activate only when combat_shield research is complete.
- Call MULEs with parameter `50`.
- Lower Supply Depots.
- Clear blocked base minerals.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Defend owned zones.
- When stimpack research is complete, use `4` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

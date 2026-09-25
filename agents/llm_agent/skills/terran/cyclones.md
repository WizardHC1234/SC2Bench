---
name: cyclones
race: terran
kind: strategy
---

# Cyclones

## Objective

Execute the `Cyclones` plan through the cumulative targets and conditions below.

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

## Cyclone Bot plan

### Economy and structures

- When supply used reaches 13, build `supply_depot` to a cumulative total of 1.
- When supply used reaches 16, expand to a cumulative total of 2 `command_center` structures.
- When supply used reaches 18, build `barracks` to a cumulative total of 1.
- Build `refinery` to a cumulative total of 1.
- When supply used reaches 20, build `supply_depot` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 2. activate only when total marine count reaches 2.
- Build `factory` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- Build `factory` to a cumulative total of 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 1.
- Build `refinery` to a cumulative total of 4.
- Expand to a cumulative total of 3 `command_center` structures.
- Build `factory` to a cumulative total of 2.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 2.
- Build `refinery` to a cumulative total of 5.
- Build `refinery` to a cumulative total of 6. Skip this step when vespene reaches 100.
- When minerals reach 400, build `factory` to a cumulative total of 4.
- Build `factory_reactor` on `factory` to a cumulative add-on total of 1.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 3.
- When minerals reach 400, expand to a cumulative total of 4 `command_center` structures.
- Build `engineering_bay` to a cumulative total of 1.
- Build `refinery` to a cumulative total of 8.
- Build `factory` to a cumulative total of 6.
- When minerals reach 400, build `factory` to a cumulative total of 8.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 7.
- When supply used reaches 120, build `armory` to a cumulative total of 2.
- When total command_center count reaches 2, morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Morph eligible `command_center` structures into `planetary_fortress`. activate only when ready engineering_bay count reaches 1.
- Train `scv` to a cumulative total of 40.
- When total command_center count reaches 3, train `scv` to a cumulative total of 70.

### Army production

- Train `marine` to a cumulative total of 4.
- Train `cyclone` to a cumulative total of 4.
- Train `hellion` to a cumulative total of 1. This target is issued only once.
- Train `cyclone` to a cumulative total of 4.
- Train `cyclone` to a cumulative total of 120.
- When ready factory_reactor count reaches 1, train `hellion` to a cumulative total of 60. activate only when minerals reach 300.

### Research

- Research `mag_field_accelerator`. activate only when ready factory_techlab count reaches 1.
- Research `infernal_pre_igniter`. activate only when ready factory_techlab count reaches 2.
- When ready armory count reaches 1, research `vehicle_weapons_1`.
- Research `vehicle_ship_armor_1`.
- Research `vehicle_weapons_2`.
- Research `vehicle_ship_armor_2`.
- Research `vehicle_weapons_3`.
- Research `vehicle_ship_armor_3`.

### Tactical behavior

- Use `40` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Scout with one worker. activate only when total supply_depot count reaches 1.
- Distribute workers.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Lower Supply Depots.
- Defend owned zones.
- Call MULEs with parameter `50`. skip this step when game time reaches 300 seconds.
- Call MULEs with parameter `100`. activate only when game time reaches 300 seconds.
- Scan enemy territory. activate only when game time reaches 300 seconds.
- Repair damaged assets.
- Resume interrupted construction.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

## Cyclone Bot.depots

### Economy and structures

- When free supply is at most 6, build `supply_depot` to a cumulative total of 2.
- When free supply is at most 14, build `supply_depot` to a cumulative total of 4.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 6.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 7.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 10.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 12.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 14.
- When free supply is at most 20, build `supply_depot` to a cumulative total of 16.
- When ready supply_depot count reaches 16, build `supply_depot` to a cumulative total of 20.

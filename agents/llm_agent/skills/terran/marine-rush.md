---
name: marine-rush
race: terran
kind: strategy
---

# Marine Rush

## Objective

Execute the `Marine Rush` plan through the cumulative targets and conditions below.

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

## Marine Rush Bot plan

### Economy and structures

- when the proxy two-Barracks bunker branch is selected: when supply used reaches 12, build `supply_depot` to a cumulative total of 1.
- when the proxy two-Barracks bunker branch is selected: build `supply_depot` to a cumulative total of 2.
- when the proxy two-Barracks bunker branch is selected: when minerals reach 225, build `barracks` to a cumulative total of 6.
- when not (the proxy two-Barracks bunker branch is selected); when the 20-Marine all-in branch is selected: when supply used reaches 14, build `supply_depot` to a cumulative total of 1.
- when not (the proxy two-Barracks bunker branch is selected); when the 20-Marine all-in branch is selected: when ready supply_depot count reaches 1, build `barracks` to a cumulative total of 1.
- when not (the proxy two-Barracks bunker branch is selected); when the 20-Marine all-in branch is selected: build `supply_depot` to a cumulative total of 2.
- when not (the proxy two-Barracks bunker branch is selected); when the 20-Marine all-in branch is selected: build `barracks` to a cumulative total of 6.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): when supply used reaches 14, build `supply_depot` to a cumulative total of 1.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): when ready supply_depot count reaches 1, build `barracks` to a cumulative total of 1.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): build `supply_depot` to a cumulative total of 2.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): when minerals reach 225, build `barracks` to a cumulative total of 6.
- Morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Train `scv` to a cumulative total of 20.

### Army production

- Train `marine` to a cumulative total of 200.

### Tactical behavior

- when the proxy two-Barracks bunker branch is selected: use `3` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- when not (the proxy two-Barracks bunker branch is selected); when the 20-Marine all-in branch is selected: use `20` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): use `10` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
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

### Steps without a direct action

- when the proxy two-Barracks bunker branch is selected: the plan requires an exact position for `barracks`; this step has no direct high-level action equivalent.
- when the proxy two-Barracks bunker branch is selected: the plan requires an exact position for `barracks`; this step has no direct high-level action equivalent.
- when the proxy two-Barracks bunker branch is selected: the plan requires an exact position for `barracks`; this step has no direct high-level action equivalent.
- when the proxy two-Barracks bunker branch is selected: when ready marine count reaches 1, the plan requires an exact position for `bunker`; this step has no direct high-level action equivalent.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): the plan requires an exact position for `barracks`; this step has no direct high-level action equivalent.
- when not (the proxy two-Barracks bunker branch is selected); when not (the 20-Marine all-in branch is selected): the plan requires an exact position for `barracks`; this step has no direct high-level action equivalent.

## Attack behavior

- The source selects one of three build branches: proxy two-Barracks bunker pressure with initial army-power value 3, a 20-Marine all-in with initial army-power value 20, or a proxy-Barracks branch with initial army-power value 10.
- Before 5:00, the two proxy branches change the gather point to the own natural expansion's gather point.
- During an attack, if a Force Field is within distance 5 of the bottom of the enemy main ramp, send all attacking units to the own natural gather point with a defensive retreat.
- The proxy Barracks and bunker steps use map-coordinate placement chosen by the source. Those positions have no direct high-level build action.

---
name: battle-cruisers
race: terran
kind: strategy
---

# Battlecruisers

## Objective

Execute the `Battlecruisers` plan through the cumulative targets and conditions below.

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

## Battle Cruisers plan

### Economy and structures

- Morph eligible `command_center` structures into `orbital_command`. activate only when ready barracks count reaches 1.
- Train `scv` to a cumulative total of 46.
- When supply used reaches 13, build `supply_depot` to a cumulative total of 1.
- When one supply_depot is at least 95% complete, build `barracks` to a cumulative total of 1.
- Build `refinery` to a cumulative total of 1.
- Expand to a cumulative total of 2 `command_center` structures.
- When supply used reaches 20, build `supply_depot` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 2.
- Build `factory` to a cumulative total of 1. activate only when ready barracks count reaches 1.
- When ready factory count reaches 1, build `starport` to a cumulative total of 1.
- Build `bunker` defensively to a cumulative total of 1.
- Build `barracks` to a cumulative total of 2.
- Build `refinery` to a cumulative total of 3.
- Build `factory_techlab` on `factory` to a cumulative add-on total of 1.
- When ready starport count reaches 1, build `fusion_core` to a cumulative total of 1.
- Build `starport_techlab` on `starport` to a cumulative add-on total of 1.
- Build `refinery` to a cumulative total of 4. Skip this step when total battlecruiser count reaches 1, including lost units, including queued units.
- When total battlecruiser count reaches 1, including lost units, build `barracks` to a cumulative total of 3.
- Build `barracks_techlab` on `barracks` to a cumulative add-on total of 1.
- Build `barracks_reactor` on `barracks` to a cumulative add-on total of 1.
- Build `starport` to a cumulative total of 2.
- When ready starport count reaches 2, build `starport_techlab` on `starport` to a cumulative add-on total of 2.
- When minerals reach 600, build `barracks` to a cumulative total of 5.
- Expand to a cumulative total of 3 `command_center` structures.

### Army production

- When ready starport count reaches 1, train `raven` to a cumulative total of 2.
- Train `battlecruiser` to a cumulative total of 20. activate only when ready fusion_core count reaches 1.
- Train `siege_tank` to a cumulative total of 10.
- Train `marine` to a cumulative total of 50.

### Research

- Research `combat_shield`.

### Tactical behavior

- Use a randomly selected integer from 50 through 80 as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Scout with one worker. activate only when total supply_depot count reaches 1.
- Distribute workers with parameter `4`.
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

- Use the Battlecruiser tactical-jump routine. skip this step when the non-jump build branch is selected.

## Explicit conditional rules

- The attack-strength value is a random integer from 50 through 80.

## Tactical jump behavior

- The source chooses either the normal branch or the tactical-jump branch.
- In the tactical-jump branch, wait until more than 1 Battlecruiser exists, then jump every current Battlecruiser to the point behind the enemy main mineral line. This jump is issued once.
- Tactical Jump is controlled below the action interface, so it is not a separate production or combat action in this Skill.

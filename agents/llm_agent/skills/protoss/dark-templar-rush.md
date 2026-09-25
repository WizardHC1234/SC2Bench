---
name: dark-templar-rush
race: protoss
kind: strategy
---

# Dark Templar Rush

## Objective

Execute the `Dark Templar Rush` plan through the cumulative targets and conditions below.

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

## Dark Templar Rush plan

### Economy and structures

- When ready gateway count reaches 1, build `cybernetics_core` to a cumulative total of 1.
- When ready cybernetics_core count reaches 1, build `twilight_council` to a cumulative total of 1.
- When ready twilight_council count reaches 1, build `dark_shrine` to a cumulative total of 1.
- Build `nexus` to a cumulative total of 1. skip this step when total nexus count reaches 1.
- Train `probe` to a cumulative total of continuous. skip this step when total probe count reaches 14.
- Train `probe` to a cumulative total of continuous. skip this step when total probe count reaches 22.
- When the own main base is running low on minerals, expand to a cumulative total of 2 `nexus` structures.
- Train `probe` to a cumulative total of continuous. skip this step when total probe count reaches 30.
- Build `gateway` to a cumulative total of 5.
- Build `assimilator` to a cumulative total of 3.
- Build `gateway` to a cumulative total of 6.
- When supply used reaches 14, build `pylon` to a cumulative total of 1. skip this step when total pylon count reaches 1.
- When supply used reaches 16, build `assimilator` to a cumulative total of 1.
- When supply used reaches 16, build `gateway` to a cumulative total of 1.
- Build `assimilator` to a cumulative total of 2.
- When supply used reaches 21, build `pylon` to a cumulative total of 2. skip this step when total pylon count reaches 2.
- Build `gateway` to a cumulative total of 2.
- Build `gateway` to a cumulative total of 3.
- Maintain Pylon supply as production grows.

### Army production

- Train `dark_templar` to a cumulative total of 4. activate only when ready dark_shrine count reaches 1.
- When ready gateway count reaches 1, train `zealot` to a cumulative total of 1. skip this step when warp_gate research is at least 100% complete.
- Train `stalker` to a cumulative total of continuous.
- When total twilight_council count reaches 1, train `stalker` to a cumulative total of 3. skip this step when warp_gate research is at least 100% complete.
- When minerals reach 400, train `zealot` to a cumulative total of continuous.

### Research

- Research `blink`.
- Research `charge`.
- When ready cybernetics_core count reaches 1, research `warp_gate`.

### Tactical behavior

- Chrono Boost `probe` production. skip this step when total probe count reaches 20, including lost units; activate only when ready pylon count reaches 1.
- Chrono Boost active research with parameter `0`.
- Use `20` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

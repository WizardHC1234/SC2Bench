---
name: void-ray
race: protoss
kind: strategy
---

# Void Ray

## Objective

Execute the `Void Ray` plan through the cumulative targets and conditions below.

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

## Macro Voidray plan

### Economy and structures

- Train `probe` to a cumulative total of 14.
- Build `pylon` to a cumulative total of 1.
- Train `probe` to a cumulative total of 16.
- Build `assimilator` to a cumulative total of 1.
- Build `gateway` to a cumulative total of 1.
- Train `probe` to a cumulative total of 20.
- Build `cybernetics_core` to a cumulative total of 1.
- Train `probe` to a cumulative total of 21.
- Expand to a cumulative total of 2 `nexus` structures.
- Train `probe` to a cumulative total of 22.
- Build `assimilator` to a cumulative total of 2.
- Build `pylon` to a cumulative total of 1.
- Maintain Pylon supply as production grows.
- Train `probe` to a cumulative total of 22.
- When total nexus count reaches 2, train `probe` to a cumulative total of 44.
- Build `assimilator` to a cumulative total of 3.
- When total nexus count reaches 3, train `probe` to a cumulative total of 56.
- Build `assimilator` to a cumulative total of 5.
- When ready cybernetics_core count reaches 1, build `twilight_council` to a cumulative total of 1.
- Build `stargate` to a cumulative total of 1.
- When game time reaches 300 seconds, expand to a cumulative total of 3 `nexus` structures.
- Build `gateway` to a cumulative total of 4.
- Build `assimilator` to a cumulative total of 4.
- Build `stargate` to a cumulative total of 2.

### Army production

- Train `stalker` to a cumulative total of 2.
- Train `void_ray` to a cumulative total of 20.
- Train `zealot` to a cumulative total of 6.
- Train `adept` to a cumulative total of 10.
- Train `zealot` to a cumulative total of 15.
- Train `adept` to a cumulative total of 20.
- Train `zealot` to a cumulative total of 23.
- Train `adept` to a cumulative total of 30.

### Research

- Research `warp_gate`.
- When ready twilight_council count reaches 1, research `charge`.
- When ready twilight_council count reaches 1, research `resonating_glaives`.

### Tactical behavior

- Use `4` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Chrono Boost `probe` production. skip this step when total probe count reaches 30, including queued units; activate only when total assimilator count reaches 1.
- Chrono Boost `void_ray` production.
- Clear blocked base minerals.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

---
name: macro-stalkers
race: protoss
kind: strategy
---

# Macro Stalkers

## Objective

Execute the `Macro Stalkers` plan through the cumulative targets and conditions below.

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

## Macro Stalkers plan

### Economy and structures

- Train `probe` to a cumulative total of 14.
- Build `pylon` to a cumulative total of 1.
- Train `probe` to a cumulative total of 16.
- Build `assimilator` to a cumulative total of 1.
- Build `gateway` to a cumulative total of 1.
- Train `probe` to a cumulative total of 20.
- Expand to a cumulative total of 2 `nexus` structures.
- Build `cybernetics_core` to a cumulative total of 1.
- Train `probe` to a cumulative total of 21.
- Build `assimilator` to a cumulative total of 2.
- Train `probe` to a cumulative total of 22.
- Build `pylon` to a cumulative total of 1.
- Maintain Pylon supply as production grows.
- Train `probe` to a cumulative total of 22.
- When total nexus count reaches 2, train `probe` to a cumulative total of 44.
- Build `assimilator` to a cumulative total of 3.
- Build `gateway` to a cumulative total of 7.
- Build `assimilator` to a cumulative total of 4.

### Army production

- Train `stalker` to a cumulative total of 100.

### Research

- Research `warp_gate`.

### Tactical behavior

- Chrono Boost `probe` production. skip this step when total probe count reaches 40, including queued units; activate only when total assimilator count reaches 1.
- Clear blocked base minerals.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- When ready gateway count reaches 4, use `4` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

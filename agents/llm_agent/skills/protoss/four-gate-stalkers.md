---
name: four-gate-stalkers
race: protoss
kind: strategy
---

# Four Gate Stalkers

## Objective

Execute the `Four Gate Stalkers` plan through the cumulative targets and conditions below.

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

## Stalkers4 Gate plan

### Economy and structures

- Train `probe` to a cumulative total of 14.
- Build `pylon` to a cumulative total of 1.
- Train `probe` to a cumulative total of 16.
- Build `gateway` to a cumulative total of 1.
- Train `probe` to a cumulative total of 17.
- Build `assimilator` to a cumulative total of 2.
- Train `probe` to a cumulative total of 19.
- Build `gateway` to a cumulative total of 2.
- Maintain Pylon supply as production grows.
- Train `probe` to a cumulative total of 22.
- When ready cybernetics_core count reaches 1, build `twilight_council` to a cumulative total of 1.
- Build `cybernetics_core` to a cumulative total of 1. activate only when ready gateway count reaches 1.
- When total cybernetics_core count reaches 1, build `gateway` to a cumulative total of 4.

### Army production

- When ready cybernetics_core count reaches 1, train `adept` to a cumulative total of 2. This target is issued only once.
- Train `stalker` to a cumulative total of 100.

### Research

- When ready twilight_council count reaches 1, research `blink`.
- Research `warp_gate`.

### Tactical behavior

- Use `6` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Chrono Boost `probe` production. skip this step when total probe count reaches 20, including queued units; activate only when total assimilator count reaches 1.
- Clear blocked base minerals.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

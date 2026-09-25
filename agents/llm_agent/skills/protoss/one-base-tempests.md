---
name: one-base-tempests
race: protoss
kind: strategy
---

# One Base Tempests

## Objective

Execute the `One Base Tempests` plan through the cumulative targets and conditions below.

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

## One Base Tempests plan

### Economy and structures

- Train `probe` to a cumulative total of 14.
- Build `pylon` to a cumulative total of 1.
- Train `probe` to a cumulative total of 15.
- Build `gateway` to a cumulative total of 1.
- Build `forge` to a cumulative total of 1.
- Build `assimilator` to a cumulative total of 2.
- Train `probe` to a cumulative total of 18.
- Build `pylon` to a cumulative total of 2.
- Build `cybernetics_core` to a cumulative total of 1.
- Train `probe` to a cumulative total of 22.
- Maintain Pylon supply as production grows.
- Build `stargate` to a cumulative total of 1.
- When ready stargate count reaches 1, build `fleet_beacon` to a cumulative total of 1.
- When total fleet_beacon count reaches 1, build `stargate` to a cumulative total of 2.

### Army production

- Train `tempest` to a cumulative total of 100.

### Tactical behavior

- Use `4` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Chrono Boost `tempest` production.
- Clear blocked base minerals.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- Search for and destroy remaining enemy structures.

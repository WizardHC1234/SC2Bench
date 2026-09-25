---
name: disruptor
race: protoss
kind: strategy
---

# Disruptor

## Objective

Execute the `Disruptor` plan through the cumulative targets and conditions below.

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

## Distruptor Build setup

### Economy and structures

- Train `probe` to a cumulative total of 22.
- When total nexus count reaches 2, train `probe` to a cumulative total of 44.
- When ready pylon count reaches 1, maintain Pylon supply as production grows.
- Build `pylon` to a cumulative total of 1.
- Build `gateway` to a cumulative total of 2.
- Build `assimilator` to a cumulative total of 2.
- Build `cybernetics_core` to a cumulative total of 1.
- Build `robotics_facility` to a cumulative total of 1.
- Build `robotics_bay` to a cumulative total of 1.
- When total disruptor count reaches 1, including lost units, expand to a cumulative total of 2 `nexus` structures.
- Build `assimilator` to a cumulative total of 4.
- When minerals reach 300, build `gateway` to a cumulative total of 3.
- When ready nexus count reaches 2, build `gateway` to a cumulative total of 6.

### Army production

- Train `immortal` to a cumulative total of 1. This target is issued only once.
- Train `observer` to a cumulative total of 1.
- Train `disruptor` to a cumulative total of 4.
- Train `stalker` to a cumulative total of continuous.

### Research

- Research `warp_gate`.

### Tactical behavior

- When ready pylon count reaches 1, chrono Boost `probe` production. skip this step when total probe count reaches 19.
- Chrono Boost `immortal` production. skip this step when total immortal count reaches 1, including lost units.
- Chrono Boost `observer` production. skip this step when total observer count reaches 1, including lost units.
- Chrono Boost `disruptor` production. skip this step when total disruptor count reaches 1, including lost units.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Restore power to unpowered structures.
- Distribute workers.
- Defend owned zones.
- Gather free army before attacking.
- When total disruptor count reaches 1, including lost units, use `20` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

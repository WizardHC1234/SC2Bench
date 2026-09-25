---
name: adept-allin
race: protoss
kind: strategy
---

# Adept All-In

## Objective

Execute the `Adept All-In` plan through the cumulative targets and conditions below.

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

## Adept Rush plan

### Economy and structures

- Build `pylon` to a cumulative total of 1.
- Train `probe` to a cumulative total of 14.
- Build `gateway` to a cumulative total of 1.
- Train `probe` to a cumulative total of 16.
- Build `assimilator` to a cumulative total of 1.
- Train `probe` to a cumulative total of 17.
- Build `gateway` to a cumulative total of 2.
- Train `probe` to a cumulative total of 20.
- Build Pylons to the plan's cumulative target of 2.
- Maintain Pylon supply as production grows.
- Build `cybernetics_core` to a cumulative total of 1. activate only when ready gateway count reaches 1.
- When total cybernetics_core count reaches 1, build `gateway` to a cumulative total of 4. activate only when minerals reach 200.

### Army production

- When ready cybernetics_core count reaches 1, train `adept` to a cumulative total of 2. This target is issued only once.
- Train `adept` to a cumulative total of 100.
- Train `zealot` to a cumulative total of 100. skip this step when vespene reaches 25; activate only when minerals reach 200.

### Research

- Research `warp_gate`.

### Tactical behavior

- Start attacking when the own Adept count is greater than 10.
- Chrono Boost `probe` production. skip this step when total probe count reaches 20, including queued units; activate only when total assimilator count reaches 1.
- Clear blocked base minerals.
- Chrono Boost active research with parameter `0`.
- Defend owned zones.
- Restore power to unpowered structures.
- Distribute workers.
- Gather free army before attacking.
- Begin Adept harassment when the randomly selected 10 to 15 idle Adepts are available.
- Search for and destroy remaining enemy structures.

## Explicit conditional rules

- The attack starts when own Adept count is greater than 10; the randomly selected scout count does not change this trigger.
- The Adept scout count is selected randomly from 10 through 15.

---
name: cannon-transition
race: protoss
kind: strategy
---

# Cannon Rush

## Objective

Execute the `Cannon Rush` plan through the cumulative targets and conditions below.

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

## Cannon Rush plan

### Economy and structures

- Train `probe` to a cumulative total of 13.
- Build `pylon` to a cumulative total of 1.
- Expand to a cumulative total of 2 `nexus` structures.
- Train `probe` to a cumulative total of 30.
- When total nexus count reaches 2, train `probe` to a cumulative total of 44.
- Build `gateway` to a cumulative total of 2.
- Build `cybernetics_core` to a cumulative total of 1.
- Build `assimilator` to a cumulative total of 2.
- Maintain Pylon supply as production grows.
- Build `assimilator` to a cumulative total of 3.
- Train `probe` to a cumulative total of 22.
- When total nexus count reaches 2, train `probe` to a cumulative total of 44.
- Build `assimilator` to a cumulative total of 3.
- When ready cybernetics_core count reaches 1, build `twilight_council` to a cumulative total of 1.
- When ready cybernetics_core count reaches 1, build `gateway` to a cumulative total of 7.
- Build `assimilator` to a cumulative total of 4.

### Army production

- Train `stalker` to a cumulative total of 4.
- Train `stalker` to a cumulative total of 100.

### Research

- Research `warp_gate`.
- When ready twilight_council count reaches 1, research `blink`.

### Tactical behavior

- Chrono Boost `probe` production. skip this step when total probe count reaches 16; activate only when ready pylon count reaches 1.
- Chrono Boost active research with parameter `0`.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Defend owned zones.
- Distribute workers.
- Gather free army before attacking.
- Use `6` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Search for and destroy remaining enemy structures.

## Cannon Rush.cannon rush

### Economy and structures

- Build `pylon` to a cumulative total of 1.
- Build `forge` to a cumulative total of 1.
- Train `probe` to a cumulative total of 18.
- When minerals reach 400, build `gateway` to a cumulative total of 1.
- When minerals reach 700, expand to a cumulative total of 2 `nexus` structures. skip this step when total nexus count reaches 2.
- Build `cybernetics_core` to a cumulative total of 1.

### Tactical behavior

- Chrono Boost `probe` production.

## Cannon Rush.cannon expand

### Economy and structures

- Train `probe` to a cumulative total of 14. This target is issued only once.
- Build `forge` to a cumulative total of 1.
- Train `probe` to a cumulative total of 18.
- Expand to a cumulative total of 2 `nexus` structures.
- Build `gateway` to a cumulative total of 1.

### Steps without a direct action

- The plan requires an exact position for `pylon`; this step has no direct high-level action equivalent.
- The plan requires an exact position for `photon_cannon`; this step has no direct high-level action equivalent. skip this step when at least one own Pylon has been lost.

## Cannon placement behavior

- Two reserved Probes are used: one advances the Pylon chain and one advances the Photon Cannon chain.
- Candidate Pylon positions run from the enemy natural toward the enemy main and across the enemy ramp. Against Zerg, the final main-base point is 10 distance units from the enemy main toward the ramp; against other races, three additional points are used inside the main approach.
- The Cannon worker builds near the current Pylon-chain target. From the third target onward, the chain requires 2 Cannons near each target before advancing.
- The rush stage stops after at least 3 own Probes have been lost or game time passes 4:00; the selected macro transition then continues.
- These coordinate-level placements and individual Probe movements have no direct high-level action equivalent.

---
name: worker-rush
race: zerg
kind: strategy
---

# Worker Rush

## Objective

Execute the `Worker Rush` plan through the cumulative targets and conditions below.

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

## Ling Flood Build setup

### Economy and structures

- When total hatchery count reaches 2, build `extractor` to a cumulative total of 1.
- Build `spawning_pool` to a cumulative total of 1.
- When total zergling count reaches 4, including lost units, expand to a cumulative total of 2 `hatchery` structures.
- When total drone count reaches 24, including lost units, expand to a cumulative total of 3 `hatchery` structures.
- When total drone count reaches 30, including lost units, expand to a cumulative total of 4 `hatchery` structures.
- Train `drone` to a cumulative total of 35.
- Morph eligible `hatchery` structures into `lair`.
- Train `drone` to a cumulative total of 40.
- Build `extractor` to a cumulative total of 3.
- Build `spire` to a cumulative total of 1.
- When total spawning_pool count reaches 1, train `overlord` to a cumulative total of 2. skip this step when total overlord count reaches 2.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 14. skip this step when total drone count reaches 14.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 20.
- When total spawning_pool count reaches 1, train `drone` to a cumulative total of 30.

### Army production

- Train `queen` to a cumulative total of 2.
- Train `queen` to a cumulative total of 3.
- Train `queen` to a cumulative total of 4.
- Train `mutalisk` to a cumulative total of 10.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 4.
- When total spawning_pool count reaches 1, train `queen` to a cumulative total of 1. skip this step when total queen count reaches 1.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of 12. This target is issued only once.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of continuous.

### Research

- Research `metabolic_boost`. activate only when vespene reaches 100.

## Worker Rush plan

### Economy and structures

- Train `drone` to a cumulative total of 24. This target is issued only once.

### Tactical behavior

- Inject Larva.
- Distribute workers with parameter `3`. skip this step when (stop_gas or end_game).
- Distribute workers with parameter `0`. activate only when stop_gas; skip this step when end_game.
- Distribute workers with parameter `None`. activate only when end_game.

## Attack behavior

- On the first iteration, `WorkerAttack` records every current Drone tag and continues controlling the surviving tagged Drones.
- Until enemy lost minerals reach 50, those Drones target the point behind the enemy main minerals with `PanicRetreat`. After that threshold, they target the closest enemy structure there with `Assault`.
- A tagged Drone above 5 health and more than 20 distance units from the enemy main center moves toward that center. A Drone below 6 health gathers from the closest mineral field at the enemy natural. If a non-structure enemy is within 4, it becomes the assault target.
- The follow-up attack starts when idle Zerglings reach 6 before 9:00, 12 from 9:00 to 12:59, 20 from 13:00 to 16:59, or 25 from 17:00 onward.
- During the follow-up all-out attack, send Zerglings, Roaches and Mutalisks. End the attack state when no Zerglings remain.
- Defend the first owned zone found under attack. Against a ground presence, send Zerglings, Roaches, Queens and Mutalisks; when only enemy air units are present, send Queens.
- The available combat action cannot dispatch workers, so the direct Drone-control portion has no executable high-level equivalent.

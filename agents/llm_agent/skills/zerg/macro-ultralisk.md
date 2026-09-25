---
name: macro-ultralisk
race: zerg
kind: strategy
---

# Macro Zerg V2

## Objective

Execute the `Macro Zerg V2` plan through the cumulative targets and conditions below.

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

## Macro Build setup

### Economy and structures

- Train `drone` to a cumulative total of 100. skip this step when total hive count reaches 1.
- Train `drone` to a cumulative total of 50.
- Expand to a cumulative total of 999 `hatchery` structures.
- Build `spawning_pool` to a cumulative total of 1.
- Build `extractor` to a cumulative total of 2.
- When vespene reaches 120, build `evolution_chamber` to a cumulative total of 2.
- Morph eligible `hatchery` structures into `lair`. skip this step when total hive count reaches 1.
- Build `extractor` to a cumulative total of 4.
- Build `infestation_pit` to a cumulative total of 1.
- When ready infestation_pit count reaches 1, morph eligible `lair` structures into `hive`.
- Build `extractor` to a cumulative total of 6.
- Build `ultralisk_cavern` to a cumulative total of 1.

### Army production

- When vespene reaches 500, train `ultralisk` to a cumulative total of continuous.
- When total spawning_pool count reaches 1, train `zergling` to a cumulative total of continuous.
- When minerals reach 500, train `queen` to a cumulative total of 5.

### Research

- Research `metabolic_boost`.
- When total evolution_chamber count reaches 1, research `melee_attacks_1`.
- Research `ground_carapace_1`.
- Research `melee_attacks_2`.
- Research `ground_carapace_2`.
- When ready hive count reaches 1, research `adrenal_glands`.
- Research `melee_attacks_3`.
- Research `ground_carapace_3`.
- Research `chitinous_plating`.
- Research `anabolic_synthesis`.

## Macro Zerg V2 plan

### Tactical behavior

- Use `120` as the minimum strength value in the attack check against known enemy forces; attack regardless when supply used is above 190.
- Clear blocked base minerals.
- Cancel unsafe or invalid construction.
- Inject Larva.
- Distribute workers.
- Search for and destroy remaining enemy structures.

---
name: roach-rush
race: zerg
kind: strategy
---

# Roach Rush

## Objective

Execute the scripted one-base Roach rush. The opening is a strict sequence; after it ends, the production rules run continuously.

## Procedure

1. Process only the current opening item. The source returns without advancing when minerals are below 25, the item is unaffordable, or the Spawning Pool prerequisite for the Roach Warren or Queen is not ready.
2. After the opening reaches `END`, maintain supply, inject Larva, produce the army and replace workers according to the exact conditions below.
3. The army contains only Roaches and Zerglings. The script does not define an army-size threshold before moving out.
4. Issue only the missing production for the current condition and end the decision with `advance`.

## Opening sequence

1. Train 1 Drone.
2. Build 1 Spawning Pool.
3. Train 1 Drone.
4. Train 1 Drone.
5. Build 1 Extractor.
6. Train 1 Drone.
7. Train 1 Overlord.
8. Build 1 Roach Warren. Wait until the Spawning Pool is ready.
9. Train 1 Queen. Wait until the Spawning Pool is ready.
10. Train 1 Overlord.

Buildings use one Drone. The script places the Spawning Pool and Roach Warren toward the main ramp and selects the closest free geyser for the Extractor; exact placement is not a high-level action.

## Production after the opening

- If the Spawning Pool is ready, no Queen exists or is pending, a Hatchery is idle and a Queen is affordable, train 1 Queen and stop production for that decision.
- If Larva exists and the Roach Warren is ready, train 1 Roach whenever a Roach is affordable, then stop production for that decision.
- If a Roach is unaffordable, minerals are at least 50 and vespene is at most 8, spend one Larva on a Zergling production order, then stop production for that decision.
- Once at least one ready Roach exists, if worker supply plus pending Drones is below 16 and a Drone is affordable, train 1 Drone.
- For supply, train 1 Overlord only when it is affordable, Larva exists, supply cap is below 200, and `supply_left + pending_overlords * 8 < 2 + supply_used // 7`.
- Use `inject_larva` with idle Queens whenever the ability is available.

## Economy

- Fill missing Extractor worker slots from collecting Drones that are not carrying resources.
- Move surplus Extractor workers back to the nearest mineral patch.
- The script has no expansion step.
- If all Drones die, the script surrenders.

## Combat behavior

- Dispatch every Roach and Zergling; the script does not wait for a fixed first-wave count.
- The army can fight only ground targets. It excludes Larvae and Eggs from target selection.
- If combat-capable enemies or Bunkers, Spine Crawlers or Photon Cannons are present, move to the closest threat.
- When several enemies are in range, target workers first, then the lowest-health target.
- Roaches attack when their weapon is ready and move away during cooldown; Zerglings focus the lowest-health target in range.
- If no enemy unit is visible, attack known structures, prioritizing the lowest-health structure in range. If no structure is visible, search the enemy start and then the expansion locations.

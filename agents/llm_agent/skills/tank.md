# Marine–Siege Tank

Adapted from the SC2-Commander two-base Marine and Siege Tank plan. The composition and build goals stay the same. Counts are directions for this match, not one request.

## Summary

A two-base Marine-Siege Tank strategy focused on a concentrated mid-game push. Establish a moderate economy, protect the Tank core with Marines, and reinforce the attack with the same composition.

## Plan

- Economy: Take both gas geysers at each of the first two bases, for 4 Refineries, and grow toward about 44 living SCVs. Replace lost SCVs while leaving resources for Marines and Siege Tanks.
- Expansion: Take an early second Command Center and stay on two bases until the attack starts. Start the third Command Center after the attack has begun.
- Production: Reach 3 Barracks and 2 Factories. Keep producing Marines and Siege Tanks, and keep enough supply for both.
- Technology: 2 Barracks Reactors, 1 Barracks Tech Lab, and a Tech Lab on each Factory. Research Combat Shield. Stay on Barracks, Factories, and Combat Shield for this composition.
- Scouting: Send one early SCV to the enemy main when the route is reasonably safe. Before the attack, refresh the enemy natural or the first objective when that information is missing or stale. After an objective is cleared, scout the next relevant enemy base only when needed.
- Scans: Morph useful Command Centers into Orbital Commands. Scan when missing vision would change the attack, when a ground scout is unsafe, or when cloaked or burrowed enemies need detection. Do not hold a ready attack only to wait for a scan.
- Posture: Before attacking, keep available Marines and Siege Tanks together near the forward route.
- Attack: Start the push once about 30 Marines and 6 Siege Tanks are gathered and can be sent together. A small shortfall on one of those counts is acceptable. Do not wait for 45 Marines and 10 Siege Tanks, and do not attack with a token force.
- Objective: Attack one confirmed enemy base. Begin with the enemy main while it remains valid. After it is cleared, continue to the nearest known enemy base or structures.
- Reinforcement: Keep the force on the current objective. Send newly completed Marines and Siege Tanks to that same objective instead of opening a second route.
- Recovery: If the force can no longer make progress, bring the survivors home, rebuild toward about 30 Marines and 6 Siege Tanks, gather them, and attack again. After known enemy bases are gone, search the remaining expansions.
- Late game: Keep producing toward about 96 Marines, 20 Siege Tanks, and 44 SCVs. Replace combat losses until enemy structures are gone.

## On this platform

- `train` requests additional units. Read living counts and accepted production before asking for more. Do not submit 44, 96, or 20 as one count.
- Each `build` adds one structure. Supply Depots are not built for you.
- Add-ons are separate builds: `barracks_reactor`, `barracks_techlab`, and `factory_techlab`. An add-on cannot be moved to another building.
- Morph a Command Center with `upgrade` to `orbital_command`. Research `combat_shield` from a Barracks Tech Lab.
- A new attack is one `combat` order using free group_0 units against one zone. An outbound group does not pick up new units by itself. Send the next Marines and Siege Tanks with another `combat` to the same target. Use `retreat` to bring that group home.
- Siege mode and fighting are backend micro.

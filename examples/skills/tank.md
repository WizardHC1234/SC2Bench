# Marine–Siege Tank Skill

Source: SC2-Commander `skills/terran/tank/strategy.md`. The strategy below is
preserved; the final section explains its use with SC2Bench, not a new build order.

## Summary

A two-base Marine-Siege Tank strategy focused on a concentrated mid-game push. Establish a moderate economy, protect the Tank core with Marines, and reinforce the attack with the same composition.

## Details

* Opening and Economy: Build a total of 4 Refineries by taking both gas geysers at each of the first two bases, and grow to approximately 44 living SCVs. Replace lost SCVs as needed while preserving resources for Marine and Siege Tank production.
* Expansion: Build an early second Command Center and remain on two bases until the main attack begins. Start the third Command Center after the attack has begun.
* Production: Reach absolute counts of 3 Barracks and 2 Factories. Continuously produce Marines and Siege Tanks while maintaining the resources and supply required for both unit types.
* Technology: Equip 2 Barracks with Reactors, 1 Barracks with a Tech Lab, and both Factories with Tech Labs. Research Combat Shield. Keep the tech path on Barracks, Factories, and Combat Shield for the Marine-Siege Tank core.
* Scouting: Send one early SCV to scout the enemy main base when the route is reasonably safe. Before the planned attack, update the enemy natural or intended first objective when its information is missing or stale; after an objective is cleared, scout the next relevant enemy base or neutral mineral expansion only when needed.
* Scans: Upgrade useful Command Centers to Orbital Commands and request a Scanner Sweep when missing vision affects the attack, when necessary ground scouting is unsafe, or when cloaked or burrowed enemies require detection. Prefer scanning in parallel with a ready attack rather than holding the push for vision alone.
* Pre-Attack Army Posture: Before the attack gate is satisfied, keep available Marines and Siege Tanks concentrated in one safe staging zone near the forward route. Launch the planned attack only after that force is gathered and complete.
* Main Attack Gate: Begin the planned attack as soon as at least 45 completed and living Marines and 10 completed and living Siege Tanks are available. Both unit thresholds must be satisfied. These thresholds only open the offensive; Marine and Siege Tank production targets remain the Ultimate Goal counts below.
* Attack Objective: Attack the enemy main base directly and maintain this objective while it remains valid. After it is cleared, continue toward the nearest known enemy base or structure.
* Engagement and Reinforcement: After the planned attack begins, keep the concentrated force advancing toward the current objective while it can make progress. Send newly completed Marines and Siege Tanks to the same objective instead of starting a separate attack route.
* Recovery and Cleanup: If the force can no longer make progress against a superior defense, withdraw the survivors to a safe owned zone, rebuild to 45 Marines and 10 Siege Tanks, and concentrate them before attacking again. Search remaining expansions after known enemy bases are destroyed.
* Ultimate Goal: Continue toward approximately 96 Marines, 20 Siege Tanks, and 44 SCVs to fill the 200-supply limit. After the attack gate is met, retain or raise Marine and Siege Tank production toward those Ultimate Goal counts. Replace combat losses until all enemy structures are destroyed.

## SC2Bench interface adaptation

* The quantities above are strategic goals, NOT counts to resubmit every turn. Use current living inventory, accepted orders and paid queues to decide additional production. Existing work persists; cancel only matching unstarted work you no longer want. SC2Bench build entries each request one additional building.
* Home gathering is automatic in group_0. There is no independent staging/move action; a forward staging posture can only use supported combat commands. Outbound members are not free for a new dispatch: retarget their existing group, or retreat home before dispatching them again. For the initial push, check that the intended 45 Marines and 10 Tanks are actually dispatchable, not merely living or queued.
* Reinforcements are not automatically added to an outbound group. Dispatch available new units as another group to the same objective; this supports the shared attack objective, not a merged-group capability. Use returned group IDs for later orders.
* Only attack/defend styles and retreat are exposed. Select actual zone IDs from Observation; never assume enemy_main is an action target or infer ownership from a zone number. Building morphs use observed structure IDs. Placement, sieging, movement and combat micro remain backend-controlled.
* For cleanup, scout.route may be "all" to request one automatic pass over non-own expansion centers, rather than listing every zone. This checks centers, not all terrain or flying buildings; a dead SCV fails without replacement. Use discovered structures to choose army retargets yourself; the scout does not change combat orders.
* Supply Depots, training, Orbital morphs and scan/MULE spending require Agent commands. The platform does not execute this Skill or fill missing prerequisites for you. Adapt scheduling to resources, available producers, supply and actual enemy information.

"""Model-facing contract text; each rule has one presentation owner."""

ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information, scouting or scans to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, town-hall morphs, scouting, scan and MULE use, and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, repair, interrupted-construction continuation and local combat micro.
- Supply Depots, MULEs, Supply Drops, town-hall morphs, strategic scans and reinforcements require explicit actions. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

INTERACTION_RULES = """- Changes reports execution progress since the previous decision. Use Read tools for omitted match-state detail and Knowledge tools for static game data when the decision needs them.
- Queries read one frozen snapshot and do not advance time. Each Action immediately returns whether it was staged, rejected or a no-op, together with its effect on unfinished work when relevant. Staged actions are stored for this decision only; they are not paid for, started or completed yet.
- Query and Action calls made before advance use the same frozen decision snapshot. Review Action results before calling advance. Call advance exactly once as the final tool call to submit staged actions and continue the game. With no new action, call advance by itself.
"""

PLANNING_RULES = """Planning context:
- Treat each decision as part of the same continuing match. Use the current Observation, the previous Feedback and persistent accepted work rather than planning from the current resource bank alone.
- When adding work, consider its mineral and gas requirements together with continuing income, supply, prerequisites, completion times and existing unfinished work.
"""

EXECUTION_RULES = """Execution model:
- Accepted build and train requests persist across decisions until completed, cancelled or failed. Repeating one requests additional work. Check existing persistent work before adding more; cancel edits the queue by removing matching unstarted work.
- Runnable requests use resources in submission order. Missing prerequisites do not reserve resources, and waiting_for reports observed blockers.
- An invalid or infeasible Action is rejected immediately and is not staged; actions already staged in this decision remain. After advance, Changes reports execution progress, blockers, completion and failure.
- Production buildings add capacity; units and research still require explicit train or research actions.
"""

ARMY_RULES = """Army groups:
- group_0 is the permanent home pool. New army units gather at the natural expansion when available, otherwise at the main. Use its available counts for dispatch or reinforcement; units performing another platform-managed role, scouting, transporting or assigned to an outbound group are unavailable. A returned group joins it only after arriving home.
- New units do not automatically reinforce outbound groups.
- Army orders persist until changed. Reassess existing groups as objectives, positions and threats change, and issue new orders when appropriate.
- Zone numbers do not imply adjacency; query routes when path choice matters.
"""

GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed SCVs generate income while gathering resources; SCVs occupied by other work are not gathering. Additional eligible gathering SCVs increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. A unit cannot begin production while its required supply is unavailable. Query the relevant building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Unit production also requires a completed compatible producer; accepted work waits while its compatible production lanes are occupied.
- Barracks, Factories and Starports can each use one add-on. A Reactor lets its host train two compatible units in parallel, while a Tech Lab unlocks advanced units or research. An add-on cannot produce anything independently of its host building.
- Town halls train SCVs, and morphing a town hall prevents it from training them until the morph completes. SCVs construct buildings and gather resources.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

PROTOSS_ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information or scouting to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

PROTOSS_CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, scouting and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, interrupted-construction continuation, and local combat micro. warp_gate research does not morph Gateways.
- Pylons, chrono_boost and reinforcements require explicit actions. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

PROTOSS_GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed Probes generate income while gathering resources; Probes occupied by other work are not gathering. Additional eligible gathering Probes increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. A unit cannot begin production while its required supply is unavailable. Query the relevant building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Protoss production buildings also need Pylon power. Unit production requires a completed compatible producer; accepted work waits while compatible producers are busy. Gateways, Robotics Facilities and Stargates hold a short queue. A Warp Gate instead warps one unit when its cooldown is ready.
- Gateways, Robotics Facilities and Stargates do not take add-ons. build warpgate converts one completed Gateway after research. Train names stay the same, and warp-ins join group_0 at home.
- A Nexus trains Probes and builds with Probes. Probes do not repair.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

ZERG_ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information or scouting to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

ZERG_CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, scouting and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, interrupted-construction continuation, and local combat micro.
- Overlords, inject_larva, spawn_creep_tumor and reinforcements require explicit actions. Queen energy is reported. Burrow, unburrow and Overlord Generate Creep stay on the backend. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

ZERG_GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed Drones generate income while gathering resources; Drones occupied by other work are not gathering. Additional eligible gathering Drones increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing units or structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. Morphing a town hall does not add more supply. A unit cannot begin production while its required supply is unavailable. Query the relevant unit or building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Most Zerg buildings must be placed on creep. Most units hatch from shared larva. Queens train at a town hall and do not use larva. One larva order can produce more than one unit; count is the minimum requested, and Feedback reports the actual number when a batch produces more. Drones are consumed when they start a building. Unit morphs consume the source unit.
- There are no add-ons. morph_townhall changes a selected Hatchery to a Lair, or a Lair to a Hive. Lurker Den morphs a Hydralisk Den, and Greater Spire morphs a Spire.
- Baneling, Ravager, Lurker, Overseer, Brood Lord and transport Overlords morph existing units. They do not train the source unit.
- A Hatchery, Lair or Hive provides larva and trains Queens. Drones gather resources and are consumed when they start buildings. Drones do not repair.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

# Action-local behavior is exposed through each Action tool's schema.
COMBAT_DISPATCH_RULES = "dispatch_group atomically splits requested units from group_0 or another active group into a new attack or defend group. group_0 itself remains at home. attack_group and defend_group only change an existing outbound group's order."
COMBAT_RETARGET_RULES = "reinforce_group transfers a requested subset without changing the destination order; merge_groups transfers whole groups; retreat_group returns a whole outbound group home. Transfers do not pass through group_0 unless home is the destination."
COMBAT_STYLE_RULES = "attack engages and changes to defend after the target is continuously confirmed clear. defend holds locally without attacking structures or pursuing across the map. Acceptance is not arrival or success."

ACTION_RULES = {
    "build": "Request one additional structure or structure form. The platform chooses a legal placement or a valid source building. A town hall is placed as an expansion. Gas buildings require a free geyser at a ready owned town hall. Completes when construction or the structure morph starts.",
    "train": "count is the minimum number of additional target units. The platform trains, warps, hatches, morphs or merges them. A game batch may finish more than requested; Feedback then reports the requested count and the actual count. The request stays active until that minimum is met. Later losses do not reopen completed production.",
    "research": "Duplicate accepted, active or completed research requests are ignored. The request completes on queue entry; its effect becomes available only after Research reports it completed.",
    "cancel": "Remove matching unstarted build, train, research or morph_townhall work using target_action and target. Optional count removes up to that quantity from the newest matching requests first; omit count to remove all matching unstarted work. Started or paid work remains without a refund.",
    "scan": "Spend 50 energy from one ready Orbital for temporary local vision and detection in the target zone; it does not reveal the entire zone.",
    "call_mule": "Spend 50 energy from one ready Orbital to call a MULE at a safe owned mineral line. Scan, MULE and Supply Drop share that Orbital's energy.",
    "supply_drop": "Spend 50 energy from one ready Orbital to add supply to one completed living Supply Depot the platform chooses. The same Orbital energy is shared with Scan and MULE. One depot can receive one drop.",
    "chrono_boost": "Spend 50 energy from one ready Nexus to Chrono Boost one structure the backend chooses, preferring a structure that is already producing or researching. It is not cast automatically.",
    "inject_larva": "Spend 25 energy from one ready Queen to inject one town hall the backend chooses. It is not cast automatically. Transfuse remains backend combat micro.",
    "spawn_creep_tumor": "Plant one creep tumor on creep, toward the enemy. A burrowed tumor spreads the next one for free. If none can, one ready Queen spends 25 energy. It is not cast automatically.",
    "scout": 'Send one SCV along the ordered route; route="all" performs one expansion-zone sweep. A new scout command replaces existing scout work, and the scout\'s death fails the task without automatic replacement. Arrival does not guarantee full-zone visibility or that every enemy was found.',
    "morph_townhall": "Morph the selected town hall to the requested form. The request completes when the morph command is issued, not when the new form finishes.",
    "dispatch_group": "Split the requested units from group_0 or another active group into a new group and give it an attack or defend order. The split is all-or-nothing; the source keeps its own ID and order. group_0 remains the home pool.",
    "attack_group": "Order an existing outbound group to attack a zone, whether or not the zone is currently visible. This changes that group's order and does not create a group or change its membership.",
    "defend_group": "Order an existing outbound group to defend a zone. This changes that group's order and does not create a group or change its membership. Defending groups stay near the target, do not chase across the map and do not proactively attack structures.",
    "reinforce_group": "Transfer the requested units from group_0 or an active outbound source to an active destination without changing the destination order. Transfers are all-or-nothing and remain pending until the units physically join; destination group_0 means return home.",
    "merge_groups": "Move all living source members into the destination without passing through group_0. The destination keeps its ID and order. Missing, duplicate, withdrawing or recalling groups reject the whole call.",
    "retreat_group": "Return one active outbound group home. Survivors join group_0 only after arrival.",
    "advance": "End this decision: submit the actions already staged, advance the requested positive number of game seconds and return the next Observation. Episode end may return early; active work continues.",
}

# Kept for harnesses that still quote the former per-turn reminder. The live
# contract owns these rules in INTERACTION_RULES; Brief OBS only shows staged
# actions under [Decision] and does not repeat the tutorial each turn.
DECISION_REQUEST = """[Decision Request]
Review the current Observation and Changes. Each Action immediately reports its staged effect, rejection or no-op. Review those results before calling advance. Staged actions are stored for this decision only; they have not been paid for, started or completed. Call advance to submit them and move game time forward. If no new action is needed, call advance alone.
"""

# Replaceable default advice. A harness may swap PromptParts.decision_guidance
# and keep the platform contract above.
AGENT_GUIDANCE = """- Use Read or Knowledge tools when the current decision requires information omitted from the Brief Observation. Reuse information that remains valid.
"""

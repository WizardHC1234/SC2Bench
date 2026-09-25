"""Model-facing contract text; each rule has one presentation owner."""

ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information, scouting or scans to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, town-hall morphs, scouting, scan and MULE use, and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, repair, interrupted-construction continuation and local combat micro.
- Supply Depots, MULEs, town-hall morphs, strategic scans and reinforcements require explicit actions. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

INTERACTION_RULES = """- Observation describes the current state, while Feedback reports how the previous submission was handled.
- Read the current Observation and previous Feedback before choosing actions, then identify the facts needed for the current decision. Use Read tools for map, zone and route information. Use Knowledge tools for static unit, building, research, combat-statistics and technology-path information.
- If a required fact is absent from the current Observation and earlier tool results, query it before choosing actions that depend on it. Do not rely on recalled values for costs, supply, prerequisites, production sources, durations, attack targets, damage, range or research effects. Query related objects together and reuse results already obtained in this session.
- Read and Knowledge tools may be called across multiple replies until the information required for the current decision is available. A query reply may contain multiple query calls, but it must not contain Action tools.
- Once ready to act, submit all Action tool calls for the current decision in one reply. Do not split the action batch across replies, and finish it with exactly one advance call. If no new action is needed, call advance by itself so that the game can continue.
"""

PLANNING_RULES = """Planning context:
- Treat each decision as part of the same continuing match. Use the current Observation, the previous Feedback and persistent accepted work rather than planning from the current resource bank alone.
- When adding work, consider its mineral and gas requirements together with continuing income, supply, prerequisites, completion times and available production slots.
"""

EXECUTION_RULES = """Execution model:
- Accepted build and train requests remain in Production Priority across later decisions and continue when their requirements can be satisfied. Before submitting another build or train command, check whether the same work is already present. Repeating a command requests additional buildings or units; it does not remind the platform to continue an existing request. Use cancel when matching unstarted work is no longer wanted.
- Commands are registered in submission order before backend execution. Eligible requests receive available resources in that order, while a request blocked by missing prerequisites does not reserve resources. The waiting_for field reports blockers observed by the platform.
- If the submitted batch is schema-invalid, none of its actions are applied. Otherwise, inspect Feedback for accepted, normalized, rejected or failed entries, and only resubmit rejected work when it is still wanted.
- Production buildings provide capacity but do not create units by themselves. Units and research require explicit train or research commands, so constructing additional production buildings alone does not expand the army.
"""

ARMY_RULES = """Army groups:
- group_0 is the home army pool. New completed army units gather at the completed natural expansion, falling back to the main base before the natural is ready or after it is lost. Its free members remain dispatchable while the remaining home force continues local defense; units still gathering are not dispatchable.
- Use Own Forces "Available for dispatch from home pool" as the authoritative quantity available for a new combat order.
- A combat call with units forms a new outbound group from available members of group_0. A combat call with group changes only that existing group's style and target; it does not add members, and group_0 cannot be retargeted. To support an existing mission, dispatch another units-based group toward the same target.
- Units assigned to an outbound group, scouting, stationed in a bunker or loaded in a transport are unavailable. New units do not automatically reinforce outbound groups.
- An attack group keeps its surviving members until its target has been reached and continuously confirmed clear, then holds that zone as defend until changed or ordered to retreat. A defend group remains active until changed or ordered to retreat. Retreat starts the return immediately, but survivors rejoin group_0 only after arriving home.
- Combat targets are objectives rather than search waypoints. Zone numbers do not imply adjacency; query the map or route when the path matters.
"""

GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed SCVs generate income while gathering resources; SCVs occupied by other work are not gathering. Additional eligible gathering SCVs increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. A unit cannot begin production while its required supply is unavailable. Query the relevant building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Unit production also requires a completed compatible producer and a free production slot. Each producer has limited parallel capacity and can hold up to five orders.
- Barracks, Factories and Starports can each use one add-on. A Reactor provides two parallel production slots for compatible units, while a Tech Lab unlocks advanced units or research. An add-on cannot produce anything independently of its host building.
- Town halls train SCVs, and morphing a town hall prevents it from training them until the morph completes. SCVs construct buildings and gather resources.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

PROTOSS_ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information or scouting to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

PROTOSS_CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, scouting and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, interrupted-construction continuation, Warp Gate morphs after research, and local combat micro.
- Pylons, chrono_boost and reinforcements require explicit actions. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

PROTOSS_GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed Probes generate income while gathering resources; Probes occupied by other work are not gathering. Additional eligible gathering Probes increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. A unit cannot begin production while its required supply is unavailable. Query the relevant building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Protoss production buildings also need Pylon power. Unit production requires a completed compatible producer and a free production slot. Each producer has limited parallel capacity and can hold up to five orders.
- Gateways, Robotics Facilities and Stargates do not take add-ons. After Warp Gate research finishes, the backend morphs completed Gateways; train names stay the same.
- A Nexus trains Probes and builds with Probes. Probes do not repair.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

ZERG_ROLE_RULES = """Your objective is to destroy enemy structures before the time limit. Fog may hide surviving enemies, including flying structures, so an empty visible-enemy list is not proof of victory. If play continues with no enemy structures visible, use last-seen information or scouting to search unconfirmed areas. Only Observation.terminated/result establishes the outcome, and reaching the time limit results in a Tie.
"""

ZERG_CONTROL_RULES = """Control boundary:
- You make the strategic decisions for construction, production, research, scouting and army orders.
- The backend handles building placement, worker assignment, pathfinding, mining, interrupted-construction continuation, and local combat micro.
- Overlords, inject_larva, spawn_creep_tumor and reinforcements require explicit actions. Queen energy is reported. The backend does not automatically replace destroyed production, add technology, train units or reinforce an outbound group.
"""

ZERG_GAME_RULES = """- Minerals and gas pay for buildings, units and research. Completed Drones generate income while gathering resources; Drones occupied by other work are not gathering. Additional eligible gathering Drones increase income until the available mineral or gas worker capacity is reached.
- Queued units commit resources and supply. Completed supply-providing units or structures increase the supply cap, unfinished ones provide no supply, and total supply is capped at 200. Morphing a town hall does not add more supply. A unit cannot begin production while its required supply is unavailable. Query the relevant unit or building data when the exact amount is needed.
- Buildings and research require completed prerequisites. Most Zerg buildings must be placed on creep. Unit production requires larva or a completed town hall for Queens, plus a free production slot.
- There are no add-ons. Lair and Hive morph a selected town hall. Lurker Den morphs a Hydralisk Den, and Greater Spire morphs a Spire.
- Baneling, Ravager, Lurker, Overseer and Brood Lord morph existing units. They do not train the source unit.
- A Hatchery, Lair or Hive provides larva and trains Queens. Drones gather resources and are consumed when they start buildings. Drones do not repair.
- Cloaked or burrowed enemies require detection before they can be targeted.
- Mining, construction, production, research, scouting and combat continue concurrently while game time advances.
"""

# Action-local behavior is exposed through each Action tool's schema.
COMBAT_DISPATCH_RULES = "units atomically dispatches the requested free units from group_0 to create a new outbound group. To support an existing mission, create another units-based group with the same target. If any requested units are unavailable, the command is rejected instead of waiting or partially dispatching."
COMBAT_RETARGET_RULES = "group changes only the named outbound group's style and target; it keeps the surviving membership and adds no replacements. group_0 is invalid, and repeating the same style and target creates no group."
COMBAT_STYLE_RULES = "attack advances and engages, then holds the cleared zone as defend until changed or retreated; defend holds an area without cross-map pursuit or attacking structures until changed or retreated. Acceptance does not prove arrival, success or target clearance; use retreat for an immediate return order."

ACTION_RULES = {
    "build": "Build one additional structure or add-on. Placement is automatic; a Command Center is placed as an expansion. Refineries require a free geyser at a ready owned town hall. Completes when construction starts and an unfinished structure appears.",
    "train": "count requests additional units rather than a desired total. The request remains active until all requested units appear. Paid queued units are not yet living units, and later losses do not reopen completed production.",
    "research": "Duplicate accepted, active or completed research requests are ignored. The request completes on queue entry; its effect becomes available only after Research reports it completed.",
    "cancel": "Cancel all matching unstarted build, train or research work across accepted requests. Active construction or research and paid training remain without refund.",
    "scan": "Spend 50 energy from one ready Orbital for temporary local vision and detection in the target zone; it does not reveal the entire zone.",
    "call_mule": "Spend 50 energy from one ready Orbital to call a MULE at a safe owned mineral line. Scan and MULE share that Orbital's energy.",
    "chrono_boost": "Spend 50 energy from one ready Nexus to Chrono Boost one structure the backend chooses, preferring a structure that is already producing or researching. It is not cast automatically.",
    "inject_larva": "Spend 25 energy from one ready Queen to inject one town hall the backend chooses. It is not cast automatically. Transfuse remains backend combat micro.",
    "spawn_creep_tumor": "Spend 25 energy from one ready Queen to plant one creep tumor on creep, toward the enemy. A burrowed tumor spreads the next one instead when it can. It is not cast automatically.",
    "scout": 'Send one SCV along the ordered route; route="all" performs one expansion-zone sweep. A new scout command replaces existing scout work, and the scout\'s death fails the task without automatic replacement. Arrival does not guarantee full-zone visibility or that every enemy was found.',
    "upgrade": "Morph the selected structure to the requested form. The request completes when issued, not when the morph finishes.",
    "combat": " ".join((
        "Use exactly one of units or group.",
        COMBAT_DISPATCH_RULES,
        COMBAT_RETARGET_RULES,
        COMBAT_STYLE_RULES,
    )),
    "retreat": "Start an outbound group's immediate return home. Acceptance is not arrival; survivors merge into group_0 and the old group ends only after they arrive home.",
    "advance": "End the action reply: submit all its actions, advance the requested positive number of game seconds and return the next Observation. Episode end may return early; active work continues.",
}

DECISION_REQUEST = """[Decision Request]
Review the current Observation and previous Feedback. Query any missing static or map facts needed for this decision. When enough information is available, submit one complete action batch ending with advance. If no new action is needed, call advance alone.
"""

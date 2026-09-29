"""Non-instantiated field descriptions: syntax guidance, not sample decisions.

The renderer checks these keys against the shared parser/Schema field rules.
Descriptions must not select a unit, quantity, zone, style, or timing for Agents.
Not injected into the system prompt; Tool Schema owns argument fields.
"""

ACTION_FIELDS = {
    "build": {"target": "catalog building/add-on name"},
    "train": {"target": "catalog unit name", "count": "additional units (positive integer)"},
    "research": {"target": "catalog research name"},
    "cancel": {
        "task_id": "one exact ID from Production Priority",
        "target_action": "build, train, research or morph_townhall",
        "target": "name of matching work to cancel",
    },
    "scan": {"target": "observed zone ID string"},
    "call_mule": {},
    "supply_drop": {},
    "chrono_boost": {},
    "inject_larva": {},
    "spawn_creep_tumor": {},
    "scout": {"route": 'ordered nonempty zone ID array, or "all" for one automatic expansion sweep'},
    "morph_townhall": {"target": "townhall ID from Structures", "to": "catalog morph name"},
    "combat": {
        "style": "attack or defend", "target": "observed zone ID string",
        "units": "object: army unit name -> positive integer count",
        "group": "observed outbound group ID string",
    },
    "retreat": {
        "group": "observed outbound group ID string",
        "method": "move, or recall for the Protoss main Nexus",
    },
    "advance": {"seconds": "positive number of game seconds"},
}

FORMAT_HEADER = """Use NormalizedToolCall objects {name, arguments}. Finish with advance(seconds).
Names use the catalog; IDs use Observation.
"""

# Developer/test forms; not injected into the model prompt.
COMMAND_TEMPLATES = {
    "build": '{"name":"build","arguments":{"target":"<building_or_addon_name>"}}',
    "train": '{"name":"train","arguments":{"target":"<unit_name>","count":<positive_integer>}}',
    "research": '{"name":"research","arguments":{"target":"<research_name>"}}',
    "cancel_task": '{"name":"cancel","arguments":{"task_id":"<task_id>"}}',
    "cancel_target": '{"name":"cancel","arguments":{"target_action":"<build_or_train_or_research_or_morph_townhall>","target":"<target_name>"}}',
    "morph_townhall": '{"name":"morph_townhall","arguments":{"target":"<townhall_id>","to":"<morph_name>"}}',
    "scout": '{"name":"scout","arguments":{"route":["<zone_id>","<another_zone_id>"]}}',
    "scan": '{"name":"scan","arguments":{"target":"<zone_id>"}}',
    "call_mule": '{"name":"call_mule","arguments":{}}',
    "supply_drop": '{"name":"supply_drop","arguments":{}}',
    "chrono_boost": '{"name":"chrono_boost","arguments":{}}',
    "inject_larva": '{"name":"inject_larva","arguments":{}}',
    "spawn_creep_tumor": '{"name":"spawn_creep_tumor","arguments":{}}',
    "combat_units": '{"name":"combat","arguments":{"style":"<attack_or_defend>","target":"<zone_id>","units":{"<unit_name>":<positive_integer>}}}',
    "combat_group": '{"name":"combat","arguments":{"style":"<attack_or_defend>","target":"<zone_id>","group":"<outbound_group_id>"}}',
    "retreat": '{"name":"retreat","arguments":{"group":"<outbound_group_id>","method":"<move_or_recall>"}}',
    "advance": '{"name":"advance","arguments":{"seconds":<positive_number>}}',
}

FORMAT_TEMPLATES = FORMAT_HEADER + "Independent command objects:\n" + "\n".join(COMMAND_TEMPLATES.values()) + "\n"

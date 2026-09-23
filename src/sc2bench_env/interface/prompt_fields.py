"""Non-instantiated field descriptions: syntax guidance, not sample decisions.

The renderer checks these keys against the shared parser/Schema field rules.
Descriptions must not select a unit, quantity, zone, style, or timing for Agents.
Not injected into the system prompt; Tool Schema owns argument fields.
"""

ACTION_FIELDS = {
    "build": {"target": "catalog building/add-on name"},
    "train": {"target": "catalog unit name", "count": "additional units (positive integer)"},
    "research": {"target": "catalog research name"},
    "cancel": {"target_action": "build, train or research", "target": "name of the work to cancel"},
    "scan": {"target": "observed zone ID string"},
    "call_mule": {},
    "chrono_boost": {},
    "inject_larva": {},
    "spawn_creep_tumor": {},
    "scout": {"route": 'ordered nonempty zone ID array, or "all" for one automatic expansion sweep'},
    "upgrade": {"target": "townhall ID from Structures", "to": "catalog morph name"},
    "combat": {
        "style": "attack or defend", "target": "observed zone ID string",
        "units": "object: army unit name -> positive integer count",
        "group": "observed outbound group ID string",
    },
    "retreat": {"group": "observed outbound group ID string"},
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
    "cancel": '{"name":"cancel","arguments":{"target_action":"<build_or_train_or_research>","target":"<target_name>"}}',
    "upgrade": '{"name":"upgrade","arguments":{"target":"<townhall_id>","to":"<morph_name>"}}',
    "scout": '{"name":"scout","arguments":{"route":["<zone_id>","<another_zone_id>"]}}',
    "scan": '{"name":"scan","arguments":{"target":"<zone_id>"}}',
    "call_mule": '{"name":"call_mule","arguments":{}}',
    "chrono_boost": '{"name":"chrono_boost","arguments":{}}',
    "inject_larva": '{"name":"inject_larva","arguments":{}}',
    "spawn_creep_tumor": '{"name":"spawn_creep_tumor","arguments":{}}',
    "combat_units": '{"name":"combat","arguments":{"style":"<attack_or_defend>","target":"<zone_id>","units":{"<unit_name>":<positive_integer>}}}',
    "combat_group": '{"name":"combat","arguments":{"style":"<attack_or_defend>","target":"<zone_id>","group":"<outbound_group_id>"}}',
    "retreat": '{"name":"retreat","arguments":{"group":"<outbound_group_id>"}}',
    "advance": '{"name":"advance","arguments":{"seconds":<positive_number>}}',
}

FORMAT_TEMPLATES = FORMAT_HEADER + "Independent command objects:\n" + "\n".join(COMMAND_TEMPLATES.values()) + "\n"

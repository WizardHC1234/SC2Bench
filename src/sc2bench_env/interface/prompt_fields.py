"""Non-instantiated field descriptions: syntax guidance, not sample decisions.

The renderer checks these keys against the shared parser/Schema field rules.
Descriptions must not select a unit, quantity, zone, style, or timing for Agents.
Not injected into the system prompt; Tool Schema owns argument fields.
"""

import re

# Unquoted numeric placeholders in COMMAND_TEMPLATES; shared by renderers/tests.
NUMERIC_PLACEHOLDER_RE = re.compile(
    r'(?<!")<(?:optional_)?(?:positive_integer|positive_number)>'
)

ACTION_FIELDS = {
    "build": {"target": "catalog building/add-on name"},
    "train": {"target": "catalog unit name", "count": "additional units (positive integer)"},
    "research": {"target": "catalog research name"},
    "cancel": {
        "target_action": "build, train, research or morph_townhall",
        "target": "name of matching work to cancel",
        "count": "optional quantity to remove; omit to remove all matching unstarted work",
    },
    "scan": {"target": "observed zone ID string"},
    "call_mule": {},
    "supply_drop": {},
    "chrono_boost": {},
    "inject_larva": {},
    "spawn_creep_tumor": {},
    "scout": {"route": 'ordered nonempty zone ID array, or "all" for one automatic expansion sweep'},
    "morph_townhall": {"target": "townhall ID from Structures", "to": "catalog morph name"},
    "dispatch_group": {
        "source": "group_0 or an observed active group ID",
        "units": "object: army unit name -> positive integer count",
        "order": "attack or defend",
        "target": "observed zone ID string",
    },
    "attack_group": {
        "group": "observed outbound group ID",
        "target": "observed zone ID string",
    },
    "defend_group": {
        "group": "observed outbound group ID",
        "target": "observed zone ID string",
    },
    "reinforce_group": {
        "source": "group_0 or an observed outbound group ID",
        "destination": "group_0 or an observed outbound group ID",
        "units": "object: army unit name -> positive integer count",
    },
    "merge_groups": {
        "destination": "outbound group ID that keeps its order",
        "sources": "nonempty array of other outbound group IDs",
    },
    "retreat_group": {
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
    "cancel": '{"name":"cancel","arguments":{"target_action":"<build_or_train_or_research_or_morph_townhall>","target":"<target_name>","count":<optional_positive_integer>}}',
    "morph_townhall": '{"name":"morph_townhall","arguments":{"target":"<townhall_id>","to":"<morph_name>"}}',
    "scout": '{"name":"scout","arguments":{"route":["<zone_id>","<another_zone_id>"]}}',
    "scan": '{"name":"scan","arguments":{"target":"<zone_id>"}}',
    "call_mule": '{"name":"call_mule","arguments":{}}',
    "supply_drop": '{"name":"supply_drop","arguments":{}}',
    "chrono_boost": '{"name":"chrono_boost","arguments":{}}',
    "inject_larva": '{"name":"inject_larva","arguments":{}}',
    "spawn_creep_tumor": '{"name":"spawn_creep_tumor","arguments":{}}',
    "dispatch_group": '{"name":"dispatch_group","arguments":{"source":"group_0","units":{"<unit_name>":<positive_integer>},"order":"<attack_or_defend>","target":"<zone_id>"}}',
    "attack_group": '{"name":"attack_group","arguments":{"group":"<outbound_group_id>","target":"<zone_id>"}}',
    "defend_group": '{"name":"defend_group","arguments":{"group":"<outbound_group_id>","target":"<zone_id>"}}',
    "reinforce_group": '{"name":"reinforce_group","arguments":{"source":"group_0","destination":"<outbound_group_id>","units":{"<unit_name>":<positive_integer>}}}',
    "merge_groups": '{"name":"merge_groups","arguments":{"destination":"<outbound_group_id>","sources":["group_3"]}}',
    "retreat_group": '{"name":"retreat_group","arguments":{"group":"<outbound_group_id>","method":"<move_or_recall>"}}',
    "advance": '{"name":"advance","arguments":{"seconds":<positive_number>}}',
}

FORMAT_TEMPLATES = FORMAT_HEADER + "Independent command objects:\n" + "\n".join(COMMAND_TEMPLATES.values()) + "\n"

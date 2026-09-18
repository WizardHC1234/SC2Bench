"""Non-instantiated field descriptions: syntax guidance, not sample decisions.

The renderer checks these keys against the shared parser/Schema field rules.
Descriptions must not select a unit, quantity, zone, style, or timing for Agents.
"""

ACTION_FIELDS = {
    "build": {"target": "catalog building/add-on name"},
    "train": {"target": "catalog unit name", "count": "additional units (positive integer)"},
    "research": {"target": "catalog research name"},
    "cancel": {"target_action": "build, train or research", "target": "name of the work to cancel"},
    "scan": {"target": "observed zone ID string"},
    "call_mule": {},
    "scout": {"route": 'ordered nonempty zone ID array, or "all" for one automatic expansion sweep'},
    "upgrade": {"target": "townhall ID from Structures", "to": "catalog morph name"},
    "combat": {
        "style": "attack or defend", "target": "observed zone ID string",
        "units": "object: army unit name -> positive integer count",
        "group": "observed outbound group ID string",
    },
    "retreat": {"group": "observed outbound group ID string"},
    "wait": {"any_of": "JSON array of condition objects", "all_of": "JSON array of condition objects"},
}

FORMAT_HEADER = """Generic format templates (metasyntax, not executable JSON):
Replace all <...>; never submit placeholders. Strings stay quoted; numbers WITHOUT quotes. Names come from the catalog; IDs from Observation. These are syntax, not a strategy or sequence.
Complete reply shape:
[
  <zero or more command objects, each followed by a comma>,
  {"action":"wait","any_of":[{"condition":"interval","seconds":<positive_number>}],"all_of":[]}
]
With no commands, omit the placeholder line/comma. Choose your own wait conditions/duration, or use bare wait.
"""

# One syntax owner per action; the prompt places each form beside its semantics.
COMMAND_TEMPLATES = {
    "build": '{"action":"build","target":"<building_or_addon_name>"}',
    "train": '{"action":"train","target":"<unit_name>","count":<positive_integer>}',
    "research": '{"action":"research","target":"<research_name>"}',
    "cancel": '{"action":"cancel","target_action":"<build_or_train_or_research>","target":"<target_name>"}',
    "upgrade": '{"action":"upgrade","target":"<townhall_id>","to":"<morph_name>"}',
    "scout": '{"action":"scout","route":["<zone_id>","<another_zone_id>"]}',
    "scan": '{"action":"scan","target":"<zone_id>"}',
    "call_mule": '{"action":"call_mule"}',
    "combat_units": '{"action":"combat","style":"<attack_or_defend>","target":"<zone_id>","units":{"<unit_name>":<positive_integer>}}',
    "combat_group": '{"action":"combat","style":"<attack_or_defend>","target":"<zone_id>","group":"<outbound_group_id>"}',
    "retreat": '{"action":"retreat","group":"<outbound_group_id>"}',
}

# Combined reference for external callers/tests; not injected a second time.
FORMAT_TEMPLATES = FORMAT_HEADER + "Independent command objects:\n" + "\n".join(COMMAND_TEMPLATES.values()) + "\n"

WAIT_FIELDS = {
    "interval": {"seconds": "positive number of game seconds"},
    "resource_at_least": {"resource": "minerals or vespene", "amount": "non-negative integer threshold"},
    "supply_left_at_most": {"amount": "non-negative integer threshold"},
    "unit_count_at_least": {"unit": "unit name from the catalog", "count": "non-negative integer threshold"},
    "building_count_at_least": {"building": "building name from the catalog", "count": "non-negative integer threshold"},
    "scan_ready": {"count": "positive integer threshold"},
    "game_time_at_least": {"seconds": "non-negative number of game seconds"},
    "zone_under_attack": {"zone": "zone ID string from current Observation"},
}

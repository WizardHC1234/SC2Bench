"""Human-readable model input; canonical Observation remains structured.

Inspired by Commander's summaries and zone/production tables, with no runtime
dependency on Commander and no strategic recommendations or derived intel.
"""
from typing import Any


def value(item: Any) -> str:
    if item is None:
        return "unknown"
    if isinstance(item, bool):
        return "yes" if item else "no"
    if isinstance(item, float):
        return f"{item:.2f}".rstrip("0").rstrip(".")
    return str(item).replace("\n", " ").replace("|", "/")


def label(key):
    return str(key).replace("_", " ").capitalize()


def counts(items):
    if items is None:
        return "unknown"
    return ", ".join(f"{name} {value(count)}" for name, count in sorted(items.items())) or "none"


def content(items):
    if items is None:
        return "unknown"
    parts = [f"{key}: {counts(items.get(key))}" for key in ("units", "buildings")
             if items.get(key) is None or items.get(key)]
    return "; ".join(parts) or "none"


def fields(items, indent=""):
    """Readable fallback preserves extra/race-specific fields without JSON."""
    lines = []
    for key, item in items.items():
        title = label(key)
        if isinstance(item, dict):
            if key in {"requested", "available", "missing", "units", "alive"} and all(
                    isinstance(number, (int, float)) for number in item.values()):
                lines.append(f"{indent}{title}: {counts(item)}")
                continue
            lines.append(f"{indent}{title}:")
            lines.extend(fields(item, indent + "  ") if item else [indent + "  none"])
        elif isinstance(item, list):
            if all(not isinstance(entry, (dict, list)) for entry in item):
                lines.append(f"{indent}{title}: {', '.join(value(entry) for entry in item) or 'none'}")
            else:
                lines.append(f"{indent}{title}:")
                for index, entry in enumerate(item, 1):
                    lines.extend(fields({str(index): entry}, indent + "  "))
        else:
            lines.append(f"{indent}{title}: {value(item)}")
    return lines


def extras(row, known, prefix="  "):
    return fields({key: item for key, item in row.items() if key not in known}, prefix)


def table(rows, columns):
    """Each column is (label, source key, optional renderer)."""
    if not rows:
        return ["none"]
    lines = [" | ".join(column[0] for column in columns)]
    for row in rows:
        lines.append(" | ".join((column[2] if len(column) > 2 else value)(row.get(column[1]))
                                for column in columns))
        lines.extend(extras(row, {column[1] for column in columns}))
    return lines


def time_value(seconds):
    if seconds is None:
        return "unknown"
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d} ({value(seconds)} s)"


def resource_lines(rows):
    if not rows:
        return ["none"]
    compact = all(
        "minerals_initial" not in row and "vespene_initial" not in row
        for row in rows
    )
    if compact:
        lines = ["Owned-base resources:",
                 "Zone | Minerals remaining | Vespene remaining | Owned gas structures | Available geyser slots"]
        for row in rows:
            lines.append(" | ".join(value(row.get(key)) for key in (
                "zone_id", "minerals_remaining", "vespene_remaining",
                "owned_gas_structure_count", "available_geyser_slots",
            )))
            lines.extend(extras(row, {
                "zone_id", "minerals_remaining", "vespene_remaining",
                "owned_gas_structure_count", "available_geyser_slots",
            }))
        return lines
    def ratio(row, prefix):
        return "/".join("?" if row.get(key) is None else value(row[key])
                        for key in (f"{prefix}_remaining", f"{prefix}_initial"))
    lines = ["Base resources (remaining/initial; ? = unknown):",
             "Zone | Minerals | Vespene gas | Geyser slots | Owned gas structures | Available geyser slots | Resource visibility"]
    known = {"zone_id", "minerals_remaining", "minerals_initial", "vespene_remaining",
             "vespene_initial", "geyser_slots", "owned_gas_structure_count",
             "available_geyser_slots", "resource_visibility"}
    for row in rows:
        lines.append(" | ".join([value(row.get("zone_id")), ratio(row, "minerals"), ratio(row, "vespene"),
                                 *[value(row.get(key)) for key in ("geyser_slots", "owned_gas_structure_count",
                                                                  "available_geyser_slots", "resource_visibility")]]))
        lines.extend(extras(row, known))
    return lines


def combat_lines(groups):
    if not groups:
        return ["none"]
    lines = []
    def group_order(item):
        name = item[0]
        suffix = name.removeprefix("group_")
        return (int(suffix) if suffix.isdigit() else float("inf"), name)
    for group, row in sorted(groups.items(), key=group_order):
        lines.append(f"{group}: {value(row.get('style'))}; target {value(row.get('target'))}; "
                     f"status {value(row.get('status'))}; phase {value(row.get('phase'))}")
        member_label = "Available home members" if group == "group_0" else "Living members"
        if "alive" in row:
            lines.append(f"  {member_label}: {counts(row.get('alive'))}")
        if "requested" in row:
            lines.append(f"  Originally requested: {counts(row['requested'])}")
        if "nearest_zone" in row:
            lines.append(f"  Nearest zone to group center: {value(row['nearest_zone'])}")
        if "assigned" in row:
            lines.append(f"  Assigned to outbound order: {value(row['assigned'])}")
        if "visible_enemy_nearby" in row:
            nearby = row["visible_enemy_nearby"]
            lines.append("  Visible enemies within 15 of on-map members: " + (
                f"units {value(nearby.get('units'))}; buildings {value(nearby.get('buildings'))}"
                if nearby is not None else "unknown"))
        if "weapon_cooldown_active_count" in row:
            lines.append(f"  Members with active weapon cooldown: {value(row['weapon_cooldown_active_count'])}")
        for key in ("cloaked", "forms", "skill_evidence"):
            if key in row:
                lines.append(f"  {label(key)}: {counts(row[key])}")
        if "transport" in row:
            transport = row["transport"]
            lines.append(f"  Transport activity: {value(transport.get('activity'))}; "
                         f"currently loaded: {counts(transport.get('loaded_units'))}")
            lines.extend(extras(transport, {"activity", "loaded_units"}, "    "))
        lines.extend(extras(row, {"status", "style", "target", "phase", "alive", "requested",
                                 "nearest_zone", "assigned", "visible_enemy_nearby",
                                 "weapon_cooldown_active_count", "cloaked", "forms", "skill_evidence", "transport"}))
    return lines


def event_lines(events, *, omit_types=()):
    if not events:
        return ["none"]
    events = [row for row in events if row.get("type") not in omit_types]
    if not events:
        return ["none"]
    lines = []
    for index, row in enumerate(compact_counted(events, event=True), 1):
        lines.append(f"{index}. {value(row.get('type'))}")
        lines.extend(extras(row, {"type"}))
    return lines


def compact_counted(rows, *, event=False):
    """Merge adjacent simple macro notices for display only, not execution.

    Preserve richer receipts/events, boundaries and original structured records.
    """
    result = []
    previous_simple = False
    for raw in rows:
        row = dict(raw)
        allowed = ({"type", "action", "target", "count"} if event else
                   {"action", "result", "target", "count"})
        simple = (set(row) <= allowed and row.get("action") in {"build", "train", "research"}
                  and type(row.get("count")) is int and row["count"] > 0
                  and (row.get("type") == "demand_accepted" if event else row.get("result") == "accepted"))
        key = {k: v for k, v in row.items() if k != "count"}
        entry_key = "event_count" if event else "orders"
        if simple and previous_simple and result and {k: v for k, v in result[-1].items()
                                  if k not in {"count", entry_key}} == key:
            result[-1]["count"] += row["count"]
            result[-1][entry_key] = result[-1].get(entry_key, 1) + 1
        else:
            result.append(row)
        previous_simple = simple
    return result


def blocker_text(reason):
    if not isinstance(reason, str):
        return value(reason)
    explanations = {
        "no_available_geyser": "no eligible free geyser at a ready own base",
        "resources": "resource budget unavailable (including earlier spending priority)",
        "supply": "insufficient available supply",
        "energy": "insufficient available energy",
        "producer_busy": "compatible in-game production queues full",
        "producer_unavailable": "no ready grounded producer",
        "producer_techlab_unavailable": "no compatible producer with a ready attached Tech Lab",
        "addon_host_unavailable": "no ready grounded parent without an add-on",
        "addon_host_busy": "eligible add-on parents busy",
    }
    if reason.startswith("prerequisite:"):
        return "requires ready " + reason.split(":", 1)[1]
    return explanations.get(reason, value(reason))


def waiting_summary(action, target, priority, fallback):
    """All observed waiting reasons, not the last same-target reason alone."""
    matching = [row for row in (priority or [])
                if row.get("action") == action and row.get("target") == target]
    if not matching:
        if action == "train" and fallback.get("waiting_to_produce") == 0:
            return "none"
        if (action == "build" and fallback.get("waiting_to_start") == 0
                and fallback.get("worker_en_route") == 0):
            return "none"
        return blocker_text(fallback.get("waiting_for"))
    amounts = {}
    for row in matching:
        amount = row.get("waiting_to_produce") if action == "train" else row.get("remaining")
        if type(amount) is int and amount <= 0:
            continue
        reason = blocker_text(row.get("waiting_for"))
        previous = amounts.get(reason, 0)
        amounts[reason] = (previous + amount if type(previous) is int and type(amount) is int else None)
    return "; ".join(f"{reason}: {value(amount)}" for reason, amount in amounts.items()) or "none"


def priority_lines(data):
    """Full per-order status, explicitly separated from new Agent commands."""
    rows = []
    for index, source in enumerate(data, 1):
        row = dict(source, priority=index)
        for name in ("order_progress", "in_production", "waiting_to_produce"):
            row.setdefault(name, "-")
        row.setdefault("waiting_for", "not reported")
        rows.append(row)
    lines = ["Active macro requests: " + str(len(data)),
             "ALREADY ACCEPTED WORK — execution status and cancellable quantities."]
    return lines + table(rows, [("Priority", "priority"), ("Action", "action"), ("Target", "target"),
                    ("Remaining", "remaining"), ("Order progress", "order_progress"),
                    ("Paid train queue", "in_production"), ("Unqueued train", "waiting_to_produce"),
                    ("State", "state", execution_state_text),
                    ("Waiting for", "waiting_for", blocker_text),
                    ("Cancellable", "cancellable_count")])


def execution_state_text(state):
    """Explain reported phases only; never infer counts or completion."""
    return {
        "waiting_to_start": "not started",
        "worker_en_route": "worker travelling",
        "under_construction": "construction underway",
        "in_production": "production active",
        "in_progress": "execution active",
        "completed": "command completed",
        "cancelled": "cancelled",
        "failed": "failed",
    }.get(state, value(state))


def _production_catalog(race):
    """Observation rendering remains usable for unavailable race catalogs."""
    from sc2bench_env.interface.catalogs import get_catalog

    try:
        return get_catalog(race=race)
    except ValueError:
        return None


def _catalog_unavailable(race):
    return "unknown (catalog unavailable for own race " + value(race) + ")"


def _production_unit_technology(row, buildings, *, race="terran"):
    """Catalog-derived technology facts, independent of budget or slots."""
    catalog = _production_catalog(race)
    if catalog is None:
        return []
    ready = row.get("ready_grounded")
    if type(ready) is not int or ready <= 0:
        return []
    options = []
    for spec in catalog.targets:
        if spec.action != "train":
            continue
        if spec.produced_at != row.get("facility"):
            continue
        missing, unknown = [], []
        for req in spec.prerequisites:
            if req == spec.produced_at:
                continue
            if race == "terran" and req == spec.produced_at + "_techlab":
                count = row.get("techlab_hosts")
                name = "attached " + req
            else:
                count = (buildings.get(req, {}).get("completed", 0)
                         if isinstance(buildings, dict) else None)
                name = req
            if type(count) is not int:
                unknown.append(name)
            elif count <= 0:
                missing.append(name)
        notes = (["missing ready " + ", ".join(missing)] if missing else [])
        if unknown:
            notes.append("unknown readiness: " + ", ".join(unknown))
        options.append((spec.name, tuple(notes)))
    return options


def _production_unit_options(row, buildings, *, race="terran"):
    """Compatibility view; model input groups identical technology conditions."""
    return [name + " (" + ("; ".join(notes) or "tech ready") + ")"
            for name, notes in _production_unit_technology(row, buildings, race=race)]


def production_options(data, buildings, *, race="terran"):
    """Standalone technology view retained for callers; renderer groups by facility."""
    if data and _production_catalog(race) is None:
        return ["Unit technology: " + _catalog_unavailable(race)]
    lines = []
    for row in data:
        options = _production_unit_options(row, buildings, race=race)
        if options:
            lines.append(str(row["facility"]) + ": " + "; ".join(options))
    if not lines:
        return []
    return ["Training options for ready producers (tech only, not recommendations):",
            *lines,
            "Buildings provide capacity, NOT automatic production. Use explicit train requests for the units you choose. Tech ready does not mean affordable or an available slot; costs/supply remain in the game reference."]


def _shown_on_facility(spec, facility, race):
    """Larva is the live Zerg producer; the catalog names the town hall instead."""
    if race == "zerg" and spec.produced_at == "hatchery":
        if spec.name == "queen":
            return facility == "hatchery"
        return facility == "larva"
    return spec.produced_at == facility


def facility_training(facility, training, *, race="terran"):
    """Reuse canonical unit totals; never assign orders to individual buildings."""
    if not isinstance(training, dict):
        return "unknown"
    catalog = _production_catalog(race)
    if catalog is None:
        return _catalog_unavailable(race)
    specs = {spec.name: spec for spec in catalog.targets if spec.action == "train"}
    known = {spec.produced_at for spec in specs.values()}
    if race == "zerg":
        known.add("larva")
    if facility not in known:
        return "unknown (producer type not in current catalog)"
    parts, unmapped = [], []
    for target, row in sorted(training.items()):
        row = row if isinstance(row, dict) else {}
        paid = row.get("in_production")
        waiting = row.get("waiting_to_produce")
        paid = paid if type(paid) is int and paid >= 0 else None
        waiting = waiting if type(waiting) is int and waiting >= 0 else None
        if paid == 0 and waiting == 0:
            continue
        spec = specs.get(target)
        if spec is None:
            unmapped.append(str(target))
        elif _shown_on_facility(spec, facility, race):
            parts.append(f"{target} — paid {value(paid)}, waiting {value(waiting)}")
    if unmapped:
        parts.append("unmapped training targets: " + ", ".join(unmapped) + " (producer unknown)")
    return "; ".join(parts) or "none"


def production_lines(data, buildings, training, *, race="terran"):
    columns = [("Facility", "facility"), ("Ready", "ready_grounded"),
                  ("Ready attached Tech Labs", "techlab_hosts"), ("Ready attached Reactors", "reactor_hosts"),
                  ("Capacity", "capacity"), ("Free production slots", "free_slots"),
                  ("Free queue positions", "free_queue_positions"),
                  ("Free Tech Lab slots", "free_techlab_slots")]
    if race != "terran":
        # Keep supplied capacity facts; never manufacture Terran-specific columns.
        common_columns = [("Facility", "facility"), ("Ready", "ready_grounded"),
                          ("Capacity", "capacity"), ("Free production slots", "free_slots"),
                          ("Free queue positions", "free_queue_positions")]
        columns = [column for column in common_columns
                   if column[1] == "facility" or any(column[1] in row for row in data)]
    hidden = {"occupied_slots", "queue_capacity", "queued_orders"}
    rows = [{key: item for key, item in row.items() if key not in hidden} for row in data]
    return table(rows, columns)


def section(key, data, *, production_priority=None, buildings=None, training=None, race="terran"):
    if data is None:
        return ["unknown"]
    if key == "terminated":
        return [value(data)]
    if not data:
        return ["unknown" if key == "map_topology" else "none"]
    if key == "game":
        names = {"game_time_seconds": "Game time", "race": "Own race", "enemy_race": "Enemy race",
                 "game_time_limit_seconds": "Time limit", "seconds_remaining": "Time remaining"}
        return [f"{names.get(name, label(name))}: " + (time_value(item) if name in
                {"game_time_seconds", "game_time_limit_seconds", "seconds_remaining"} else value(item))
                for name, item in data.items()]
    if key == "economy":
        return [f"Minerals: {value(data.get('minerals'))}; Vespene gas: {value(data.get('vespene'))}",
                f"Supply: {value(data.get('supply_used'))}/{value(data.get('supply_cap'))}; "
                f"Available supply: {value(data.get('supply_left'))}; Army supply: {value(data.get('army_supply'))}",
                f"Workers: {value(data.get('worker_count'))}; Current mining worker capacity: "
                f"{value(data.get('mining_worker_capacity'))}",
                f"Income per minute: minerals {value(data.get('mineral_income_per_minute'))}; "
                f"vespene gas {value(data.get('vespene_income_per_minute'))}",
                *extras(data, {"minerals", "vespene", "supply_used", "supply_cap", "supply_left",
                               "army_supply", "worker_count", "mining_worker_capacity",
                               "mineral_income_per_minute", "vespene_income_per_minute"}, "")]
    if key == "map_control":
        return [*fields({name: item for name, item in data.items() if name != "base_resources"}),
                *resource_lines(data.get("base_resources", []))]
    if key == "map_topology":
        if not data.get("zones"):
            return ["unknown"]
        def neighbors(items):
            if items is None:
                return "unknown"
            return ", ".join(f"{item['zone_id']} ({value(item.get('path_distance'))})"
                             for item in items) or "none"
        return [f"Ground-path source: {value(data.get('distance_basis'))}",
                f"Neighbor basis: {value(data.get('neighbor_basis'))}",
                f"Verified ground-path pairs: {value(data.get('verified_path_pair_count'))}/"
                f"{value(data.get('total_path_pair_count'))}",
                *table(data["zones"], [("Zone", "zone_id"), ("Ramp", "has_ramp"),
                    ("Ground distance from own main", "path_distance_from_own_main"),
                    ("Ground distance to confirmed enemy main", "path_distance_to_enemy_main"),
                    ("Corridor neighbors (ground distance)", "corridor_neighbors", neighbors)]),
                *extras(data, {"distance_basis", "neighbor_basis", "verified_path_pair_count",
                               "total_path_pair_count", "zones"}, "")]
    if key == "production_priority":
        return priority_lines(data)
    if key == "production":
        return production_lines(data, buildings, training, race=race)
    if key == "available_targets":
        if not isinstance(data, dict):
            return ["none"]
        lines = []
        for group in ("build", "train", "research", "upgrade"):
            names = data.get(group) or []
            lines.append(f"{group.capitalize()}: {', '.join(value(name) for name in names) or 'none'}")
        return lines
    if key == "relevant_zone_ids":
        if isinstance(data, dict):
            return fields(data)
        return [", ".join(value(item) for item in data) or "none"]
    if key == "zone_state":
        if not data:
            return ["none"]
        return [
            f"Relevant zones: {len(data)}. Query query_zone_state or query_map_overview for the rest.",
            *table(data, [
            ("Zone", "zone_id"), ("Role", "zone_role"), ("Known owner", "known_owner"),
            ("Center vision", "vision_state"),
            ("Own contents", "own_contents", lambda items: "OWN: " + content(items)),
            ("Visible enemy", "visible_enemy_contents", lambda items: "ENEMY visible: " + content(items)),
            ("Enemy history", "last_seen_enemy_contents", lambda items: "ENEMY last seen: " + content(items)),
            ("History age (s)", "enemy_information_age_seconds"),
            ("Visible enemy weapon in range", "visible_enemy_weapon_in_range"),
        ])]
    if key in {"building", "training"}:
        action = "build" if key == "building" else "train"
        waiting_key = "waiting_to_start" if key == "building" else "waiting_to_produce"
        priorities = production_priority or []
        represented = {
            row.get("target") for row in priorities
            if row.get("action") == action
        }
        unrepresented_waiting = any(
            type(row.get(waiting_key)) is int and row.get(waiting_key) > 0 and name not in represented
            for name, row in data.items()
        )
        compact = production_priority is not None and not unrepresented_waiting
        columns = [("Type", "type")]
        if key == "building":
            columns += [("Ready", "completed"), ("Under construction", "under_construction")]
            if not compact:
                columns += [("Worker en route", "worker_en_route"),
                            ("Waiting to start", "waiting_to_start")]
        else:
            columns += [("Paid training queue", "in_production")]
            if not compact:
                columns += [("Additional units waiting", "waiting_to_produce")]
        if not compact:
            columns.append(("Waiting reason (quantity)", "waiting_for", value))
        hidden = {"order_progress"}
        if compact:
            hidden |= {"waiting_for", "worker_en_route", "waiting_to_start", "waiting_to_produce"}
        rows = []
        for name, row in data.items():
            if compact and key == "training" and not (
                type(row.get("in_production")) is int and row.get("in_production") > 0
            ):
                continue
            view = {k: v for k, v in row.items() if k not in hidden}
            view["type"] = name
            if not compact:
                view["waiting_for"] = waiting_summary(
                    action, name, production_priority, row)
            rows.append(view)
        return table(rows, columns)
    if key == "own_forces":
        names = {"workers": "Living workers", "army": "Living army", "assigned": "Unavailable for dispatch",
                 "free": "Available for dispatch from home pool",
                 "bunker_garrison": "Bunker garrison"}
        return [f"{names.get(name, label(name))}: {counts(item)}" for name, item in data.items()]
    if key == "structures":
        return table(data, [("Object ID", "id"), ("Type", "type")])
    if key == "research":
        return [f"{name}: {value(status)}" for name, status in data.items()]
    if key == "combat":
        return combat_lines(data)
    if key == "scouting":
        lines = []
        for name, row in data.items():
            lines.append(f"{name}:")
            if "route" in row:
                lines.append("  Route: " + ("unknown" if row["route"] is None else
                    (" -> ".join(value(item) for item in row["route"]) or "none")))
            lines.extend(extras(row, {"route"}))
        return lines
    if key == "recent_events":
        # Acceptance is already shown by Previous Feedback and Production
        # Priority. Keep execution changes and failures here.
        return event_lines(data, omit_types={"demand_accepted"})
    return fields(data) if isinstance(data, dict) else fields({"items": data})


def render_text(observation, sections):
    game = observation.get("game")
    if isinstance(game, dict) and "race" in game:
        race = game["race"]
    else:
        # Older dictionary callers may omit Game; an explicit unknown never defaults.
        race = observation.get("race", "terran")
    lines = []
    for key, title in sections:
        lines.extend([f"[{title}]", *section(key, observation.get(key),
                      production_priority=observation.get("production_priority"),
                      buildings=observation.get("building"), training=observation.get("training"),
                      race=race), ""])
    return "\n".join(lines).rstrip()


def render_feedback_text(feedback, *, shown_events=None):
    lines = ["Action receipts:"]
    if any(event.get("type") == "decision_rejected" for event in feedback.get("events", [])):
        lines.append("Batch rejected: no entries from this array applied; previously accepted work continues.")
    receipts = feedback.get("receipts", [])
    if not receipts:
        lines.append("none")
    for index, row in enumerate(compact_counted(receipts), 1):
        meaning = receipt_meaning(row)
        lines.append(f"{index}. {value(row.get('name') or row.get('action'))}: {value(row.get('result'))}"
                     + (f" ({meaning})" if meaning else ""))
        display = dict(row)
        arguments = display.get("arguments")
        if isinstance(arguments, dict):
            for key, item in arguments.items():
                display.setdefault(key, item)
        name = row.get("name") or row.get("action")
        if name == "cancel" and row.get("result") == "accepted":
            reason = row.get("reason")
            prefix = "cleared_waiting="
            if isinstance(reason, str) and reason.startswith(prefix) and reason[len(prefix):].isdigit():
                display["reason"] = "unstarted work quantity removed: " + reason[len(prefix):]
        lines.extend(extras(display, {"action", "name", "arguments", "result"}))
    events = feedback.get("events", [])
    if shown_events is None:
        lines.extend(["", "Execution events:", *event_lines(events)])
    else:
        # Match occurrences, not set membership: two equal events can represent
        # two distinct accepted orders/births and must not collapse into one.
        remaining_shown = list(shown_events)
        additional = []
        for event in events:
            if event in remaining_shown:
                remaining_shown.remove(event)
            else:
                additional.append(event)
        lines.extend(["", "Execution events (additional to Recent Events):",
                      *(event_lines(additional) if additional else
                        ["none; any repeated events are shown in Recent Events"])])
    if feedback.get("name_normalizations"):
        lines.extend(["", "Name normalizations (use canonical names next time):"])
        for row in feedback["name_normalizations"]:
            verb = row.get("name") or row.get("action")
            if verb == "cancel":
                verb = f"cancel {value(row.get('target_action'))}"
            lines.append(f"entry {value(row.get('entry_index'))} ({value(verb)}): "
                         f"{value(row.get('original'))} -> {value(row.get('canonical'))}")
    lines.extend(extras(feedback, {"receipts", "events", "name_normalizations"}, ""))
    return "\n".join(lines)


def receipt_meaning(row):
    """Last-submission effects, not the current execution/completion state."""
    result = row.get("result")
    if result == "accepted":
        return {
            "build": "additional construction registered",
            "train": "additional production registered",
            "research": "research request registered",
            "cancel": "cancellation processed",
            "combat": "army order registered",
            "retreat": "return order registered, not arrival",
            "scan": "cast request registered",
            "call_mule": "cast request registered",
            "chrono_boost": "cast request registered",
            "inject_larva": "cast request registered",
            "spawn_creep_tumor": "cast request registered",
            "scout": "scouting order registered",
            "upgrade": "morph request registered",
        }.get(row.get("name") or row.get("action"), "registered, not completion")
    return {
        "idempotent_noop": "unchanged; no additional work",
        "ignored_duplicate_action_id": "retry ignored; no additional work",
        "rejected": "not applied",
    }.get(result, "")

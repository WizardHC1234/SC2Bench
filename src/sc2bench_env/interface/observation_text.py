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
            if key in {"requested", "requested_units", "available", "missing", "units", "alive"} and all(
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


def time_clock(seconds):
    """mm:ss clock used by the Brief Game line."""
    if seconds is None:
        return "unknown"
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def time_value(seconds):
    if seconds is None:
        return "unknown"
    return f"{time_clock(seconds)} ({value(seconds)} s)"


def resource_amount(item):
    """Make confirmed exhaustion explicit while preserving unknown values."""
    if resource_depleted(item):
        return "depleted"
    return value(item)


def resource_depleted(item):
    return isinstance(item, (int, float)) and not isinstance(item, bool) and item == 0


def resource_lines(rows):
    if not rows:
        return ["none"]
    compact = all(
        "minerals_initial" not in row and "vespene_initial" not in row
        for row in rows
    )
    if compact:
        lines = ["Resource deposits:"]
        for row in rows:
            lines.append(
                f"- {value(row.get('zone_id'))}: "
                f"minerals {resource_amount(row.get('minerals_remaining'))}; "
                f"vespene {resource_amount(row.get('vespene_remaining'))}; "
                f"own gas structures {value(row.get('owned_gas_structure_count'))}; "
                f"free geysers {value(row.get('available_geyser_slots'))}"
            )
            lines.extend(extras(row, {
                "zone_id", "minerals_remaining", "vespene_remaining",
                "owned_gas_structure_count", "available_geyser_slots",
            }))
        return lines
    def ratio(row, prefix):
        remaining = row.get(f"{prefix}_remaining")
        initial = row.get(f"{prefix}_initial")
        rendered = "/".join("?" if item is None else value(item)
                            for item in (remaining, initial))
        return f"depleted ({rendered})" if resource_depleted(remaining) else rendered
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


def _group_sort_key(item):
    name = item[0]
    suffix = name.removeprefix("group_")
    return (int(suffix) if suffix.isdigit() else float("inf"), name)


def combat_lines(groups):
    if not groups:
        return ["none"]
    lines = []
    for group, row in sorted(groups.items(), key=_group_sort_key):
        transition = row.get("order_transition")
        holding_after_attack = (
            isinstance(transition, dict)
            and transition.get("from") == "attack"
            and transition.get("to") == "defend"
            and transition.get("reason") == "target_confirmed_clear"
        )
        members = counts(row.get("alive")) if "alive" in row else None
        lines.append(f"{group}: {members}" if members is not None else f"{group}:")
        location = row.get("nearest_zone") or row.get("target")
        lines.append(f"  Location: {value(location)}")
        if holding_after_attack:
            lines.append(f"  Current activity: holding at {value(row.get('target'))}")
            if "holding_seconds" in row:
                lines.append(f"  Holding duration: {value(row.get('holding_seconds'))} s")
            lines.append(
                f"  Previous order: attack {value(row.get('target'))} — completed"
            )
        else:
            lines.append(
                f"  Current order: {value(row.get('style'))} "
                f"{value(row.get('target'))} — {value(row.get('status'))}"
            )
            if "order_seconds" in row:
                lines.append(f"  Current order duration: {value(row.get('order_seconds'))} s")
        if "requested" in row:
            lines.append(f"  Originally requested: {counts(row['requested'])}")
        if "assigned" in row:
            lines.append(f"  Assigned to outbound order: {value(row['assigned'])}")
        if isinstance(transition, dict):
            if not holding_after_attack:
                lines.append(
                    f"  Order transition: {value(transition.get('from'))} -> "
                    f"{value(transition.get('to'))}; reason {value(transition.get('reason'))}"
                )
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
                                 "weapon_cooldown_active_count", "cloaked", "forms", "skill_evidence",
                                 "transport", "order_transition", "attack_result", "holding_seconds",
                                 "order_seconds"}))
    return lines


def brief_economy_lines(economy, map_control=None, *, race="terran"):
    """Compact Bank / Supply / Workers / Base resources block for Brief OBS."""
    if not isinstance(economy, dict):
        return ["unknown"]
    worker_name = {
        "terran": "SCVs", "protoss": "Probes", "zerg": "Drones",
    }.get(race, "workers")
    supply_blocked = type(economy.get("supply_left")) is int and economy.get("supply_left") <= 0
    lines = [
        f"Bank: minerals {value(economy.get('minerals'))}; "
        f"vespene {value(economy.get('vespene'))} | "
        f"Income/min: minerals {value(economy.get('mineral_income_per_minute'))}; "
        f"vespene {value(economy.get('vespene_income_per_minute'))}",
        f"Supply: {value(economy.get('supply_used'))}/{value(economy.get('supply_cap'))}; "
        f"free {value(economy.get('supply_left'))}; army supply {value(economy.get('army_supply'))}"
        + ("; SUPPLY BLOCKED" if supply_blocked else ""),
        f"Workers ({worker_name}): {value(economy.get('worker_count'))}; "
        f"minerals {value(economy.get('workers_on_minerals'))}/"
        f"{value(economy.get('mineral_worker_saturation'))}; "
        f"vespene {value(economy.get('workers_on_vespene'))}/"
        f"{value(economy.get('vespene_worker_saturation'))}; "
        f"other {value(economy.get('workers_other'))}",
    ]
    if isinstance(map_control, dict):
        resources = resource_lines(map_control.get("base_resources") or [])
        if resources != ["none"]:
            lines.extend(resources)
    return lines


def brief_combat_lines(groups):
    """Group core summary used by Brief OBS (plan section 4.5)."""
    if not groups:
        return ["none"]
    outbound_count = sum(
        name != "group_0" and isinstance(row, dict)
        for name, row in groups.items()
    )
    lines = [f"Outbound groups: {outbound_count}"]
    for group, row in sorted(groups.items(), key=_group_sort_key):
        if not isinstance(row, dict):
            lines.append(f"{group}: {value(row)}")
            continue
        transition = row.get("order_transition")
        holding_after_attack = (
            isinstance(transition, dict)
            and transition.get("from") == "attack"
            and transition.get("to") == "defend"
            and transition.get("reason") == "target_confirmed_clear"
        )
        style = value(row.get("style"))
        target = value(row.get("target"))
        phase = value(row.get("phase") or row.get("status"))
        header = f"{group}: {style} {target}; {phase}"
        nearest = row.get("nearest_zone")
        if nearest and nearest != row.get("target"):
            header += f"; near {value(nearest)}"
        if holding_after_attack:
            header = f"{group}: defend {target}; holding_after_attack"
            if nearest and nearest != row.get("target"):
                header += f"; near {value(nearest)}"
            if "holding_seconds" in row:
                header += f"; holding {value(row.get('holding_seconds'))} s"
        elif group != "group_0" and "order_seconds" in row:
            header += f"; order active {value(row.get('order_seconds'))} s"
        lines.append(header)
        if group == "group_0":
            free = row.get("alive") if "alive" in row else row.get("free")
            if free:
                lines.append(f"  Free: {counts(free)}")
            if row.get("unavailable"):
                lines.append(f"  Unavailable: {counts(row.get('unavailable'))}")
        elif "alive" in row:
            lines.append(f"  Living: {counts(row.get('alive'))}")
        if row.get("pending_transfers"):
            for transfer in row["pending_transfers"]:
                lines.append(
                    f"  Transfer pending from {value(transfer.get('source'))}: "
                    f"{counts(transfer.get('units'))}"
                )
        nearby = row.get("visible_enemy_nearby")
        if group != "group_0" and "visible_enemy_nearby" in row:
            if nearby is None:
                lines.append("  Nearby enemies: unknown")
            elif not nearby or (
                int(nearby.get("units") or 0) == 0 and int(nearby.get("buildings") or 0) == 0
            ):
                lines.append("  Nearby enemies: none")
            else:
                lines.append(
                    f"  Nearby enemies: units {value(nearby.get('units'))}, "
                    f"buildings {value(nearby.get('buildings'))}"
                )
    return lines


def brief_zone_lines(zones):
    """Bullet Relevant Zones for Brief OBS (plan section 4.6)."""
    if not zones:
        return ["none"]
    lines = []
    for row in zones:
        if not isinstance(row, dict):
            continue
        zone_id = value(row.get("zone_id"))
        role = value(row.get("zone_role"))
        owner = value(row.get("known_owner"))
        vision = value(row.get("vision_state"))
        threat = row.get("visible_enemy_weapon_in_range") is True
        ownership = {
            "self": "owned by self",
            "enemy": "owned by enemy",
            "unconfirmed": "owner unconfirmed",
        }.get(str(row.get("known_owner")), f"owner {owner}")
        if vision == "visible":
            vision_text = f"{ownership} and visible"
        elif vision == "fogged":
            age = row.get("enemy_information_age_seconds")
            vision_text = f"{ownership} and fogged"
            if type(age) in (int, float):
                vision_text += f" for {int(age)}s"
        else:
            vision_text = f"{ownership}; vision {vision}"
        head = f"- {zone_id} ({role}): {vision_text}"
        if threat:
            head = f"- IMMEDIATE WEAPON THREAT - {zone_id} ({role}): {vision_text}"
        parts = [head]
        own = content(row.get("own_contents"))
        if own != "none":
            parts.append(f"own {own}")
        visible = content(row.get("visible_enemy_contents"))
        if visible != "none":
            parts.append(f"visible enemy {visible}")
        last_seen = content(row.get("last_seen_enemy_contents"))
        if last_seen != "none" and vision != "visible":
            parts.append(f"last seen {last_seen}")
        if threat:
            parts.append(
                "At least one visible enemy weapon is currently in range of an own "
                "unit or building; this does not confirm that damage was dealt"
            )
        lines.append("; ".join(parts))
    return lines


def brief_facility_lines(data, *, race="terran"):
    """Compact facility capacity lines without unknown placeholder columns."""
    if not data:
        return ["none"]
    lines = []
    for row in data:
        if not isinstance(row, dict):
            continue
        facility = value(row.get("facility"))
        ready = row.get("ready_grounded")
        capacity = row.get("capacity")
        free = row.get("free_slots")
        bits = [facility]
        if ready is not None:
            bits[0] = f"{facility} {value(ready)}"
        detail = []
        if free is not None and capacity is not None:
            detail.append(f"free slots {value(free)}/{value(capacity)}")
        elif free is not None:
            detail.append(f"free slots {value(free)}")
        elif capacity is not None:
            detail.append(f"capacity {value(capacity)}")
        for key, label_text in (
            ("techlab_hosts", "techlab hosts"),
            ("reactor_hosts", "reactor hosts"),
            ("ready_to_warp", "warp ready"),
            ("warp_ready", "warp ready"),
            ("cooling", "cooling down"),
            ("cooling_down", "cooling down"),
            ("larva", "larva"),
        ):
            if key in row and row.get(key) is not None:
                detail.append(f"{label_text} {value(row.get(key))}")
        lines.append(f"{bits[0]}" + (f" ({', '.join(detail)})" if detail else ""))
    return lines


def brief_building_lines(data):
    if not data:
        return ["none"]
    parts = []
    for name, row in data.items():
        if not isinstance(row, dict):
            continue
        ready = int(row.get("completed") or 0)
        building = int(row.get("under_construction") or 0)
        if ready <= 0 and building <= 0:
            continue
        text = f"{name}: ready {ready}"
        if building > 0:
            text += f"; under construction {building}"
        parts.append(text)
    return ["; ".join(parts)] if parts else ["none"]


def brief_training_lines(data):
    if not data:
        return ["none"]
    parts = []
    for name, row in data.items():
        if not isinstance(row, dict):
            continue
        paid = int(row.get("in_production") or 0)
        if paid > 0:
            parts.append(f"{name} {paid}")
    return ["; ".join(parts)] if parts else ["none"]


def brief_available_target_lines(data):
    if not isinstance(data, dict):
        return ["none"]
    labels = {
        "build": "Available build",
        "train": "Available train",
        "research": "Available research",
        "morph_townhall": "Available morph",
    }
    lines = []
    for key, title in labels.items():
        names = data.get(key) or []
        if not names and key == "morph_townhall":
            continue
        lines.append(f"{title}: {', '.join(value(name) for name in names) or 'none'}")
    return lines or ["none"]


def brief_forces_lines(data, *, race="terran"):
    if not isinstance(data, dict) or not data:
        return ["none"]
    lines = []
    army = dict(data.get("army") or {})
    non_dispatchable = set()
    catalog = _production_catalog(race)
    if catalog is not None:
        non_dispatchable = {
            spec.name for spec in catalog.targets
            if spec.action == "train" and not spec.dispatchable
        }
    support = {
        name: int(count) for name, count in dict(data.get("support") or {}).items()
        if int(count) > 0
    }
    support.update({
        name: int(count) for name, count in army.items()
        if name in non_dispatchable and int(count) > 0
    })
    army = {
        name: int(count) for name, count in army.items()
        if name not in non_dispatchable and int(count) > 0
    }
    if army:
        lines.append(f"Total: {counts(army)}")
    assigned = {
        name: int(count) for name, count in dict(data.get("assigned") or {}).items()
        if name not in non_dispatchable and int(count) > 0
    }
    if assigned:
        lines.append(f"Unavailable: {counts(assigned)}")
    if support:
        lines.append(f"Non-combat support: {counts(support)}")
    garrison = data.get("bunker_garrison")
    if garrison:
        lines.append(f"Bunker garrison: {counts(garrison)}")
    return lines or ["none"]


def brief_priority_lines(data):
    if not data:
        return ["none"]
    request_count = sum(int(row.get("request_count", 1)) for row in data if isinstance(row, dict))
    remaining_values = [
        row.get("remaining") for row in data
        if isinstance(row, dict) and type(row.get("remaining")) is int
    ]
    cancellable_values = [
        row.get("cancellable_count") for row in data
        if isinstance(row, dict) and type(row.get("cancellable_count")) is int
    ]
    unknown_cancellable = sum(
        isinstance(row, dict) and row.get("cancellable_count") is None
        for row in data
    )
    blocked = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        reason = row.get("waiting_for")
        if not isinstance(reason, str) or not reason:
            continue
        if reason == "supply":
            category = "supply"
        elif reason == "resources":
            category = "resources"
        elif reason.startswith("prerequisite:"):
            category = "prerequisites"
        elif reason in {
            "producer_busy", "production_slot", "producer_unavailable",
            "producer_techlab_unavailable", "waiting_for_queue",
        }:
            category = "production"
        elif reason.startswith("builder_unavailable:") or reason in {
            "no_available_geyser", "addon_host_unavailable", "addon_host_busy",
            "source_unit_reserved", "source_unit_unavailable",
        }:
            category = "availability"
        else:
            category = "other"
        amount = (
            row.get("waiting_to_produce")
            if row.get("action") == "train" else row.get("cancellable_count")
        )
        if type(amount) is not int:
            amount = row.get("remaining")
        if type(amount) is int and amount > 0:
            blocked[category] = blocked.get(category, 0) + amount
    lines = [
        f"{request_count} requests"
        + (f" grouped into {len(data)} lines" if request_count != len(data) else "")
        + (f"; {sum(remaining_values)} items remaining" if remaining_values else ""),
    ]
    cancellable_total = sum(cancellable_values)
    if cancellable_total > 0 or unknown_cancellable:
        cancellable_text = f"Not started and cancellable: {cancellable_total}"
        if unknown_cancellable:
            cancellable_text += f"; unknown for {unknown_cancellable} grouped lines"
        lines.append(cancellable_text)
    if blocked:
        lines.append("Blocked work: " + "; ".join(
            f"{name} {amount}" for name, amount in blocked.items()
        ))
    for index, row in enumerate(data, 1):
        target = value(row.get("target"))
        if row.get("to"):
            target += " -> " + value(row.get("to"))
        remaining = row.get("remaining")
        qty = f" x{remaining}" if type(remaining) is int else ""
        summary = []
        if row.get("action") == "train":
            summary.append(f"paid {value(row.get('in_production'))}")
            summary.append(f"unstarted {value(row.get('waiting_to_produce'))}")
        cancellable = row.get("cancellable_count")
        if type(cancellable) is int and cancellable > 0:
            summary.append(f"cancellable {cancellable}")
        waiting_for = row.get("waiting_for")
        blocker = blocker_text(waiting_for)
        state = row.get("state")
        if state == "failed":
            summary.append(f"FAILED; reason: {blocker}")
        elif waiting_for not in {None, "", "unknown"}:
            summary.append(f"waiting for {blocker}")
        elif state not in {None, "", "unknown"}:
            summary.append(execution_state_text(state))
        lines.append(
            f"{index}. {value(row.get('action'))} {target}{qty}: " + "; ".join(summary)
        )
    return lines


def event_lines(events, *, omit_types=(), historical=False):
    if not events:
        return ["none"]
    events = [row for row in events if row.get("type") not in omit_types]
    if not events:
        return ["none"]
    lines = []
    if historical:
        lines.append("Historical — earlier decisions; not new this turn.")
    for index, row in enumerate(compact_counted(events, event=True), 1):
        stamp = ""
        if "event_id" in row or "game_time_seconds" in row:
            stamp = (
                f" [#{value(row.get('event_id'))}"
                f" @ {time_value(row.get('game_time_seconds'))}]"
            )
        lines.append(f"{index}. {value(row.get('type'))}{stamp}")
        details = {key: item for key, item in row.items() if key not in {"type", "event_id", "game_time_seconds"}}
        if row.get("type") == "train_completed" and "count" in details:
            details = {("requested_count" if key == "count" else key): item for key, item in details.items()}
        if row.get("type") == "combat_ended" and "units" in details:
            details = {("requested_units" if key == "units" else key): item for key, item in details.items()}
        lines.extend(fields(details, "  "))
    return lines


def compact_counted(rows, *, event=False):
    """Merge adjacent simple macro notices for display only, not execution.

    Preserve richer receipts/events, boundaries and original structured records.
    """
    result = []
    previous_simple = False
    for raw in rows:
        row = dict(raw)
        identity = {"event_id", "game_time_seconds"}
        allowed = ({"type", "action", "target", "count"} | identity if event else
                   {"action", "result", "target", "count"})
        keys = set(row) - identity
        simple = (keys <= (allowed - identity) and row.get("action") in {"build", "train", "research"}
                  and type(row.get("count")) is int and row["count"] > 0
                  and (row.get("type") == "demand_accepted" if event else row.get("result") == "accepted"))
        key = {k: v for k, v in row.items() if k not in {"count", "event_id", "game_time_seconds"}}
        entry_key = "event_count" if event else "orders"
        if simple and previous_simple and result and {k: v for k, v in result[-1].items()
                                  if k not in {"count", entry_key, "event_id", "game_time_seconds"}} == key:
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
        "producer_busy": "all compatible production lanes are currently occupied",
        "production_slot": "compatible production is currently busy",
        "producer_unavailable": "no ready grounded producer",
        "producer_techlab_unavailable": "no compatible producer with a ready attached Tech Lab",
        "addon_host_unavailable": "no ready grounded parent without an add-on",
        "addon_host_busy": "eligible add-on parents busy",
        "waiting_for_queue": "source building finishing its paid queue before morph",
        "source_unit_reserved": "source already reserved by another demand",
        "source_unit_unavailable": "no eligible source building or unit",
    }
    if reason.startswith("prerequisite:"):
        return "requires ready " + reason.split(":", 1)[1]
    if reason.startswith("builder_unavailable:"):
        return "no ready " + reason.split(":", 1)[1] + " available to construct"
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
    """Compact text for persistent macro work; structured rows keep full detail."""
    if not data:
        return ["none"]
    request_count = sum(
        int(row.get("request_count", 1))
        for row in data if isinstance(row, dict)
    )
    remaining_values = [
        row.get("remaining") for row in data
        if isinstance(row, dict) and type(row.get("remaining")) is int
    ]
    remaining_total = sum(remaining_values)
    lines = [
        f"Active macro requests: {request_count}"
        + (f" grouped into {len(data)} lines" if request_count != len(data) else "")
        + (f"; total remaining items: {remaining_total}" if remaining_values else ""),
    ]
    for index, row in enumerate(data, 1):
        target = value(row.get("target"))
        if row.get("to"):
            target += " -> " + value(row.get("to"))
        summary = []
        if int(row.get("request_count", 1)) > 1:
            summary.append(f"requests {row['request_count']}")
        summary.append(f"remaining {value(row.get('remaining'))}")
        if row.get("action") == "train":
            summary.extend((
                f"paid {value(row.get('in_production'))}",
                f"unstarted {value(row.get('waiting_to_produce'))}",
            ))
        cancellable = row.get("cancellable_count")
        if type(cancellable) is int and cancellable > 0:
            summary.append(f"cancellable {cancellable}")
        lines.append(
            f"{index}. {value(row.get('action'))} {target}; "
            + "; ".join(summary)
        )
        state = row.get("state")
        waiting_for = row.get("waiting_for")
        blocker = blocker_text(waiting_for)
        if state == "failed":
            status = f"FAILED; reason: {blocker}"
        elif waiting_for not in {None, "", "unknown"}:
            qualifier = "BLOCKED remainder" if state in {"in_production", "in_progress"} else "BLOCKED"
            status = f"{execution_state_text(state)}; {qualifier}: {blocker}"
        else:
            status = f"{execution_state_text(state)}; waiting: {blocker}"
        lines.append(f"   Status: {status}")
    return lines


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
    from sc2bench_env.catalog.races import get_catalog

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
                  ("Free Tech Lab slots", "free_techlab_slots")]
    if race != "terran":
        # Keep supplied capacity facts; never manufacture Terran-specific columns.
        common_columns = [("Facility", "facility"), ("Ready", "ready_grounded"),
                          ("Capacity", "capacity"), ("Free production slots", "free_slots"),
                          ("Ready to warp", "ready_to_warp"), ("Cooling", "cooling"),
                          ("Reserved for morph", "reserved_for_morph"),
                          ("Waiting for queue", "waiting_for_queue")]
        columns = [column for column in common_columns
                   if column[1] == "facility" or any(column[1] in row for row in data)]
    # Native waiting-queue positions are deliberately not used by persistent
    # platform tasks, because pre-filling them would make later cancel/priority
    # changes non-preemptible. Exposing those positions suggests capacity the
    # platform cannot actually schedule.
    hidden = {"occupied_slots", "queue_capacity", "queued_orders", "free_queue_positions"}
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
        # One self-contained line so each decision remains readable without
        # relying on earlier chat history for race or time-limit identity.
        current = time_clock(data.get("game_time_seconds"))
        limit = time_clock(data.get("game_time_limit_seconds"))
        remaining = time_clock(data.get("seconds_remaining"))
        if limit != "unknown":
            timing = f"Time {current}/{limit}"
            if remaining != "unknown":
                timing += f" ({remaining} remaining)"
        else:
            timing = f"Time {current}"
            if remaining != "unknown":
                timing += f" ({remaining} remaining)"
        parts = [timing]
        if data.get("race") is not None:
            parts.append(f"own race {value(data.get('race'))}")
        if data.get("enemy_race") is not None:
            parts.append(f"enemy race {value(data.get('enemy_race'))}")
        if data.get("status") is not None:
            parts.append(f"status {value(data.get('status'))}")
        if data.get("result") is not None:
            parts.append(f"result {value(data.get('result'))}")
        if data.get("end_reason") is not None:
            parts.append(f"end_reason {value(data.get('end_reason'))}")
        extras_keys = {
            "game_time_seconds", "game_time_limit_seconds", "seconds_remaining",
            "race", "enemy_race", "status", "result", "end_reason",
        }
        return ["; ".join(parts), *extras(data, extras_keys, "")]
    if key == "economy":
        worker_name = {
            "terran": "SCVs",
            "protoss": "Probes",
            "zerg": "Drones",
        }.get(race, "workers")
        supply_blocked = type(data.get("supply_left")) is int and data.get("supply_left") <= 0
        return [f"Minerals: {value(data.get('minerals'))}; Vespene gas: {value(data.get('vespene'))}",
                f"Supply: {value(data.get('supply_used'))}/{value(data.get('supply_cap'))}; "
                f"Available supply: {value(data.get('supply_left'))}; Army supply: {value(data.get('army_supply'))}"
                + ("; SUPPLY BLOCKED" if supply_blocked else ""),
                f"Workers ({worker_name}): {value(data.get('worker_count'))}",
                f"Mineral workers: {value(data.get('workers_on_minerals'))} / saturation "
                f"{value(data.get('mineral_worker_saturation'))}",
                f"Vespene workers: {value(data.get('workers_on_vespene'))} / saturation "
                f"{value(data.get('vespene_worker_saturation'))}",
                f"Other workers: {value(data.get('workers_other'))}",
                f"Income per minute: minerals {value(data.get('mineral_income_per_minute'))}; "
                f"vespene gas {value(data.get('vespene_income_per_minute'))}",
                *extras(data, {"minerals", "vespene", "supply_used", "supply_cap", "supply_left",
                               "army_supply", "worker_count", "workers_on_minerals",
                               "mineral_worker_saturation", "workers_on_vespene",
                               "vespene_worker_saturation", "workers_other",
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
        for group in ("build", "train", "research", "morph_townhall"):
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
            f"Relevant zones shown: {len(data)}.",
            *table(data, [
            ("Zone", "zone_id"), ("Role", "zone_role"), ("Known owner", "known_owner"),
            ("Center vision", "vision_state"),
            ("Own contents", "own_contents", lambda items: "OWN: " + content(items)),
            ("Visible enemy", "visible_enemy_contents", lambda items: "ENEMY visible: " + content(items)),
            ("Enemy history", "last_seen_enemy_contents", lambda items: "ENEMY last seen: " + content(items)),
            ("History age (s)", "enemy_information_age_seconds"),
            ("Immediate weapon threat", "visible_enemy_weapon_in_range", lambda active: (
                "IMMEDIATE WEAPON THREAT - a visible armed enemy can currently reach an own unit or building; "
                "this does not confirm damage" if active is True else
                "no" if active is False else "unknown"
            )),
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
        return event_lines(data, omit_types={"demand_accepted", "decision_rejected"})
    return fields(data) if isinstance(data, dict) else fields({"items": data})


def render_text(observation, sections, *, race=None):
    game = observation.get("game")
    if not race:
        if isinstance(game, dict) and game.get("race"):
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


def render_feedback_text(feedback, *, shown_events=None, include_receipts=True):
    lines = []
    if any(event.get("type") == "decision_rejected" for event in feedback.get("events", [])):
        lines.append("Batch rejected: no entries from this array applied; previously accepted work continues.")
    receipts = feedback.get("receipts", [])
    if include_receipts:
        lines.append("Action receipts:")
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
        if events:
            if lines:
                lines.append("")
            lines.extend(["Execution events:", *event_lines(events)])
    else:
        # Prefer event_id when present so the same logical event is never shown
        # twice under two unrecognizable copies.
        shown_ids = {
            event.get("event_id") for event in shown_events
            if isinstance(event, dict) and event.get("event_id") is not None
        }
        remaining_shown = [event for event in shown_events if event.get("event_id") is None]
        additional = []
        for event in events:
            event_id = event.get("event_id") if isinstance(event, dict) else None
            if event_id is not None and event_id in shown_ids:
                continue
            if event_id is None and event in remaining_shown:
                remaining_shown.remove(event)
                continue
            additional.append(event)
        if additional:
            if lines:
                lines.append("")
            lines.extend(["Execution events:", *event_lines(additional)])
    if include_receipts and feedback.get("name_normalizations"):
        if lines:
            lines.append("")
        lines.append("Name normalizations (use canonical names next time):")
        for row in feedback["name_normalizations"]:
            verb = row.get("name") or row.get("action")
            if verb == "cancel":
                verb = f"cancel {value(row.get('target_action'))}"
            lines.append(f"entry {value(row.get('entry_index'))} ({value(verb)}): "
                         f"{value(row.get('original'))} -> {value(row.get('canonical'))}")
    lines.extend(extras(
        feedback,
        {"receipts", "events", "name_normalizations", "receipts_delivered"},
        "",
    ))
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
            "merge_groups": "group merge registered",
            "retreat": "return order registered, not arrival",
            "scan": "cast request registered",
            "call_mule": "cast request registered",
            "supply_drop": "cast request registered",
            "chrono_boost": "cast request registered",
            "inject_larva": "cast request registered",
            "spawn_creep_tumor": "cast request registered",
            "scout": "scouting order registered",
            "morph_townhall": "morph request registered",
        }.get(row.get("name") or row.get("action"), "registered, not completion")
    return {
        "idempotent_noop": "unchanged; no additional work",
        "ignored_duplicate_action_id": "retry ignored; no additional work",
        "rejected": "not applied",
    }.get(result, "")

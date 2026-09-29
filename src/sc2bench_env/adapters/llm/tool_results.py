"""Standard text for knowledge and read tool results."""
from __future__ import annotations


from typing import Any, Mapping, Sequence

def _display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _display_list(value: Any) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return "none"
    items = [str(item) for item in value]
    return ", ".join(items) if items else "none"


def _display_supply_provided(value: Any) -> str:
    if value in (0, 0.0, "0", "0.0"):
        return "none"
    return _display_value(value)


def _display_counts(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "none"
    return ", ".join(f"{name} x{count}" for name, count in value.items())


def _render_catalog_results(result: Mapping[str, Any], *, kind: str) -> str:
    headings = {
        "unit": "Unit data",
        "building": "Building data",
        "research": "Research data",
    }
    lines = [headings[kind]]
    rows = result.get("results")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        return "\n".join((*lines, "No results."))
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = _display_value(row.get("name"))
        if row.get("error"):
            extra = ""
            if row.get("candidates"):
                extra = f"; close candidates: {_display_list(row.get('candidates'))}"
            lines.append(f"- {name}: error: {_display_value(row.get('error'))}{extra}")
            continue
        lines.append(f"- {name}")
        lines.append(
            "  Cost: "
            f"{_display_value(row.get('minerals'))} minerals, "
            f"{_display_value(row.get('vespene'))} gas"
        )
        time_label = "Training time" if kind == "unit" else (
            "Build time" if kind == "building" else "Research time"
        )
        lines.append(f"  {time_label}: {_display_value(row.get('time_seconds'))} seconds")
        if kind == "unit":
            lines.append(f"  Supply required by this action: {_display_value(row.get('supply'))}")
            lines.append(f"  Total supply while alive: {_display_value(row.get('client_food_required'))}")
            lines.append(f"  Supply provided when complete: {_display_supply_provided(row.get('food_provided'))}")
            lines.append(f"  Produced at: {_display_value(row.get('produced_at'))}")
        elif kind == "building":
            lines.append(f"  Builder: {_display_value(row.get('builder'))}")
            lines.append(f"  Kind: {_display_value(row.get('kind'))}")
            lines.append(f"  Supply provided when complete: {_display_supply_provided(row.get('food_provided'))}")
            if row.get("morph_from"):
                lines.append(f"  Morphs from: {_display_value(row.get('morph_from'))}")
        else:
            lines.append(f"  Facility: {_display_value(row.get('facility'))}")
        if row.get("mechanism"):
            lines.append(f"  Mechanism: {_display_value(row.get('mechanism'))}")
        if row.get("production_batch_size"):
            lines.append(f"  Units per order: {_display_value(row.get('production_batch_size'))}")
        if kind == "unit" and "dispatchable" in row:
            lines.append(f"  Dispatchable: {_display_value(row.get('dispatchable'))}")
        if row.get("action") == "morph_townhall":
            lines.append("  Order this with morph_townhall and a townhall_<n> id.")
        lines.append(f"  Prerequisites: {_display_list(row.get('prerequisites'))}")
        lines.append(f"  Availability: {_display_value(row.get('availability'))}")
        if kind == "research":
            lines.append(f"  Effect: {_display_value(row.get('description'))}")
            continue
        lines.append(f"  Roles: {_display_list(row.get('roles'))}")
        gaps = [
            label for label, key in (
                ("health", "health"), ("shields", "shields"), ("energy_max", "energy_max"),
            ) if row.get(key) == "unknown"
        ]
        if gaps:
            lines.append(f"  Data gaps: {', '.join(gaps)}")
        else:
            lines.append(f"  Health: {_display_value(row.get('health'))}")
            lines.append(f"  Shields: {_display_value(row.get('shields'))}")
            lines.append(f"  Energy max: {_display_value(row.get('energy_max'))}")
        lines.append(f"  Armor: {_display_value(row.get('armor'))}")
        if kind == "unit":
            lines.append(f"  Movement speed: {_display_value(row.get('movement_speed'))}")
        lines.append(f"  Sight: {_display_value(row.get('sight_range'))}")
        lines.append(f"  Attributes: {_display_list(row.get('attributes'))}")
        attack = row.get("can_attack")
        lines.append(
            "  Can attack: varies by form"
            if attack == "varies_by_form" else f"  Can attack: {_display_value(attack)}"
        )
        if row.get("form_note"):
            lines.append(f"  {_display_value(row.get('form_note'))}")
        forms = row.get("forms")
        if isinstance(forms, Sequence) and not isinstance(forms, (str, bytes)) and forms:
            lines.append("  Forms:")
            for form in forms:
                if not isinstance(form, Mapping):
                    continue
                lines.append(
                    f"  - {_display_value(form.get('proto_name'))} "
                    f"id {_display_value(form.get('unit_id'))}, "
                    f"movement {_display_value(form.get('movement_layer'))}, "
                    f"can_attack {_display_value(form.get('can_attack'))}"
                )
                for weapon in form.get("weapons") or []:
                    lines.append(f"    Weapon: {_display_value(weapon)}")
        platform = row.get("platform_behavior")
        if (platform is not None and platform not in ("unknown", "not_applicable")
                and platform != row.get("description") and platform != row.get("form_note")):
            lines.append(f"  Platform behavior: {_display_value(platform)}")
        lines.append(f"  Description: {_display_value(row.get('description'))}")
    return "\n".join(lines)


def _render_map_overview(result: Mapping[str, Any]) -> str:
    topology = result.get("map_topology")
    if not isinstance(topology, Mapping):
        return "Map overview\nNo map topology is available."
    lines = ["Map overview"]
    lines.append(
        f"Distance basis: {_display_value(topology.get('distance_basis'))}; "
        f"neighbor basis: {_display_value(topology.get('neighbor_basis'))}"
    )
    lines.append(
        "Path coverage: "
        f"{_display_value(topology.get('verified_path_pair_count'))}/"
        f"{_display_value(topology.get('total_path_pair_count'))} verified pairs"
    )
    zones = topology.get("zones")
    if not isinstance(zones, Sequence) or isinstance(zones, (str, bytes)) or not zones:
        lines.append("Zones: none")
        return "\n".join(lines)
    lines.append("Zones:")
    for zone in zones:
        if not isinstance(zone, Mapping):
            continue
        neighbors = []
        for neighbor in zone.get("corridor_neighbors") or []:
            if isinstance(neighbor, Mapping):
                neighbors.append(
                    f"{_display_value(neighbor.get('zone_id'))} "
                    f"({_display_value(neighbor.get('path_distance'))})"
                )
        lines.append(
            f"- {_display_value(zone.get('zone_id'))}: "
            f"own-main distance={_display_value(zone.get('path_distance_from_own_main'))}; "
            f"enemy-main distance={_display_value(zone.get('path_distance_to_enemy_main'))}; "
            f"ramp={_display_value(zone.get('has_ramp'))}; "
            f"neighbors={', '.join(neighbors) if neighbors else 'none'}"
        )
    return "\n".join(lines)


def _render_zone_state(result: Mapping[str, Any]) -> str:
    resources_by_zone = {
        row.get("zone_id"): row
        for row in result.get("base_resources") or []
        if isinstance(row, Mapping)
    }
    lines = ["Zone state"]
    zones = result.get("zones")
    if not isinstance(zones, Sequence) or isinstance(zones, (str, bytes)) or not zones:
        lines.append("No matching zones were returned.")
    else:
        for zone in zones:
            if not isinstance(zone, Mapping):
                continue
            zone_id = zone.get("zone_id")
            lines.append(
                f"- {_display_value(zone_id)}: role={_display_value(zone.get('zone_role'))}; "
                f"owner={_display_value(zone.get('known_owner'))}; "
                f"vision={_display_value(zone.get('vision_state'))}; "
                "visible enemy weapon in range="
                f"{_display_value(zone.get('visible_enemy_weapon_in_range'))}"
            )
            own = zone.get("own_contents") if isinstance(zone.get("own_contents"), Mapping) else {}
            visible = zone.get("visible_enemy_contents") if isinstance(zone.get("visible_enemy_contents"), Mapping) else {}
            last_seen = zone.get("last_seen_enemy_contents") if isinstance(zone.get("last_seen_enemy_contents"), Mapping) else {}
            lines.append(
                f"  Own: units={_display_counts(own.get('units'))}; "
                f"buildings={_display_counts(own.get('buildings'))}"
            )
            lines.append(
                f"  Visible enemy: units={_display_counts(visible.get('units'))}; "
                f"buildings={_display_counts(visible.get('buildings'))}"
            )
            lines.append(
                f"  Last seen enemy: units={_display_counts(last_seen.get('units'))}; "
                f"buildings={_display_counts(last_seen.get('buildings'))}; "
                f"age={_display_value(zone.get('enemy_information_age_seconds'))}"
            )
            resource = resources_by_zone.get(zone_id)
            if isinstance(resource, Mapping):
                lines.append(
                    "  Resources: "
                    f"minerals={_display_value(resource.get('minerals_remaining'))}/"
                    f"{_display_value(resource.get('minerals_initial'))}; "
                    f"gas={_display_value(resource.get('vespene_remaining'))}/"
                    f"{_display_value(resource.get('vespene_initial'))}; "
                    f"gas structures={_display_value(resource.get('owned_gas_structure_count'))}; "
                    f"geysers available={_display_value(resource.get('available_geyser_slots'))}/"
                    f"{_display_value(resource.get('geyser_slots'))}; "
                    f"visibility={_display_value(resource.get('resource_visibility'))}"
                )
    unknown = result.get("unknown_zone_ids")
    if isinstance(unknown, Sequence) and not isinstance(unknown, (str, bytes)) and unknown:
        lines.append(f"Unknown zone IDs: {_display_list(unknown)}")
    return "\n".join(lines)


def render_tool_result(name: str, result: Any) -> str:
    """Render provider-facing tool output as compact, labeled text."""
    if not isinstance(result, Mapping):
        return f"Tool result for {name}\n{_display_value(result)}"
    if result.get("error") and name not in {
        "query_unit_data", "query_building_data", "query_research_data",
        "query_prerequisite_path",
    }:
        return f"Tool error for {name}: {_display_value(result.get('error'))}"
    if name == "query_map_overview":
        return _render_map_overview(result)
    if name == "query_zone_state":
        return _render_zone_state(result)
    if name == "query_route":
        segments = []
        for segment in result.get("segments") or []:
            if isinstance(segment, Mapping):
                segments.append(
                    f"{_display_value(segment.get('from'))} -> "
                    f"{_display_value(segment.get('to'))} "
                    f"({_display_value(segment.get('distance'))})"
                )
        return "\n".join((
            f"Route from {_display_value(result.get('from_zone'))} to {_display_value(result.get('to_zone'))}",
            f"Path: {' -> '.join(str(item) for item in result.get('path') or []) or 'none'}",
            f"Segments: {'; '.join(segments) if segments else 'none'}",
            f"Total path distance: {_display_value(result.get('total_distance'))}",
        ))
    if name == "query_unit_data":
        return _render_catalog_results(result, kind="unit")
    if name == "query_building_data":
        return _render_catalog_results(result, kind="building")
    if name == "query_research_data":
        return _render_catalog_results(result, kind="research")
    if name == "query_prerequisite_path":
        lines = [
            f"Prerequisite path for {_display_value(result.get('target'))}",
            "Static dependency path, not a match build order.",
        ]
        if result.get("error"):
            lines.append(f"Error: {_display_value(result.get('error'))}")
            if result.get("candidates"):
                lines.append(f"Close candidates: {_display_list(result.get('candidates'))}")
            return "\n".join(lines)
        lines.append("Step | Type | Target | Minerals | Gas | Time | Requires")
        for step in result.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            lines.append(
                f"{_display_value(step.get('step'))} | {_display_value(step.get('type'))} | "
                f"{_display_value(step.get('target'))} | {_display_value(step.get('minerals'))} | "
                f"{_display_value(step.get('gas'))} | {_display_value(step.get('time_seconds'))} | "
                f"{_display_list(step.get('requires'))}"
            )
        return "\n".join(lines)
    if name == "query_race_data":
        observable = result.get("observable_only") if isinstance(result.get("observable_only"), Mapping) else {}
        return "\n".join((
            f"{_display_value(result.get('race')).title()} catalog",
            f"Controllable units: {_display_list(result.get('units'))}",
            f"Controllable buildings: {_display_list(result.get('buildings'))}",
            f"Research: {_display_list(result.get('research'))}",
            f"Town hall morphs: {_display_list(result.get('morph_townhall'))}",
            f"Observable-only units: {_display_list(observable.get('units'))}",
            f"Observable-only buildings: {_display_list(observable.get('buildings'))}",
            f"Platform abilities: {_display_list(result.get('platform_abilities'))}",
        ))
    if result.get("status") == "queued" and isinstance(result.get("call"), Mapping):
        call = result["call"]
        arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        rendered_args = ", ".join(f"{key}={value}" for key, value in arguments.items())
        return f"Action queued: {_display_value(call.get('name'))}({rendered_args})"
    return f"Tool result for {name}: {_display_value(result)}"

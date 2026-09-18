"""Terran production observations; no commands or strategic decisions."""


# Preserve existing observation text decoding and priority. Runtime research
# ability IDs, when available, take precedence over the legacy text hints.
_TRAIN_ORDER_HINTS = (
    ("REAPER", "reaper"), ("GHOST", "ghost"),
    ("HELLBAT", "hellbat"), ("HELLIONTANK", "hellbat"),
    ("HELLION", "hellion"), ("WIDOWMINE", "widow_mine"),
    ("CYCLONE", "cyclone"), ("THOR", "thor"),
    ("VIKING", "viking"), ("LIBERATOR", "liberator"),
    ("RAVEN", "raven"), ("BATTLECRUISER", "battlecruiser"),
    ("MARAUDER", "marauder"), ("MARINE", "marine"),
    ("SIEGETANK", "siege_tank"), ("MEDIVAC", "medivac"),
    ("BANSHEE", "banshee"), ("SCV", "scv"),
)

_BUILD_ORDER_HINTS = (
    ("SUPPLYDEPOT", "supply_depot"), ("BARRACKS", "barracks"),
    ("FACTORY", "factory"), ("STARPORT", "starport"),
    ("ENGINEERINGBAY", "engineering_bay"), ("ARMORY", "armory"),
    ("REFINERY", "refinery"), ("COMMANDCENTER", "command_center"),
    ("BUNKER", "bunker"), ("MISSILETURRET", "missile_turret"),
    ("SENSORTOWER", "sensor_tower"), ("GHOSTACADEMY", "ghost_academy"),
    ("FUSIONCORE", "fusion_core"),
)


def _order_text(order):
    ability = getattr(order, "ability", None)
    ability_id = getattr(ability, "id", None) if ability is not None else None
    return str(ability_id or order).upper()


def worker_build_target(order):
    text = _order_text(order)
    if "BUILD" not in text and "WARP" not in text:
        return None
    for hint, target in _BUILD_ORDER_HINTS:
        if hint in text:
            return target
    return None


def production_order_target(order, game_data, adapter):
    ability = getattr(order, "ability", None)
    research = adapter.research_target_from_order(ability, game_data)
    if research is not None:
        return "research", research
    text = _order_text(order)
    if "RESEARCH" in text or "UPGRADE" in text:
        if "STIMPACK" in text:
            return "research", "stimpack"
        if "SHIELDWALL" in text or "COMBATSHIELD" in text:
            return "research", "combat_shield"
        if "PUNISHER" in text or "CONCUSSIVE" in text:
            return "research", "concussive_shells"
        if "BANSHEECLOAK" in text or "CLOAKINGFIELD" in text:
            return "research", "cloaking_field"
        if "INFANTRYWEAPONS" in text:
            return "research", "infantry_weapons_1"
        if "INFANTRYARMOR" in text:
            return "research", "infantry_armor_1"
        if "UPGRADETOORBITAL" in text or "ORBITALCOMMAND" in text:
            return "build", "orbital_command"
        if "UPGRADETOPLANETARY" in text or "PLANETARYFORTRESS" in text:
            return "build", "planetary_fortress"
        return None
    for hint, target in _TRAIN_ORDER_HINTS:
        if hint in text:
            return "train", target
    return None


def read_production_capacity(ai, adapter):
    """Ready grounded hosts and attached ready add-ons; unknown stays None."""
    structures = list(ai.structures)
    addons = {int(s.tag): adapter.normalize_unit_name(s.type_id.name)
              for s in structures if getattr(s, "is_ready", False)}
    rows = []
    for facility in ("command_center", "barracks", "factory", "starport"):
        names = adapter.townhall_targets if facility == "command_center" else (facility,)
        parents = [s for s in structures if adapter.normalize_unit_name(s.type_id.name) in names
                   and getattr(s, "build_progress", 0) >= 1]
        grounded = [s for s in parents if getattr(s, "is_flying", None) is False]
        unknown = any(not isinstance(getattr(s, "is_flying", None), bool) for s in parents)
        capacity = occupied = free = tech_free = tech_hosts = reactor_hosts = 0
        for parent in grounded:
            tag = getattr(parent, "add_on_tag", None)
            addon = addons.get(tag)
            reactor = addon == facility + "_reactor"
            techlab = addon == facility + "_techlab"
            slots = 2 if reactor else 1
            orders = getattr(parent, "orders", None)
            if orders is None or (facility != "command_center" and not isinstance(tag, int)):
                unknown = True
                continue
            busy = min(slots, len(orders))
            capacity += slots
            occupied += busy
            free += slots - busy
            tech_free += (slots - busy) if techlab else 0
            tech_hosts += int(techlab)
            reactor_hosts += int(reactor)
        row = {"facility": facility, "ready_grounded": len(grounded),
               "techlab_hosts": tech_hosts, "reactor_hosts": reactor_hosts,
               "capacity": capacity, "occupied_slots": occupied,
               "free_slots": free, "free_techlab_slots": tech_free}
        if unknown:
            row.update({key: None for key in row if key != "facility"})
        rows.append(row)
    return rows


def read_ability_facts(ai, buildings, adapter):
    """Preserve Terran ready-structure energy sampling and cast thresholds."""
    energies = [float(getattr(structure, "energy", 0.0) or 0.0)
                for structure in ai.structures.ready
                if adapter.normalize_unit_name(structure.type_id.name) == "orbital_command"]
    ready = sum(1 for energy in energies if energy >= 50.0)
    return {"orbital_count": int(buildings.get("orbital_command", 0)),
            "orbital_energies": [round(energy, 1) for energy in energies],
            "scan_ready": ready, "mule_ready": ready}

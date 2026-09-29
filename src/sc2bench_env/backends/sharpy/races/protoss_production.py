"""Protoss production observations; no commands or strategic decisions."""

from sharpy.plans.acts.act_unit import MAX_TRAIN_QUEUE


_TRAIN_ORDER_HINTS = (
    ("MOTHERSHIP", "mothership"),
    ("HIGHTEMPLAR", "high_templar"),
    ("DARKTEMPLAR", "dark_templar"),
    ("WARPPRISM", "warp_prism"),
    ("DISRUPTOR", "disruptor"),
    ("COLOSSUS", "colossus"),
    ("IMMORTAL", "immortal"),
    ("OBSERVER", "observer"),
    ("VOIDRAY", "void_ray"),
    ("TEMPEST", "tempest"),
    ("CARRIER", "carrier"),
    ("PHOENIX", "phoenix"),
    ("ORACLE", "oracle"),
    ("STALKER", "stalker"),
    ("SENTRY", "sentry"),
    ("ZEALOT", "zealot"),
    ("ADEPT", "adept"),
    ("PROBE", "probe"),
)

_BUILD_ORDER_HINTS = (
    ("CYBERNETICSCORE", "cybernetics_core"),
    ("PHOTONCANNON", "photon_cannon"),
    ("SHIELDBATTERY", "shield_battery"),
    ("ROBOTICSFACILITY", "robotics_facility"),
    ("ROBOTICSBAY", "robotics_bay"),
    ("FLEETBEACON", "fleet_beacon"),
    ("TWILIGHTCOUNCIL", "twilight_council"),
    ("TEMPLARARCHIVE", "templar_archives"),
    ("DARKSHRINE", "dark_shrine"),
    ("ASSIMILATOR", "assimilator"),
    ("GATEWAY", "gateway"),
    ("STARGATE", "stargate"),
    ("FORGE", "forge"),
    ("PYLON", "pylon"),
    ("NEXUS", "nexus"),
)

_FACILITIES = ("nexus", "gateway", "robotics_facility", "stargate")


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
        return None
    for hint, target in _TRAIN_ORDER_HINTS:
        if hint in text:
            return "train", target
    return None


def read_production_capacity(ai, adapter):
    """Ready producers and queue headroom. Warp Gates are a separate cooldown row."""
    structures = list(ai.structures)
    rows = []
    for facility in _FACILITIES:
        names = adapter.townhall_targets if facility == "nexus" else (facility,)
        parents = [
            structure for structure in structures
            if getattr(structure, "build_progress", 0) >= 1
            and (
                str(getattr(getattr(structure, "type_id", None), "name", "")) == "GATEWAY"
                if facility == "gateway"
                else adapter.normalize_unit_name(structure.type_id.name) in names
            )
        ]
        unknown = False
        capacity = occupied = free = 0
        queue_capacity = queued_orders = free_queue_positions = 0
        reserved_for_morph = 0
        reserved_tags = set(getattr(ai, "bench_warp_reserve_tags", set()) or ())
        for parent in parents:
            orders = getattr(parent, "orders", None)
            if orders is None:
                unknown = True
                continue
            slots = 1
            busy = min(slots, len(orders))
            capacity += slots
            occupied += busy
            free += slots - busy
            queue_capacity += MAX_TRAIN_QUEUE
            queued_orders += min(MAX_TRAIN_QUEUE, len(orders))
            free_queue_positions += max(0, MAX_TRAIN_QUEUE - len(orders))
            if facility == "gateway" and int(getattr(parent, "tag", 0) or 0) in reserved_tags:
                reserved_for_morph += 1
        row = {
            "facility": facility,
            "ready_grounded": len(parents),
            "capacity": capacity,
            "occupied_slots": occupied,
            "free_slots": free,
            "queue_capacity": queue_capacity,
            "queued_orders": queued_orders,
            "free_queue_positions": free_queue_positions,
        }
        if facility == "gateway":
            row["reserved_for_morph"] = reserved_for_morph
            row["waiting_for_queue"] = sum(
                1 for parent in parents
                if int(getattr(parent, "tag", 0) or 0) in reserved_tags
                and (getattr(parent, "orders", None) or [])
            )
        if unknown:
            row.update({key: None for key in row if key != "facility"})
        rows.append(row)
    status = getattr(ai, "bench_warpgate_status", None)
    if not isinstance(status, dict):
        status = {"total": None, "ready": None, "cooling": None}
    rows.append({
        "facility": "warpgate",
        "ready_grounded": status.get("total"),
        "capacity": status.get("total"),
        "occupied_slots": status.get("cooling"),
        "free_slots": status.get("ready"),
        "queue_capacity": None,
        "queued_orders": None,
        "free_queue_positions": None,
        "ready_to_warp": status.get("ready"),
        "cooling": status.get("cooling"),
    })
    return rows


def read_ability_facts(ai, buildings, adapter):
    """Nexus energy is observed. This does not choose a Chrono Boost target."""
    energies = [float(getattr(structure, "energy", 0.0) or 0.0)
                for structure in ai.structures.ready
                if adapter.normalize_unit_name(structure.type_id.name) == "nexus"]
    ready = sum(1 for energy in energies if energy >= 50.0)
    return {
        "nexus_count": int(buildings.get("nexus", 0)),
        "nexus_energies": [round(energy, 1) for energy in energies],
        "chrono_ready": ready,
    }

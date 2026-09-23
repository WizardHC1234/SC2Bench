"""Zerg production observations; no commands or strategic decisions."""

from sharpy.plans.acts.act_unit import MAX_TRAIN_QUEUE


_TRAIN_ORDER_HINTS = (
    ("BROODLORD", "brood_lord"),
    ("SWARMHOST", "swarm_host"),
    ("ULTRALISK", "ultralisk"),
    ("HYDRALISK", "hydralisk"),
    ("BANELING", "baneling"),
    ("CORRUPTOR", "corruptor"),
    ("MUTALISK", "mutalisk"),
    ("INFESTOR", "infestor"),
    ("OVERSEER", "overseer"),
    ("OVERLORD", "overlord"),
    ("ZERGLING", "zergling"),
    ("RAVAGER", "ravager"),
    ("LURKER", "lurker"),
    ("QUEEN", "queen"),
    ("ROACH", "roach"),
    ("VIPER", "viper"),
    ("DRONE", "drone"),
)

_BUILD_ORDER_HINTS = (
    ("GREATERSPIRE", "greater_spire"),
    ("ULTRALISKCAVERN", "ultralisk_cavern"),
    ("INFESTATIONPIT", "infestation_pit"),
    ("HYDRALISKDEN", "hydralisk_den"),
    ("LURKERDEN", "lurker_den"),
    ("BANELINGNEST", "baneling_nest"),
    ("EVOLUTIONCHAMBER", "evolution_chamber"),
    ("SPAWNINGPOOL", "spawning_pool"),
    ("ROACHWARREN", "roach_warren"),
    ("SPORECRAWLER", "spore_crawler"),
    ("SPINECRAWLER", "spine_crawler"),
    ("NYDUSNETWORK", "nydus_network"),
    ("EXTRACTOR", "extractor"),
    ("HATCHERY", "hatchery"),
    ("SPIRE", "spire"),
)


def _order_text(order):
    ability = getattr(order, "ability", None)
    ability_id = getattr(ability, "id", None) if ability is not None else None
    return str(ability_id or order).upper()


def worker_build_target(order):
    text = _order_text(order)
    if "BUILD" not in text and "MORPH" not in text:
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


def _queue_row(facility, parents):
    unknown = False
    capacity = occupied = free = 0
    queue_capacity = queued_orders = free_queue_positions = 0
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
    if unknown:
        row.update({key: None for key in row if key != "facility"})
    return row


def read_production_capacity(ai, adapter):
    """Larva headroom plus town-hall queues. Lairs and Hives count as hatcheries."""
    from sc2.ids.unit_typeid import UnitTypeId

    larvae = list(ai.units(UnitTypeId.LARVA))
    larva_count = len(larvae)
    rows = [{
        "facility": "larva",
        "ready_grounded": larva_count,
        "capacity": larva_count,
        "occupied_slots": 0,
        "free_slots": larva_count,
        "queue_capacity": larva_count,
        "queued_orders": 0,
        "free_queue_positions": larva_count,
    }]
    structures = list(ai.structures)
    townhalls = [s for s in structures
                 if adapter.normalize_unit_name(s.type_id.name) in adapter.townhall_targets
                 and getattr(s, "build_progress", 0) >= 1]
    rows.append(_queue_row("hatchery", townhalls))
    return rows


def read_ability_facts(ai, buildings, adapter):
    """Queen energy is observed. This does not cast Inject."""
    from sc2.ids.unit_typeid import UnitTypeId

    queens = list(ai.units(UnitTypeId.QUEEN).ready)
    energies = [float(getattr(queen, "energy", 0.0) or 0.0) for queen in queens]
    return {
        "queen_count": len(queens),
        "queen_energies": [round(energy, 1) for energy in energies],
        "inject_ready": sum(1 for energy in energies if energy >= 25.0),
    }

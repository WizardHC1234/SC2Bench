"""Terran targets, costs, prerequisites and reference notes."""

from __future__ import annotations

from types import MappingProxyType
from typing import Tuple

from sc2bench_env.interface.catalog_types import CatalogData, TargetSpec


def _building(
    name: str,
    *,
    description: str,
    minerals: int,
    seconds: float,
    vespene: int = 0,
    prerequisites: Tuple[str, ...] = (),
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="build",
        kind="building",
        description=description,
        minerals=minerals,
        vespene=vespene,
        base_time_seconds=seconds,
        prerequisites=prerequisites,
        semantics="append",
        success_boundary="unfinished_entity_appears",
    )


def _addon(
    name: str,
    *,
    description: str,
    minerals: int,
    vespene: int,
    seconds: float,
    parent: str,
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="build",
        kind="addon",
        description=description,
        minerals=minerals,
        vespene=vespene,
        base_time_seconds=seconds,
        prerequisites=(parent,),
        semantics="append",
        success_boundary="unfinished_entity_appears",
        produced_at=parent,
    )


def _unit(
    name: str,
    *,
    description: str,
    minerals: int,
    seconds: float,
    supply: int,
    producer: str,
    vespene: int = 0,
    prerequisites: Tuple[str, ...] = (),
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="train",
        kind="unit",
        description=description,
        minerals=minerals,
        vespene=vespene,
        supply=supply,
        base_time_seconds=seconds,
        prerequisites=tuple(dict.fromkeys((producer, *prerequisites))),
        semantics="append",
        success_boundary="units_produced",
        produced_at=producer,
    )


def _research(
    name: str,
    *,
    description: str,
    minerals: int,
    vespene: int,
    seconds: float,
    researched_at: str,
    prerequisites: Tuple[str, ...] = (),
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="research",
        kind="research",
        description=description,
        minerals=minerals,
        vespene=vespene,
        base_time_seconds=seconds,
        prerequisites=prerequisites or (researched_at,),
        semantics="idempotent",
        success_boundary="enters_research_queue",
        produced_at=researched_at,
    )


def _morph(
    name: str,
    *,
    description: str,
    minerals: int,
    seconds: float,
    morph_from: str,
    vespene: int = 0,
    prerequisites: Tuple[str, ...] = (),
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="upgrade",
        kind="morph",
        description=description,
        minerals=minerals,
        vespene=vespene,
        base_time_seconds=seconds,
        prerequisites=prerequisites,
        semantics="morph",
        success_boundary="morph_issued",
        morph_from=morph_from,
    )


def _ability(
    name: str,
    *,
    action: str,
    description: str,
    seconds: float = 1.0,
    energy: int = 0,
    prerequisites: Tuple[str, ...] = (),
    semantics: str = "oneshot",
    success_boundary: str = "ability_cast",
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action=action,
        kind="ability",
        description=description,
        energy=energy,
        base_time_seconds=seconds,
        prerequisites=prerequisites,
        semantics=semantics,
        success_boundary=success_boundary,
    )


def _combat(
    name: str,
    *,
    description: str,
) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="combat",
        kind="combat",
        description=description,
        semantics="append",
        success_boundary="mission_ended",
    )


# Phase 1/2 Terran reference catalog. Expanding this registry is the supported
# way to grow the Agent action space.
#
# Cost / producer / tech-chain values are checked against
# SC2-Commander/evol_agent/sc2_data_agent/data_sc2_260701 (Liquipedia LotV +
# data_base_sc2_260701.json). Build times use in-game seconds (JSON `time` is
# game loops at ~22.4/s).
TERRAN_TARGETS: Tuple[TargetSpec, ...] = (
    _building(
        "supply_depot",
        description="Build one Supply Depot (+8 supply capacity). Supply construction is agent-controlled; AutoDepot is disabled.",
        minerals=100,
        seconds=21,
    ),
    _building(
        "barracks",
        description="Build one Barracks.",
        minerals=150,
        seconds=46,
        prerequisites=("supply_depot",),
    ),
    _building(
        "factory",
        description="Build one Factory.",
        minerals=150,
        vespene=100,
        seconds=43,
        prerequisites=("barracks",),
    ),
    _building(
        "starport",
        description="Build one Starport.",
        minerals=150,
        vespene=100,
        seconds=36,
        prerequisites=("factory",),
    ),
    _building(
        "engineering_bay",
        description="Build one Engineering Bay.",
        minerals=125,
        seconds=25,
        prerequisites=("command_center",),
    ),
    _building(
        "armory",
        description="Build one Armory.",
        minerals=150,
        vespene=50,
        seconds=46,
        prerequisites=("factory",),
    ),
    _building(
        "refinery",
        description="Build one Refinery on a legal vespene geyser. This platform currently also requires an owned ready townhall; that is a platform restriction, not a game tech prerequisite.",
        minerals=75,
        seconds=21,
        prerequisites=("command_center",),
    ),
    _building(
        "command_center",
        description="Expand by building one Command Center at a legal site chosen by Sharpy.",
        minerals=400,
        seconds=71,
    ),
    _addon(
        "barracks_techlab",
        description="Add a Tech Lab to a Barracks.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="barracks",
    ),
    _addon(
        "barracks_reactor",
        description="Add a Reactor to a Barracks.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="barracks",
    ),
    _addon(
        "factory_techlab",
        description="Add a Tech Lab to a Factory.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="factory",
    ),
    _addon(
        "factory_reactor",
        description="Add a Reactor to a Factory.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="factory",
    ),
    _addon(
        "starport_techlab",
        description="Add a Tech Lab to a Starport.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="starport",
    ),
    _addon(
        "starport_reactor",
        description="Add a Reactor to a Starport.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="starport",
    ),
    _unit(
        "scv",
        description="Train SCVs from a Command Center / Orbital / Planetary.",
        minerals=50,
        seconds=12,
        supply=1,
        producer="command_center",
        prerequisites=("command_center",),
    ),
    _unit(
        "marine",
        description="Train Marines from Barracks.",
        minerals=50,
        seconds=18,
        supply=1,
        producer="barracks",
    ),
    _unit(
        "marauder",
        description="Train Marauders from Barracks with a Tech Lab.",
        minerals=100,
        vespene=25,
        seconds=21,
        supply=2,
        producer="barracks",
        prerequisites=("barracks_techlab",),
    ),
    _unit(
        "siege_tank",
        description="Train Siege Tanks from Factory with a Tech Lab.",
        minerals=150,
        vespene=125,
        seconds=32,
        supply=3,
        producer="factory",
        prerequisites=("factory_techlab",),
    ),
    _unit(
        "medivac",
        description="Train Medivacs from Starport.",
        minerals=100,
        vespene=100,
        seconds=30,
        supply=2,
        producer="starport",
    ),
    _unit(
        "banshee",
        description="Train Banshees from Starport with a Tech Lab.",
        minerals=150,
        vespene=100,
        seconds=43,
        supply=3,
        producer="starport",
        prerequisites=("starport_techlab",),
    ),
    _research(
        "stimpack",
        description="Research Stimpack at Barracks Tech Lab.",
        minerals=100,
        vespene=100,
        seconds=100,
        researched_at="barracks_techlab",
    ),
    _research(
        "combat_shield",
        description="Research Combat Shield at Barracks Tech Lab.",
        minerals=100,
        vespene=100,
        seconds=79,
        researched_at="barracks_techlab",
    ),
    _research(
        "concussive_shells",
        description="Research Concussive Shells at Barracks Tech Lab.",
        minerals=50,
        vespene=50,
        seconds=43,
        researched_at="barracks_techlab",
    ),
    _research(
        "cloaking_field",
        description="Research Banshee Cloaking Field at Starport Tech Lab.",
        minerals=100,
        vespene=100,
        seconds=79,
        researched_at="starport_techlab",
    ),
    _research(
        "infantry_weapons_1",
        description="Research Infantry Weapons Level 1 at Engineering Bay.",
        minerals=100,
        vespene=100,
        seconds=114,
        researched_at="engineering_bay",
    ),
    _research(
        "infantry_armor_1",
        description="Research Infantry Armor Level 1 at Engineering Bay.",
        minerals=100,
        vespene=100,
        seconds=114,
        researched_at="engineering_bay",
    ),
    _morph(
        "orbital_command",
        description="Morph a specific Command Center to Orbital Command via upgrade.",
        minerals=150,
        seconds=25,
        morph_from="command_center",
        prerequisites=("barracks",),
    ),
    _morph(
        "planetary_fortress",
        description="Morph a specific Command Center to Planetary Fortress via upgrade.",
        minerals=150,
        vespene=150,
        seconds=36,
        morph_from="command_center",
        prerequisites=("engineering_bay",),
    ),
    _ability(
        "scan",
        action="scan",
        description="Cast one Scanner Sweep within a stable zone_id: prefer the zone's HeatMap hotspot when available, otherwise its center. Requires one ready Orbital Command with at least 50 energy.",
        seconds=1,
        energy=50,
        prerequisites=("orbital_command",),
        success_boundary="ability_cast",
    ),
    _ability(
        "call_mule",
        action="call_mule",
        description="Call down one MULE at a ready own base not marked under attack; the backend chooses the base with most remaining minerals. Requires one ready Orbital Command with at least 50 energy.",
        seconds=1,
        energy=50,
        prerequisites=("orbital_command",),
        success_boundary="ability_cast",
    ),
    _ability(
        "scout",
        action="scout",
        description='Send one SCV along listed zones or route="all" for one non-own expansion-center sweep. New scout replaces unfinished work; death fails without replacement. No army retargeting or all-enemies-found guarantee.',
        seconds=8,
        prerequisites=("scv",),
        semantics="replace",
        success_boundary="route_completed",
    ),
    _combat(
        "attack",
        description=(
            "Bind dispatchable home army and attack the chosen zone. Backend handles local micro, transport and safety withdrawal; no automatic map search or reinforcements."
        ),
    ),
    _combat(
        "defend",
        description=(
            "Bind home army and defend a zone with bounded chasing; persists without attack's task-level power retreat. Fails immediately if requested "
            "counts exceed idle availability. Does not auto-reinforce."
        ),
    ),
)

# Complete multiplayer roster. Hellbat is directly trainable with an Armory;
# other alternate combat forms are aliases of their trainable base unit.
TERRAN_TARGETS += (
    _building("ghost_academy", description="Build one Ghost Academy.", minerals=150, vespene=50, seconds=29, prerequisites=("barracks",)),
    _building("fusion_core", description="Build one Fusion Core.", minerals=150, vespene=150, seconds=46, prerequisites=("starport",)),
    _building("bunker", description="Build one Bunker.", minerals=100, seconds=29, prerequisites=("barracks",)),
    _building("missile_turret", description="Build one Missile Turret.", minerals=100, seconds=18, prerequisites=("engineering_bay",)),
    _building("sensor_tower", description="Build one Sensor Tower.", minerals=100, vespene=50, seconds=18, prerequisites=("engineering_bay",)),
    _unit("reaper", description="Train Reapers from Barracks.", minerals=50, vespene=50, supply=1, seconds=32, producer="barracks"),
    _unit("ghost", description="Train Ghosts from a Barracks with Tech Lab and a ready Ghost Academy.", minerals=150, vespene=125, supply=3, seconds=29, producer="barracks", prerequisites=("barracks_techlab", "ghost_academy")),
    _unit("hellion", description="Train Hellions from Factory.", minerals=100, supply=2, seconds=21, producer="factory"),
    _unit("hellbat", description="Train Hellbats directly from Factory with a ready Armory. Hellion/Hellbat forms are reported separately; the backend preserves the requested form.", minerals=100, supply=2, seconds=21, producer="factory", prerequisites=("armory",)),
    _unit("widow_mine", description="Train Widow Mines from Factory; burrowing is controlled by the backend.", minerals=75, vespene=25, supply=2, seconds=21, producer="factory"),
    _unit("cyclone", description="Train Cyclones from Factory with a Tech Lab.", minerals=150, vespene=100, supply=3, seconds=32, producer="factory", prerequisites=("factory_techlab",)),
    _unit("thor", description="Train Thors from Factory with a Tech Lab and a ready Armory.", minerals=300, vespene=200, supply=6, seconds=43, producer="factory", prerequisites=("factory_techlab", "armory")),
    _unit("viking", description="Train Vikings from Starport; fighter/assault modes share one unit identity and are backend-controlled.", minerals=125, vespene=75, supply=2, seconds=30, producer="starport"),
    _unit("liberator", description="Train Liberators from Starport; defender mode is backend-controlled.", minerals=150, vespene=125, supply=3, seconds=43, producer="starport"),
    _unit("raven", description="Train Ravens from Starport with a Tech Lab.", minerals=100, vespene=150, supply=2, seconds=34, producer="starport", prerequisites=("starport_techlab",)),
    _unit("battlecruiser", description="Train Battlecruisers from Starport with a Tech Lab and a ready Fusion Core.", minerals=400, vespene=300, supply=6, seconds=64, producer="starport", prerequisites=("starport_techlab", "fusion_core")),
    _research("personal_cloaking", description="Research Ghost Personal Cloaking at Ghost Academy.", minerals=150, vespene=150, seconds=86, researched_at="ghost_academy"),
    _research("infernal_pre_igniter", description="Research Infernal Pre-Igniter at Factory Tech Lab.", minerals=100, vespene=100, seconds=79, researched_at="factory_techlab"),
    _research("drilling_claws", description="Research Drilling Claws at Factory Tech Lab with an Armory.", minerals=75, vespene=75, seconds=79, researched_at="factory_techlab", prerequisites=("factory_techlab", "armory")),
    _research("mag_field_accelerator", description="Research Cyclone Mag-Field Accelerator at Factory Tech Lab.", minerals=100, vespene=100, seconds=100, researched_at="factory_techlab"),
    _research("smart_servos", description="Research Smart Servos at Factory Tech Lab with an Armory.", minerals=100, vespene=100, seconds=79, researched_at="factory_techlab", prerequisites=("factory_techlab", "armory")),
    _research("hyperflight_rotors", description="Research Banshee Hyperflight Rotors at Starport Tech Lab.", minerals=125, vespene=125, seconds=79, researched_at="starport_techlab"),
    _research("yamato_cannon", description="Research Battlecruiser Weapon Refit (Yamato Cannon) at Fusion Core.", minerals=150, vespene=150, seconds=100, researched_at="fusion_core"),
    _research("advanced_ballistics", description="Research Liberator Advanced Ballistics at Fusion Core.", minerals=150, vespene=150, seconds=79, researched_at="fusion_core"),
    _research("caduceus_reactor", description="Research Medivac Caduceus Reactor at Fusion Core.", minerals=100, vespene=100, seconds=50, researched_at="fusion_core"),
    _research("interference_matrix", description="Research Raven Interference Matrix at Starport Tech Lab.", minerals=50, vespene=50, seconds=57, researched_at="starport_techlab"),
    _research("hi_sec_auto_tracking", description="Research building weapon range at Engineering Bay.", minerals=100, vespene=100, seconds=57, researched_at="engineering_bay"),
    _research("neosteel_armor", description="Research Neosteel Armor at Engineering Bay.", minerals=150, vespene=150, seconds=100, researched_at="engineering_bay"),
)


def _weapon_levels(prefix: str, label: str, building: str, *, infantry: bool = False) -> Tuple[TargetSpec, ...]:
    result = []
    for level, seconds in ((1, 114), (2, 136), (3, 157)):
        if infantry and level == 1:  # Already in the original registry.
            continue
        cost = (100, 150, 200)[level - 1] if infantry else (100, 175, 250)[level - 1]
        requirements = (building,)
        if level > 1:
            requirements += (f"{prefix}_{level - 1}",)
            if infantry:
                requirements += ("armory",)
        result.append(_research(
            f"{prefix}_{level}", description=f"Research {label} Level {level} at {building}.",
            minerals=cost, vespene=cost, seconds=seconds,
            researched_at=building, prerequisites=requirements,
        ))
    return tuple(result)


for _prefix, _label, _building_name in (
    ("infantry_weapons", "Infantry Weapons", "engineering_bay"),
    ("infantry_armor", "Infantry Armor", "engineering_bay"),
    ("vehicle_weapons", "Vehicle Weapons", "armory"),
    ("ship_weapons", "Ship Weapons", "armory"),
    ("vehicle_ship_armor", "Vehicle and Ship Plating", "armory"),
):
    TERRAN_TARGETS += _weapon_levels(_prefix, _label, _building_name, infantry=_prefix.startswith("infantry"))

_TARGET_TABLE_LEGEND = (
    "Costs: minerals/vespene per item; supply is population consumed, not supply capacity provided.",
    "Times: approximate game seconds per item from start to ready/finished; exclude resource/queue/travel waits and interruptions, not action completion. Multiple producers/Reactor slots run in parallel.",
    "Prerequisites must be ready/finished. facility is a common prerequisite; rows add to it. none means no extra.",
    "command_center accepts Orbital/Planetary townhalls. Add-ons need a grounded available parent and legal space.",
)

_TARGET_NOTES = {
    "build": ("- supply_depot provides +8 supply when ready.",),
    "train": (
        "- hellbat trains directly with Armory; Hellion/Hellbat stay separate identities.",
        "- Viking forms share one identity; Viking/Liberator forms and Widow Mine burrowing are backend-controlled.",
    ),
}


CATALOG = CatalogData(
    race="terran",
    targets=TERRAN_TARGETS,
    table_legend=_TARGET_TABLE_LEGEND,
    target_notes=MappingProxyType(_TARGET_NOTES),
)

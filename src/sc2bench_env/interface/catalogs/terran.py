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
        description="Produces Terran infantry and can host one Tech Lab or Reactor.",
        minerals=150,
        seconds=46,
        prerequisites=("supply_depot",),
    ),
    _building(
        "factory",
        description="Produces Terran vehicles and can host one Tech Lab or Reactor.",
        minerals=150,
        vespene=100,
        seconds=43,
        prerequisites=("barracks",),
    ),
    _building(
        "starport",
        description="Produces Terran air and flying support units and can host one Tech Lab or Reactor.",
        minerals=150,
        vespene=100,
        seconds=36,
        prerequisites=("factory",),
    ),
    _building(
        "engineering_bay",
        description="Unlocks infantry upgrades, Missile Turrets, Sensor Towers and the Planetary Fortress morph.",
        minerals=125,
        seconds=25,
        prerequisites=("command_center",),
    ),
    _building(
        "armory",
        description="Unlocks Hellbats, Thors, vehicle/ship upgrades and higher infantry upgrade levels.",
        minerals=150,
        vespene=50,
        seconds=46,
        prerequisites=("factory",),
    ),
    _building(
        "refinery",
        description="Provides vespene mining capacity on a legal geyser; worker assignment is automatic. This platform also requires an owned ready townhall, which is a platform restriction rather than a game tech prerequisite.",
        minerals=75,
        seconds=21,
        prerequisites=("command_center",),
    ),
    _building(
        "command_center",
        description="Expands to a legal resource site chosen by Sharpy, adds mining capacity, trains SCVs and can morph into an Orbital Command or Planetary Fortress.",
        minerals=400,
        seconds=71,
    ),
    _addon(
        "barracks_techlab",
        description="Attaches to one Barracks and unlocks its advanced units and research; it does not add a second production slot.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="barracks",
    ),
    _addon(
        "barracks_reactor",
        description="Attaches to one Barracks and permits two simultaneous compatible non-Tech-Lab unit queues.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="barracks",
    ),
    _addon(
        "factory_techlab",
        description="Attaches to one Factory and unlocks its advanced units and research; it does not add a second production slot.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="factory",
    ),
    _addon(
        "factory_reactor",
        description="Attaches to one Factory and permits two simultaneous compatible non-Tech-Lab unit queues.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="factory",
    ),
    _addon(
        "starport_techlab",
        description="Attaches to one Starport and unlocks its advanced units and research; it does not add a second production slot.",
        minerals=50,
        vespene=25,
        seconds=18,
        parent="starport",
    ),
    _addon(
        "starport_reactor",
        description="Attaches to one Starport and permits two simultaneous compatible non-Tech-Lab unit queues.",
        minerals=50,
        vespene=50,
        seconds=36,
        parent="starport",
    ),
    _unit(
        "scv",
        description="Worker. Gathers resources, constructs Terran buildings and repairs mechanical units and structures; not army-dispatchable.",
        minerals=50,
        seconds=12,
        supply=1,
        producer="command_center",
        prerequisites=("command_center",),
    ),
    _unit(
        "marine",
        description="General-purpose infantry. Attacks ground and air; Stimpack and Combat Shield improve combat performance after research.",
        minerals=50,
        seconds=18,
        supply=1,
        producer="barracks",
    ),
    _unit(
        "marauder",
        description="Armored infantry. Attacks ground with bonus damage against armored targets; Concussive Shells can slow targets after research.",
        minerals=100,
        vespene=25,
        seconds=21,
        supply=2,
        producer="barracks",
        prerequisites=("barracks_techlab",),
    ),
    _unit(
        "siege_tank",
        description="Armored ground artillery. Tank mode is mobile; siege mode has long-range ground splash but cannot move. Mode is backend-controlled.",
        minerals=150,
        vespene=125,
        seconds=32,
        supply=3,
        producer="factory",
        prerequisites=("factory_techlab",),
    ),
    _unit(
        "medivac",
        description="Flying support with no weapon. Heals biological units and transports infantry; healing and transport are backend-controlled.",
        minerals=100,
        vespene=100,
        seconds=30,
        supply=2,
        producer="starport",
    ),
    _unit(
        "banshee",
        description="Flying tactical aircraft that attacks ground only. Can cloak after Cloaking Field research; cloak use is backend-controlled.",
        minerals=150,
        vespene=100,
        seconds=43,
        supply=3,
        producer="starport",
        prerequisites=("starport_techlab",),
    ),
    _research(
        "stimpack",
        description="Enables backend-controlled Stimpack for Marines and Marauders, temporarily increasing movement and attack speed at a health cost.",
        minerals=100,
        vespene=100,
        seconds=100,
        researched_at="barracks_techlab",
    ),
    _research(
        "combat_shield",
        description="Permanently increases Marine health.",
        minerals=100,
        vespene=100,
        seconds=79,
        researched_at="barracks_techlab",
    ),
    _research(
        "concussive_shells",
        description="Makes Marauder attacks temporarily slow affected ground targets.",
        minerals=50,
        vespene=50,
        seconds=43,
        researched_at="barracks_techlab",
    ),
    _research(
        "cloaking_field",
        description="Enables backend-controlled Banshee cloaking, which consumes Banshee energy and requires enemy detection to reveal.",
        minerals=100,
        vespene=100,
        seconds=79,
        researched_at="starport_techlab",
    ),
    _research(
        "infantry_weapons_1",
        description="Increases attack damage for Terran infantry.",
        minerals=100,
        vespene=100,
        seconds=114,
        researched_at="engineering_bay",
    ),
    _research(
        "infantry_armor_1",
        description="Increases armor for Terran infantry.",
        minerals=100,
        vespene=100,
        seconds=114,
        researched_at="engineering_bay",
    ),
    _morph(
        "orbital_command",
        description="Morphs a Command Center into a town hall that retains SCV production, generates energy and enables Scanner Sweep and MULE calls.",
        minerals=150,
        seconds=25,
        morph_from="command_center",
        prerequisites=("barracks",),
    ),
    _morph(
        "planetary_fortress",
        description="Morphs a Command Center into an armored town hall with a ground weapon; it retains SCV production but has no Orbital energy abilities.",
        minerals=150,
        vespene=150,
        seconds=36,
        morph_from="command_center",
        prerequisites=("engineering_bay",),
    ),
    _ability(
        "scan",
        action="scan",
        description="Cast one Scanner Sweep for temporary vision and detection in a limited area of a stable zone_id: prefer the zone's HeatMap hotspot when available, otherwise its center. Requires one ready Orbital Command with at least 50 energy.",
        seconds=1,
        energy=50,
        prerequisites=("orbital_command",),
        success_boundary="ability_cast",
    ),
    _ability(
        "call_mule",
        action="call_mule",
        description="Call down one temporary MULE that automatically mines minerals at a ready own base not marked under attack; the backend chooses the base with most remaining minerals. Requires one ready Orbital Command with at least 50 energy.",
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
            "Bind home army and hold a zone's defensive point without chasing enemies or attacking structures; persists without attack's task-level power retreat. Fails immediately if requested "
            "counts exceed idle availability. Does not auto-reinforce."
        ),
    ),
)

# Complete multiplayer roster. Hellbat is directly trainable with an Armory;
# other alternate combat forms are aliases of their trainable base unit.
TERRAN_TARGETS += (
    _building("ghost_academy", description="Unlocks Ghost production and Ghost cloak research; nuclear strike is not exposed by this platform.", minerals=150, vespene=50, seconds=29, prerequisites=("barracks",)),
    _building("fusion_core", description="Unlocks Battlecruiser production and advanced Battlecruiser, Liberator and Medivac research.", minerals=150, vespene=150, seconds=46, prerequisites=("starport",)),
    _building("bunker", description="Defensive garrison. The backend loads free home Marines into a completed bunker; there is no load or unload action.", minerals=100, seconds=29, prerequisites=("barracks",)),
    _building("missile_turret", description="Static anti-air weapon and detector for cloaked or burrowed enemies; cannot attack ground.", minerals=100, seconds=18, prerequisites=("engineering_bay",)),
    _building("sensor_tower", description="Shows enemy movement in its in-game sensor radius through fog, but has no weapon and is not a detector; SC2Bench does not expose sensor contacts in Observation.", minerals=100, vespene=50, seconds=18, prerequisites=("engineering_bay",)),
    _unit("reaper", description="Fast ground raider that attacks ground and is effective against light targets.", minerals=50, vespene=50, supply=1, seconds=32, producer="barracks"),
    _unit("ghost", description="Specialist infantry that attacks ground and air. Backend uses cloak, EMP and Steady Targeting when available; nuclear strike is not exposed.", minerals=150, vespene=125, supply=3, seconds=29, producer="barracks", prerequisites=("barracks_techlab", "ghost_academy")),
    _unit("hellion", description="Fast vehicle with a line ground attack that is effective against light targets.", minerals=100, supply=2, seconds=21, producer="factory"),
    _unit("hellbat", description="Durable short-range ground flamethrower effective against groups of light units. Trains directly in this form; no manual transform action.", minerals=100, supply=2, seconds=21, producer="factory", prerequisites=("armory",)),
    _unit("widow_mine", description="Burrowing ambusher that can hit ground or air with splash damage and a cooldown. Burrowing is backend-controlled.", minerals=75, vespene=25, supply=2, seconds=21, producer="factory"),
    _unit("cyclone", description="Mobile vehicle that attacks ground and air; Lock On is backend-controlled.", minerals=150, vespene=100, supply=3, seconds=32, producer="factory", prerequisites=("factory_techlab",)),
    _unit("thor", description="Heavy armored vehicle that attacks ground and air; anti-air weapon mode is backend-controlled.", minerals=300, vespene=200, supply=6, seconds=43, producer="factory", prerequisites=("factory_techlab", "armory")),
    _unit("viking", description="Flying fighter effective against armored air units; fighter and landed assault modes share one identity and are backend-controlled.", minerals=125, vespene=75, supply=2, seconds=30, producer="starport"),
    _unit("liberator", description="Flying unit that attacks air in mobile mode and ground units in deployed mode. Mode is backend-controlled; deployed fire does not hit structures.", minerals=150, vespene=125, supply=3, seconds=43, producer="starport"),
    _unit("raven", description="Flying detector and support caster with no basic weapon. Supported abilities are cast by the backend.", minerals=100, vespene=150, supply=2, seconds=34, producer="starport", prerequisites=("starport_techlab",)),
    _unit("battlecruiser", description="Heavy flying capital ship that attacks ground and air. Yamato Cannon is used by the backend after research.", minerals=400, vespene=300, supply=6, seconds=64, producer="starport", prerequisites=("starport_techlab", "fusion_core")),
    _research("personal_cloaking", description="Enables backend-controlled Ghost cloaking, which consumes Ghost energy and requires enemy detection to reveal.", minerals=150, vespene=150, seconds=86, researched_at="ghost_academy"),
    _research("infernal_pre_igniter", description="Increases Hellion and Hellbat damage against light units.", minerals=100, vespene=100, seconds=79, researched_at="factory_techlab"),
    _research("drilling_claws", description="Makes Widow Mines burrow and unburrow faster; burrowing remains backend-controlled.", minerals=75, vespene=75, seconds=79, researched_at="factory_techlab", prerequisites=("factory_techlab", "armory")),
    _research("mag_field_accelerator", description="Improves Cyclone Lock On against armored targets; Lock On remains backend-controlled.", minerals=100, vespene=100, seconds=100, researched_at="factory_techlab"),
    _research("smart_servos", description="Speeds Viking transformation. Hellion and Hellbat stay separate train targets; this platform does not auto-morph them.", minerals=100, vespene=100, seconds=79, researched_at="factory_techlab", prerequisites=("factory_techlab", "armory")),
    _research("hyperflight_rotors", description="Increases Banshee movement speed.", minerals=125, vespene=125, seconds=79, researched_at="starport_techlab"),
    _research("yamato_cannon", description="Enables backend-controlled Yamato Cannon for Battlecruisers, a high-damage single-target ability.", minerals=150, vespene=150, seconds=100, researched_at="fusion_core"),
    _research("advanced_ballistics", description="Increases the attack range of Liberators in deployed anti-ground mode.", minerals=150, vespene=150, seconds=79, researched_at="fusion_core"),
    _research("caduceus_reactor", description="Improves Medivac energy regeneration for sustained biological-unit healing.", minerals=100, vespene=100, seconds=50, researched_at="fusion_core"),
    _research("interference_matrix", description="Enables backend-controlled Raven Interference Matrix, which temporarily disables an enemy mechanical or psionic unit.", minerals=50, vespene=50, seconds=57, researched_at="starport_techlab"),
    _research("hi_sec_auto_tracking", description="Increases the attack range of applicable Terran defensive structures.", minerals=100, vespene=100, seconds=57, researched_at="engineering_bay"),
    _research("neosteel_armor", description="Increases armor for Terran structures.", minerals=150, vespene=150, seconds=100, researched_at="engineering_bay"),
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
            f"{prefix}_{level}", description=(
                f"Increases {label.lower()} to level {level} for the affected Terran units."
            ),
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
    "M/G, supply and time are per item; time excludes waiting.",
    "Builder/producer/facility/source is required; extra lists other prerequisites. command_center includes its morphs.",
)

_TARGET_NOTES = {
    "build": ("- supply_depot provides +8 supply when ready.",),
    "train": (
        "- hellbat trains directly with Armory; Hellion/Hellbat stay separate identities.",
        "- Viking forms share one identity; Viking/Liberator forms and Widow Mine burrowing are backend-controlled.",
    ),
}

_PROMPT_DESCRIPTIONS = {
    "supply_depot": "Adds 8 supply.",
    "barracks": "Infantry producer; supports Tech Lab/Reactor.",
    "factory": "Vehicle producer; supports Tech Lab/Reactor.",
    "starport": "Air producer; supports Tech Lab/Reactor.",
    "engineering_bay": "Infantry upgrades and static-defense tech.",
    "armory": "Advanced vehicle/air tech and upgrades.",
    "refinery": "Gas mining structure.",
    "command_center": "Expansion, SCV production and mining capacity.",
    "barracks_techlab": "Barracks advanced units/research.",
    "barracks_reactor": "Two parallel eligible Barracks slots.",
    "factory_techlab": "Factory advanced units/research.",
    "factory_reactor": "Two parallel eligible Factory slots.",
    "starport_techlab": "Starport advanced units/research.",
    "starport_reactor": "Two parallel eligible Starport slots.",
    "ghost_academy": "Ghost production and cloak research.",
    "fusion_core": "Battlecruiser and advanced air tech.",
    "bunker": "Infantry garrison defense.",
    "missile_turret": "Static anti-air and detector.",
    "sensor_tower": "Detects movement in fog; no weapon/detection.",
    "scv": "Worker: mines, builds and repairs.",
    "marine": "Infantry; attacks ground and air.",
    "marauder": "Armored infantry; attacks ground only.",
    "siege_tank": "Ground artillery; automatic siege mode.",
    "medivac": "Flying healer/transport; no weapon.",
    "banshee": "Flying ground attacker; cloak after research.",
    "reaper": "Fast anti-light ground raider.",
    "ghost": "Infantry caster; attacks ground/air.",
    "hellion": "Fast line-area ground attacker.",
    "hellbat": "Durable short-range anti-light unit.",
    "widow_mine": "Burrowed splash attack against ground/air.",
    "cyclone": "Mobile ground/air attacker with Lock On.",
    "thor": "Heavy ground/air vehicle.",
    "viking": "Anti-air fighter; automatic landed form.",
    "liberator": "Air attack; deployed mode attacks ground units.",
    "raven": "Flying detector/support caster; no weapon.",
    "battlecruiser": "Heavy flying ground/air attacker.",
    "stimpack": "Marine/Marauder speed and attack-speed ability.",
    "combat_shield": "Increases Marine health.",
    "concussive_shells": "Marauder attacks slow ground targets.",
    "cloaking_field": "Enables Banshee cloak.",
    "infantry_weapons_1": "Infantry attack level 1.",
    "infantry_armor_1": "Infantry armor level 1.",
    "personal_cloaking": "Enables Ghost cloak.",
    "infernal_pre_igniter": "Hellion/Hellbat bonus vs light.",
    "drilling_claws": "Faster Widow Mine burrow/unburrow.",
    "mag_field_accelerator": "Improves Cyclone Lock On vs armored.",
    "smart_servos": "Faster Viking transformation. Hellion and Hellbat are not auto-morphed.",
    "hyperflight_rotors": "Increases Banshee speed.",
    "yamato_cannon": "Enables Battlecruiser Yamato Cannon.",
    "advanced_ballistics": "Increases deployed Liberator range.",
    "caduceus_reactor": "Improves Medivac energy regeneration.",
    "interference_matrix": "Enables Raven Interference Matrix.",
    "hi_sec_auto_tracking": "Increases defensive-structure range.",
    "neosteel_armor": "Increases structure armor.",
    "orbital_command": "Town hall with energy, Scan and MULE.",
    "planetary_fortress": "Armored town hall with ground weapon.",
    "scan": "Temporary local vision and detection.",
    "call_mule": "Temporary mineral-gathering MULE.",
    "scout": "SCV route; all sweeps non-own expansions.",
}

for _prefix, _label in (
    ("infantry_weapons", "Infantry attack"),
    ("infantry_armor", "Infantry armor"),
    ("vehicle_weapons", "Vehicle attack"),
    ("ship_weapons", "Ship attack"),
    ("vehicle_ship_armor", "Vehicle/ship armor"),
):
    for _level in (1, 2, 3):
        _PROMPT_DESCRIPTIONS.setdefault(f"{_prefix}_{_level}", f"{_label} level {_level}.")

_PROMPT_DESCRIPTIONS = MappingProxyType(_PROMPT_DESCRIPTIONS)


CATALOG = CatalogData(
    race="terran",
    targets=TERRAN_TARGETS,
    table_legend=_TARGET_TABLE_LEGEND,
    target_notes=MappingProxyType(_TARGET_NOTES),
    prompt_descriptions=_PROMPT_DESCRIPTIONS,
)

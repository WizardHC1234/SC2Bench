"""Zerg targets, costs, prerequisites and reference notes.

Mineral and gas figures are the current multiplayer cost of that action.
Morph rows are the extra cost, not the source unit plus the morph.
"""

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


def _combat(name: str, *, description: str) -> TargetSpec:
    return TargetSpec(
        name=name,
        action="combat",
        kind="combat",
        description=description,
        semantics="append",
        success_boundary="mission_ended",
    )


ZERG_TARGETS: Tuple[TargetSpec, ...] = (
    _building("hatchery", description="Town hall, larva and mining capacity. A completed Hatchery provides 4 supply. Lair and Hive keep that same 4; morphing does not add more.", minerals=300, seconds=71),
    _building("extractor", description="Gas mining structure on a geyser.", minerals=25, seconds=21, prerequisites=("hatchery",)),
    _building("spawning_pool", description="Zergling and Queen tech.", minerals=200, seconds=46, prerequisites=("hatchery",)),
    _building("evolution_chamber", description="Ground upgrades.", minerals=75, seconds=25, prerequisites=("hatchery",)),
    _building("spine_crawler", description="Static ground weapon. It roots on creep.", minerals=100, seconds=36, prerequisites=("spawning_pool",)),
    _building("spore_crawler", description="Static detector and anti-air weapon. It roots on creep.", minerals=75, seconds=21, prerequisites=("spawning_pool",)),
    _building("roach_warren", description="Roach production.", minerals=150, seconds=39, prerequisites=("spawning_pool",)),
    _building("baneling_nest", description="Baneling morph tech.", minerals=100, vespene=50, seconds=43, prerequisites=("spawning_pool",)),
    _building("hydralisk_den", description="Hydralisk production.", minerals=100, vespene=100, seconds=29, prerequisites=("lair",)),
    _building("lurker_den", description="Morphs a completed Hydralisk Den. Hydralisk production remains available.", minerals=100, vespene=150, seconds=57, prerequisites=("hydralisk_den",)),
    _building("infestation_pit", description="Infestor and Swarm Host tech.", minerals=100, vespene=100, seconds=36, prerequisites=("lair",)),
    _building("spire", description="Mutalisk and Corruptor production, plus air upgrades.", minerals=200, vespene=200, seconds=71, prerequisites=("lair",)),
    _building("nydus_network", description="Nydus Network. Placing a worm is not an action.", minerals=150, vespene=150, seconds=36, prerequisites=("lair",)),
    _building("ultralisk_cavern", description="Ultralisk production.", minerals=150, vespene=200, seconds=46, prerequisites=("hive",)),
    _building("greater_spire", description="Morphs a completed Spire once a Hive exists. Mutalisk and Corruptor production remains available.", minerals=100, vespene=150, seconds=71, prerequisites=("spire", "hive")),
    _morph("lair", description="Morph the selected Hatchery. Larva and Queen production stay on this town hall.", minerals=150, vespene=100, seconds=57, morph_from="hatchery", prerequisites=("spawning_pool",)),
    _morph("hive", description="Morph the selected Lair.", minerals=200, vespene=150, seconds=71, morph_from="lair", prerequisites=("infestation_pit",)),
    _unit("drone", description="Worker: mines and builds. It does not repair.", minerals=50, supply=1, seconds=12, producer="hatchery"),
    _unit("overlord", description="Adds 8 supply when complete. Overlords are not built automatically.", minerals=100, supply=0, seconds=18, producer="hatchery"),
    _unit("queen", description="Support unit trained at a town hall. Transfusion is backend-controlled. inject_larva is a separate action and is not cast automatically.", minerals=150, supply=2, seconds=36, producer="hatchery", prerequisites=("spawning_pool",)),
    _unit("zergling", description="Ground melee fighter. One larva order produces two, and two use 1 supply. The supply column shows 1.", minerals=25, supply=1, seconds=17, producer="hatchery", prerequisites=("spawning_pool",)),
    _unit("baneling", description="Morphs an existing Zergling. Does not train Zerglings. Two Banelings use 1 supply.", minerals=25, vespene=25, supply=1, seconds=14, producer="zergling", prerequisites=("baneling_nest",)),
    _unit("roach", description="Armored ground fighter.", minerals=75, vespene=25, supply=2, seconds=19, producer="hatchery", prerequisites=("roach_warren",)),
    _unit("ravager", description="Morphs an existing Roach and adds 1 supply. Corrosive Bile is backend-controlled.", minerals=25, vespene=75, supply=1, seconds=12, producer="roach", prerequisites=("roach_warren",)),
    _unit("hydralisk", description="Ground attacker that also shoots air.", minerals=100, vespene=50, supply=2, seconds=24, producer="hatchery", prerequisites=("hydralisk_den",)),
    _unit("lurker", description="Morphs an existing Hydralisk and adds 1 supply. Burrow is backend-controlled.", minerals=50, vespene=100, supply=1, seconds=25, producer="hydralisk", prerequisites=("lurker_den",)),
    _unit("infestor", description="Ground caster. Fungal Growth and Neural Parasite are backend-controlled after research.", minerals=100, vespene=150, supply=2, seconds=36, producer="hatchery", prerequisites=("infestation_pit",)),
    _unit("swarm_host", description="Siege unit. Locusts are backend-controlled.", minerals=100, vespene=75, supply=3, seconds=29, producer="hatchery", prerequisites=("infestation_pit",)),
    _unit("mutalisk", description="Flying attacker against ground and air.", minerals=100, vespene=100, supply=2, seconds=24, producer="hatchery", prerequisites=("spire",)),
    _unit("corruptor", description="Flying anti-air fighter.", minerals=150, vespene=100, supply=2, seconds=29, producer="hatchery", prerequisites=("spire",)),
    _unit("overseer", description="Morphs an existing Overlord into a flying detector. Does not add supply.", minerals=50, vespene=50, supply=0, seconds=12, producer="overlord", prerequisites=("lair",)),
    _unit("viper", description="Flying caster. Abduct, Blinding Cloud and Consume are backend-controlled.", minerals=100, vespene=200, supply=3, seconds=29, producer="hatchery", prerequisites=("hive",)),
    _unit("ultralisk", description="Heavy ground melee fighter.", minerals=300, vespene=200, supply=6, seconds=39, producer="hatchery", prerequisites=("ultralisk_cavern",)),
    _unit("brood_lord", description="Morphs an existing Corruptor and adds 2 supply. Broodlings are backend-controlled.", minerals=150, vespene=150, supply=2, seconds=24, producer="corruptor", prerequisites=("greater_spire",)),
    _research("metabolic_boost", description="Zerglings move faster.", minerals=100, vespene=100, seconds=79, researched_at="spawning_pool"),
    _research("adrenal_glands", description="Zerglings attack faster.", minerals=200, vespene=200, seconds=93, researched_at="spawning_pool", prerequisites=("spawning_pool", "hive")),
    _research("glial_reconstitution", description="Roaches move faster. The backend uses it.", minerals=100, vespene=100, seconds=79, researched_at="roach_warren", prerequisites=("roach_warren", "lair")),
    _research("tunneling_claws", description="Burrowed Roaches move faster.", minerals=100, vespene=100, seconds=79, researched_at="roach_warren", prerequisites=("roach_warren", "lair")),
    _research("muscular_augments", description="Hydralisks move faster.", minerals=100, vespene=100, seconds=64, researched_at="hydralisk_den", prerequisites=("hydralisk_den", "lair")),
    _research("grooved_spines", description="Increases Hydralisk attack range.", minerals=75, vespene=75, seconds=50, researched_at="hydralisk_den"),
    _research("lurker_range", description="Increases Lurker attack range.", minerals=150, vespene=150, seconds=57, researched_at="lurker_den"),
    _research("burrow", description="Lets units burrow. Lurker burrow is backend-controlled.", minerals=100, vespene=100, seconds=71, researched_at="hatchery"),
    _research("pneumatized_carapace", description="Overlords and Overseers move faster.", minerals=100, vespene=100, seconds=43, researched_at="hatchery"),
    _research("chitinous_plating", description="Increases Ultralisk armor.", minerals=150, vespene=150, seconds=79, researched_at="ultralisk_cavern"),
    _research("anabolic_synthesis", description="Ultralisks move faster.", minerals=150, vespene=150, seconds=43, researched_at="ultralisk_cavern"),
    _research("neural_parasite", description="Infestors can cast Neural Parasite. The backend uses it.", minerals=150, vespene=150, seconds=79, researched_at="infestation_pit"),
    _research("microbial_shroud", description="Infestors can cast Microbial Shroud. The backend uses it.", minerals=150, vespene=150, seconds=79, researched_at="infestation_pit"),
    _ability(
        "spawn_creep_tumor",
        action="spawn_creep_tumor",
        description="Spend 25 Queen energy to plant one creep tumor on creep, toward the enemy. A burrowed tumor spreads the next one when it can. It is not cast automatically.",
        energy=25,
        prerequisites=("queen",),
    ),
    _ability(
        "inject_larva",
        action="inject_larva",
        description="Spend 25 Queen energy to inject one Hatchery, Lair or Hive. The backend chooses the Queen and town hall. It is not cast automatically.",
        energy=25,
        prerequisites=("queen",),
    ),
    _ability(
        "scout",
        action="scout",
        description='Send one Drone along listed zones or route="all" for one non-own expansion-center sweep. New scout replaces unfinished work; death fails without replacement.',
        seconds=8,
        prerequisites=("drone",),
        semantics="replace",
        success_boundary="route_completed",
    ),
    _combat(
        "attack",
        description="Bind dispatchable home army and attack the chosen zone. Backend handles local micro; no automatic map search or reinforcements.",
    ),
    _combat(
        "defend",
        description="Bind home army and hold a zone's defensive point without chasing enemies or attacking structures. Does not auto-reinforce.",
    ),
)


def _chamber_levels(prefix: str, label: str, costs: Tuple[int, int, int]) -> Tuple[TargetSpec, ...]:
    result = []
    times = (114, 136, 157)
    for level, minerals, seconds in zip((1, 2, 3), costs, times):
        requirements = ("evolution_chamber",)
        if level == 2:
            requirements += (f"{prefix}_1", "lair")
        elif level == 3:
            requirements += (f"{prefix}_2", "hive")
        result.append(_research(
            f"{prefix}_{level}",
            description=f"Increases {label.lower()} to level {level}.",
            minerals=minerals,
            vespene=minerals,
            seconds=seconds,
            researched_at="evolution_chamber",
            prerequisites=requirements,
        ))
    return tuple(result)


def _spire_levels(prefix: str, label: str) -> Tuple[TargetSpec, ...]:
    result = []
    for level, minerals, seconds in zip((1, 2, 3), (100, 175, 250), (114, 136, 157)):
        requirements = ("spire",)
        if level == 2:
            requirements += (f"{prefix}_1", "lair")
        elif level == 3:
            requirements += (f"{prefix}_2", "hive")
        result.append(_research(
            f"{prefix}_{level}",
            description=f"Increases {label.lower()} to level {level}.",
            minerals=minerals,
            vespene=minerals,
            seconds=seconds,
            researched_at="spire",
            prerequisites=requirements,
        ))
    return tuple(result)


ZERG_TARGETS += _chamber_levels("melee_attacks", "melee attacks", (100, 150, 200))
ZERG_TARGETS += _chamber_levels("missile_attacks", "missile attacks", (100, 150, 200))
ZERG_TARGETS += _chamber_levels("ground_carapace", "ground carapace", (150, 200, 250))
ZERG_TARGETS += _spire_levels("flyer_attacks", "flyer attacks")
ZERG_TARGETS += _spire_levels("flyer_carapace", "flyer carapace")

_TARGET_TABLE_LEGEND = (
    "M/G, supply and time are per item; time excludes waiting.",
    "Builder/producer/facility is required; extra lists other prerequisites.",
)

_TARGET_NOTES = {
    "build": (
        "- A completed Hatchery, Lair or Hive provides 4 supply. Morphing among them does not add more. A completed Overlord adds 8 supply and is a train target, not an automatic building.",
        "- Lurker Den morphs a Hydralisk Den. Greater Spire morphs a Spire. Creep tumors and Nydus worms are not actions.",
    ),
    "train": (
        "- Drones, Overlords and army units except Queens, morphs and the Queen itself come from larva at a Hatchery, Lair or Hive.",
        "- Baneling, Ravager, Lurker, Overseer and Brood Lord morph existing units. They do not train the source unit.",
        "- Two Zerglings or Banelings use 1 supply. The supply column shows 1.",
    ),
    "upgrade": (
        "- Lair morphs one Hatchery. Hive morphs one Lair. Use the town hall structure id.",
    ),
}

_PROMPT_DESCRIPTIONS = {
    "hatchery": "Town hall and larva.",
    "extractor": "Gas mining structure.",
    "spawning_pool": "Zergling and Queen tech.",
    "evolution_chamber": "Ground upgrades.",
    "spine_crawler": "Static ground weapon.",
    "spore_crawler": "Static detector and anti-air.",
    "roach_warren": "Roach production.",
    "baneling_nest": "Baneling morph tech.",
    "hydralisk_den": "Hydralisk production.",
    "lurker_den": "Morphs a Hydralisk Den.",
    "infestation_pit": "Infestor and Swarm Host tech.",
    "spire": "Air production and air upgrades.",
    "nydus_network": "Nydus Network. Worms are not an action.",
    "ultralisk_cavern": "Ultralisk production.",
    "greater_spire": "Morphs a Spire.",
    "lair": "Morph a Hatchery.",
    "hive": "Morph a Lair.",
    "drone": "Worker: mines and builds.",
    "overlord": "Adds 8 supply. Not automatic.",
    "queen": "Town-hall support. inject_larva is a separate action.",
    "zergling": "Melee fighter. Two per larva order.",
    "baneling": "Morphs a Zergling.",
    "roach": "Armored ground fighter.",
    "ravager": "Morphs a Roach.",
    "hydralisk": "Attacks ground and air.",
    "lurker": "Morphs a Hydralisk.",
    "infestor": "Ground caster.",
    "swarm_host": "Siege unit.",
    "mutalisk": "Flying attacker.",
    "corruptor": "Flying anti-air fighter.",
    "overseer": "Morphs an Overlord into a detector. Contaminate and Oversight are backend-controlled.",
    "viper": "Flying caster.",
    "ultralisk": "Heavy ground fighter.",
    "brood_lord": "Morphs a Corruptor.",
    "metabolic_boost": "Faster Zerglings.",
    "adrenal_glands": "Faster Zergling attacks.",
    "glial_reconstitution": "Faster Roaches.",
    "tunneling_claws": "Burrowed Roaches move.",
    "muscular_augments": "Faster Hydralisks.",
    "grooved_spines": "Longer Hydralisk range.",
    "lurker_range": "Longer Lurker range.",
    "burrow": "Enables burrow.",
    "pneumatized_carapace": "Faster Overlords.",
    "chitinous_plating": "Ultralisk armor.",
    "anabolic_synthesis": "Faster Ultralisks.",
    "neural_parasite": "Infestor mind control.",
    "microbial_shroud": "Infestor anti-air shroud.",
    "inject_larva": "Inject Larva. Backend chooses the Queen and town hall. Not automatic.",
    "spawn_creep_tumor": "Plants one creep tumor. Backend chooses the Queen or a burrowed tumor. Not automatic.",
    "scout": "Drone route; all sweeps non-own expansions.",
}

for _prefix, _label in (
    ("melee_attacks", "Melee attack"),
    ("missile_attacks", "Missile attack"),
    ("ground_carapace", "Ground carapace"),
    ("flyer_attacks", "Flyer attack"),
    ("flyer_carapace", "Flyer carapace"),
):
    for _level in (1, 2, 3):
        _PROMPT_DESCRIPTIONS.setdefault(f"{_prefix}_{_level}", f"{_label} level {_level}.")

_PROMPT_DESCRIPTIONS = MappingProxyType(_PROMPT_DESCRIPTIONS)


CATALOG = CatalogData(
    race="zerg",
    targets=ZERG_TARGETS,
    table_legend=_TARGET_TABLE_LEGEND,
    target_notes=MappingProxyType(_TARGET_NOTES),
    prompt_descriptions=_PROMPT_DESCRIPTIONS,
)

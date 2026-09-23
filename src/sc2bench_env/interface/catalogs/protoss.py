"""Protoss targets, costs, prerequisites and reference notes."""

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


# Costs and tech chains follow the same LotV source as the Terran catalog:
# SC2-Commander data_base_sc2_260701.json. Times are in-game seconds.
PROTOSS_TARGETS: Tuple[TargetSpec, ...] = (
    _building(
        "nexus",
        description="Expands to a legal resource site, adds mining capacity and trains Probes. It generates energy. Spending that energy is not an action.",
        minerals=400,
        seconds=71,
    ),
    _building(
        "pylon",
        description="Build one Pylon (+8 supply and building power). Pylon construction is agent-controlled; the backend does not place Pylons automatically.",
        minerals=100,
        seconds=18,
    ),
    _building(
        "assimilator",
        description="Gas mining structure on a free geyser at a ready owned Nexus.",
        minerals=75,
        seconds=21,
        prerequisites=("nexus",),
    ),
    _building(
        "gateway",
        description="Ground army producer. After Warp Gate research finishes, the backend morphs a completed Gateway into a Warp Gate. There is no separate morph action.",
        minerals=150,
        seconds=46,
        prerequisites=("pylon",),
    ),
    _building(
        "forge",
        description="Ground weapon, armor and shield upgrades, and Photon Cannon tech.",
        minerals=150,
        seconds=32,
        prerequisites=("pylon",),
    ),
    _building(
        "cybernetics_core",
        description="Gateway advanced units, Warp Gate research and air upgrades.",
        minerals=150,
        seconds=36,
        prerequisites=("gateway",),
    ),
    _building(
        "photon_cannon",
        description="Static detector that attacks ground and air. Placement is automatic.",
        minerals=150,
        seconds=29,
        prerequisites=("forge",),
    ),
    _building(
        "shield_battery",
        description="Restores nearby Protoss shields. Placement is automatic. It has no weapon.",
        minerals=100,
        seconds=29,
        prerequisites=("cybernetics_core",),
    ),
    _building(
        "robotics_facility",
        description="Produces Observers, Warp Prisms and Immortals.",
        minerals=150,
        vespene=100,
        seconds=46,
        prerequisites=("cybernetics_core",),
    ),
    _building(
        "stargate",
        description="Produces Phoenixes, Oracles and Void Rays.",
        minerals=150,
        vespene=150,
        seconds=43,
        prerequisites=("cybernetics_core",),
    ),
    _building(
        "twilight_council",
        description="Charge, Blink, Resonating Glaives and advanced ground upgrades.",
        minerals=150,
        vespene=100,
        seconds=36,
        prerequisites=("cybernetics_core",),
    ),
    _building(
        "robotics_bay",
        description="Colossus and Disruptor tech, plus robotics upgrades.",
        minerals=150,
        vespene=150,
        seconds=46,
        prerequisites=("robotics_facility",),
    ),
    _building(
        "fleet_beacon",
        description="Tempest, Carrier and Mothership tech, plus air upgrade requirements.",
        minerals=300,
        vespene=200,
        seconds=43,
        prerequisites=("stargate",),
    ),
    _building(
        "templar_archives",
        description="High Templar production and Psionic Storm research.",
        minerals=150,
        vespene=200,
        seconds=36,
        prerequisites=("twilight_council",),
    ),
    _building(
        "dark_shrine",
        description="Dark Templar production.",
        minerals=150,
        vespene=150,
        seconds=71,
        prerequisites=("twilight_council",),
    ),
    _unit("probe", description="Worker: mines and builds. It does not repair.", minerals=50, supply=1, seconds=12, producer="nexus"),
    _unit("zealot", description="Ground melee fighter.", minerals=100, supply=2, seconds=27, producer="gateway"),
    _unit("stalker", description="Ground attacker that also shoots air.", minerals=125, vespene=50, supply=2, seconds=27, producer="gateway", prerequisites=("cybernetics_core",)),
    _unit("sentry", description="Support caster. Force Field, Guardian Shield and Hallucination are backend-controlled.", minerals=50, vespene=100, supply=2, seconds=23, producer="gateway", prerequisites=("cybernetics_core",)),
    _unit("adept", description="Ground ranged fighter. Shade is backend-controlled.", minerals=100, vespene=25, supply=2, seconds=30, producer="gateway", prerequisites=("cybernetics_core",)),
    _unit("high_templar", description="Ground caster. Feedback and Psionic Storm are backend-controlled after research. Archon is not a train target.", minerals=50, vespene=150, supply=2, seconds=39, producer="gateway", prerequisites=("templar_archives",)),
    _unit("dark_templar", description="Permanent cloak melee fighter. Archon is not a train target.", minerals=125, vespene=125, supply=2, seconds=39, producer="gateway", prerequisites=("dark_shrine",)),
    _unit("observer", description="Flying detector. Surveillance mode is backend-controlled and shares this identity.", minerals=25, vespene=75, supply=1, seconds=18, producer="robotics_facility"),
    _unit("warp_prism", description="Flying transport. Phasing mode is backend-controlled and shares this identity.", minerals=250, supply=2, seconds=36, producer="robotics_facility"),
    _unit("immortal", description="Armored ground attacker.", minerals=250, vespene=100, supply=4, seconds=39, producer="robotics_facility"),
    _unit("colossus", description="Tall ground splash attacker.", minerals=300, vespene=200, supply=6, seconds=54, producer="robotics_facility", prerequisites=("robotics_bay",)),
    _unit("disruptor", description="Ground area attacker. Purification Nova is backend-controlled.", minerals=150, vespene=150, supply=4, seconds=36, producer="robotics_facility", prerequisites=("robotics_bay",)),
    _unit("phoenix", description="Flying anti-air fighter. Graviton Beam is backend-controlled.", minerals=150, vespene=100, supply=2, seconds=25, producer="stargate"),
    _unit("oracle", description="Flying ground attacker and detector pulse. Weapon, revelation and stasis wards are backend-controlled.", minerals=150, vespene=150, supply=3, seconds=37, producer="stargate"),
    _unit("void_ray", description="Flying attacker against ground and air. Prismatic Alignment is backend-controlled.", minerals=250, vespene=150, supply=4, seconds=43, producer="stargate"),
    _unit("tempest", description="Long-range flying attacker against ground and air.", minerals=250, vespene=175, supply=4, seconds=43, producer="stargate", prerequisites=("fleet_beacon",)),
    _unit("carrier", description="Flying capital ship. The backend builds and releases Interceptors.", minerals=350, vespene=250, supply=6, seconds=64, producer="stargate", prerequisites=("fleet_beacon",)),
    _unit("mothership", description="Flying support capital. Time Warp is backend-controlled. Only one can exist.", minerals=400, vespene=400, supply=8, seconds=89, producer="nexus", prerequisites=("fleet_beacon",)),
    _research("warp_gate", description="Lets the backend morph completed Gateways into Warp Gates. Gateway units keep the same train names.", minerals=50, vespene=50, seconds=100, researched_at="cybernetics_core"),
    _research("charge", description="Zealots charge to their ground target. The backend uses it.", minerals=100, vespene=100, seconds=100, researched_at="twilight_council"),
    _research("blink", description="Stalkers can blink. The backend uses it.", minerals=150, vespene=150, seconds=121, researched_at="twilight_council"),
    _research("shadow_stride", description="Dark Templars can blink a short distance. The backend uses it.", minerals=100, vespene=100, seconds=100, researched_at="dark_shrine"),
    _research("resonating_glaives", description="Adepts attack faster. The backend uses it.", minerals=100, vespene=100, seconds=100, researched_at="twilight_council"),
    _research("psionic_storm", description="High Templars can cast Psionic Storm. The backend uses it.", minerals=200, vespene=200, seconds=79, researched_at="templar_archives"),
    _research("extended_thermal_lance", description="Increases Colossus attack range.", minerals=150, vespene=150, seconds=100, researched_at="robotics_bay"),
    _research("gravitic_drive", description="Increases Warp Prism speed.", minerals=100, vespene=100, seconds=57, researched_at="robotics_bay"),
    _research("gravitic_boosters", description="Increases Observer speed.", minerals=100, vespene=100, seconds=57, researched_at="robotics_bay"),
    _research("flux_vanes", description="Increases Phoenix weapon range.", minerals=150, vespene=150, seconds=64, researched_at="fleet_beacon"),
    _ability(
        "chrono_boost",
        action="chrono_boost",
        description="Spend 50 Nexus energy to Chrono Boost one structure. The backend chooses the structure, preferring one that is already producing or researching. It is not cast automatically.",
        energy=50,
        prerequisites=("nexus",),
    ),
    _ability(
        "scout",
        action="scout",
        description='Send one Probe along listed zones or route="all" for one non-own expansion-center sweep. New scout replaces unfinished work; death fails without replacement.',
        seconds=8,
        prerequisites=("probe",),
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


def _forge_levels(prefix: str, label: str, costs: Tuple[int, int, int], times: Tuple[int, int, int], extra: str) -> Tuple[TargetSpec, ...]:
    result = []
    for level, minerals, seconds in zip((1, 2, 3), costs, times):
        requirements = ("forge",)
        if level > 1:
            requirements += (f"{prefix}_{level - 1}", extra)
        result.append(_research(
            f"{prefix}_{level}",
            description=f"Increases {label.lower()} to level {level}.",
            minerals=minerals,
            vespene=minerals,
            seconds=seconds,
            researched_at="forge",
            prerequisites=requirements,
        ))
    return tuple(result)


def _air_levels(prefix: str, label: str) -> Tuple[TargetSpec, ...]:
    result = []
    costs = (100, 175, 250)
    times = (129, 154, 179)
    for level, minerals, seconds in zip((1, 2, 3), costs, times):
        requirements = ("cybernetics_core",)
        if level > 1:
            requirements += (f"{prefix}_{level - 1}", "fleet_beacon")
        result.append(_research(
            f"{prefix}_{level}",
            description=f"Increases {label.lower()} to level {level}.",
            minerals=minerals,
            vespene=minerals,
            seconds=seconds,
            researched_at="cybernetics_core",
            prerequisites=requirements,
        ))
    return tuple(result)


PROTOSS_TARGETS += _forge_levels(
    "ground_weapons", "ground weapons", (100, 150, 200), (121, 145, 168), "twilight_council",
)
PROTOSS_TARGETS += _forge_levels(
    "ground_armor", "ground armor", (100, 150, 200), (121, 145, 168), "twilight_council",
)
PROTOSS_TARGETS += _forge_levels(
    "shields", "shields", (150, 200, 250), (121, 145, 168), "twilight_council",
)
PROTOSS_TARGETS += _air_levels("air_weapons", "air weapons")
PROTOSS_TARGETS += _air_levels("air_armor", "air armor")

_TARGET_TABLE_LEGEND = (
    "M/G, supply and time are per item; time excludes waiting.",
    "Builder/producer/facility is required; extra lists other prerequisites.",
)

_TARGET_NOTES = {
    "build": (
        "- pylon provides +8 supply and powers Protoss buildings when ready.",
        "- A completed Gateway morphs into a Warp Gate after warp_gate research. Warp Gates stay the gateway identity.",
    ),
    "train": (
        "- Gateway units can be warped in after that morph. Train names do not change.",
        "- Warp Prism phasing and Observer surveillance share those train names. Archon is not a train target.",
    ),
}

_PROMPT_DESCRIPTIONS = {
    "nexus": "Expansion, Probe production and mining capacity.",
    "chrono_boost": "Chrono Boost. Backend chooses the structure. Not automatic.",
    "pylon": "Adds 8 supply and powers buildings.",
    "assimilator": "Gas mining structure.",
    "gateway": "Ground army producer. Morphs to a Warp Gate after research.",
    "forge": "Ground upgrades and Photon Cannon tech.",
    "cybernetics_core": "Advanced Gateway units, Warp Gate and air upgrades.",
    "photon_cannon": "Static detector and ground/air weapon.",
    "shield_battery": "Restores nearby shields.",
    "robotics_facility": "Observer, Warp Prism and Immortal producer.",
    "stargate": "Air producer.",
    "twilight_council": "Charge, Blink, Glaives and advanced ground upgrades.",
    "robotics_bay": "Colossus, Disruptor and robotics upgrades.",
    "fleet_beacon": "Tempest, Carrier, Mothership and advanced air upgrades.",
    "templar_archives": "High Templar and Psionic Storm.",
    "dark_shrine": "Dark Templar production and Shadow Stride.",
    "probe": "Worker: mines and builds.",
    "zealot": "Ground melee fighter.",
    "stalker": "Attacks ground and air.",
    "sentry": "Support caster.",
    "adept": "Ground ranged fighter.",
    "high_templar": "Caster. Archon is not trained.",
    "dark_templar": "Cloaked melee fighter. Archon is not trained.",
    "observer": "Flying detector.",
    "warp_prism": "Flying transport.",
    "immortal": "Armored ground attacker.",
    "colossus": "Ground splash attacker.",
    "disruptor": "Ground area attacker.",
    "phoenix": "Flying anti-air fighter.",
    "oracle": "Flying ground attacker. Stasis wards are backend-controlled.",
    "void_ray": "Flying ground and air attacker.",
    "tempest": "Long-range flying attacker.",
    "carrier": "Flying capital ship. Interceptors are backend-controlled.",
    "mothership": "Flying support capital. Only one.",
    "warp_gate": "Backend morphs completed Gateways.",
    "charge": "Zealot charge.",
    "blink": "Stalker blink.",
    "resonating_glaives": "Faster Adept attacks.",
    "psionic_storm": "High Templar storm.",
    "extended_thermal_lance": "Longer Colossus range.",
    "gravitic_drive": "Faster Warp Prism.",
    "gravitic_boosters": "Faster Observer.",
    "flux_vanes": "Longer Phoenix range.",
    "scout": "Probe route; all sweeps non-own expansions.",
}

for _prefix, _label in (
    ("ground_weapons", "Ground attack"),
    ("ground_armor", "Ground armor"),
    ("shields", "Shields"),
    ("air_weapons", "Air attack"),
    ("air_armor", "Air armor"),
):
    for _level in (1, 2, 3):
        _PROMPT_DESCRIPTIONS.setdefault(f"{_prefix}_{_level}", f"{_label} level {_level}.")

_PROMPT_DESCRIPTIONS = MappingProxyType(_PROMPT_DESCRIPTIONS)


CATALOG = CatalogData(
    race="protoss",
    targets=PROTOSS_TARGETS,
    table_legend=_TARGET_TABLE_LEGEND,
    target_notes=MappingProxyType(_TARGET_NOTES),
    prompt_descriptions=_PROMPT_DESCRIPTIONS,
)

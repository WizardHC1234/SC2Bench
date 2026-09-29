"""Warp Gate conversion and home warp-in.

Research does not morph Gateways. build warpgate converts one idle Gateway.
Converted Warp Gates stay in that form. New units warp near home.
"""
from __future__ import annotations

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from sc2bench_env.backends.sharpy.combat_styles import unit_available_for_background
from sc2bench_env.catalog.knowledge import snapshot_has_ability

_POWER_RADIUS = 6.5
_FREE_WARP_ABILITY = 4115


def warp_research_ready(ai) -> bool:
    return float(ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH)) >= 1


def home_warp_point(ai):
    """A powered point near home, away from units already on an outbound mission."""
    home = getattr(ai, "bench_group0_home_point", None) or getattr(ai, "start_location", None)
    if home is None:
        return None
    home = Point2(home)
    combat = set(getattr(ai, "bench_combat_tags", set()) or ())
    outbound = [unit.position for unit in getattr(ai, "units", []) if unit.tag in combat]
    pylons = [p for p in ai.structures(UnitTypeId.PYLON).ready if getattr(p, "is_ready", True)]

    def powered(point) -> bool:
        return any(pylon.distance_to(point) <= _POWER_RADIUS for pylon in pylons)

    def clear_of_outbound(point) -> bool:
        return all(point.distance_to(other) > 15 for other in outbound)

    candidates = [home]
    for pylon in pylons:
        candidates.append(pylon.position)
        candidates.append(pylon.position.towards(home, 2))
    pathable = getattr(ai, "in_pathing_grid", None)
    for point in candidates:
        if pathable is not None and not pathable(point):
            continue
        if powered(point) and clear_of_outbound(point):
            return point
    return None


class ActWarpGateConversion(ActBase):
    """One explicit build warpgate converts one Gateway."""

    def __init__(self, target_count: int):
        super().__init__()
        self.target_count = int(target_count)
        self.waiting_reason = None

    def reserve_pending_sources(self) -> None:
        """Hold a Gateway after its paid queue so a later train cannot refill it."""
        tags = getattr(self.ai, "bench_warp_reserve_tags", None)
        if not isinstance(tags, set):
            tags = set()
            self.ai.bench_warp_reserve_tags = tags
        done = int(self.ai.structures(UnitTypeId.WARPGATE).amount)
        need = max(0, int(self.target_count) - done)
        if need <= 0:
            return
        used = set(getattr(self.ai, "unit_tags_received_action", set()) or ())
        held = sum(1 for tag in tags if any(
            int(gate.tag) == int(tag) for gate in self.ai.structures(UnitTypeId.GATEWAY).ready
        ))
        if held >= need:
            return
        candidates = list(self.ai.structures(UnitTypeId.GATEWAY).ready)
        idle = [
            gate for gate in candidates
            if gate.tag not in used and gate.tag not in tags and not (getattr(gate, "orders", None) or [])
        ]
        busy = [
            gate for gate in candidates
            if gate.tag not in used and gate.tag not in tags and (getattr(gate, "orders", None) or [])
        ]
        for gate in idle + busy:
            tags.add(int(gate.tag))
            held += 1
            if held >= need:
                break

    async def execute(self) -> bool:
        self.waiting_reason = None
        done = int(self.ai.structures(UnitTypeId.WARPGATE).amount)
        if done >= self.target_count:
            return True
        if not warp_research_ready(self.ai):
            self.waiting_reason = "prerequisite:warp_gate"
            return False
        used = set(getattr(self.ai, "unit_tags_received_action", set()) or ())
        reserved = set(getattr(self.ai, "bench_warp_reserve_tags", set()) or ())
        idle = [
            gate for gate in self.ai.structures(UnitTypeId.GATEWAY).ready
            if gate.tag not in used and not (getattr(gate, "orders", None) or [])
        ]
        # Prefer gateways already reserved for this earlier morph request.
        preferred = [gate for gate in idle if gate.tag in reserved] or idle
        if not preferred:
            busy_reserved = [
                gate for gate in self.ai.structures(UnitTypeId.GATEWAY).ready
                if gate.tag in reserved and (getattr(gate, "orders", None) or [])
            ]
            if busy_reserved:
                self.waiting_reason = "waiting_for_queue"
            elif list(self.ai.structures(UnitTypeId.GATEWAY).ready):
                self.waiting_reason = "source_unit_reserved"
            else:
                self.waiting_reason = "source_unit_unavailable"
            return False
        gate = preferred[0]
        ability_ids = (await _raw_ability_ids(self.ai, [gate])).get(int(gate.tag), set())
        if _FREE_WARP_ABILITY in ability_ids:
            await _issue_raw_ability(self.ai, gate, _FREE_WARP_ABILITY)
            reserved.discard(int(gate.tag))
            return done + 1 >= self.target_count
        minerals, gas = _first_conversion_price()
        if int(self.ai.minerals) < minerals or int(self.ai.vespene) < gas:
            self.waiting_reason = "resources"
            return False
        gate(AbilityId.MORPH_WARPGATE)
        reserved.discard(int(gate.tag))
        return done + 1 >= self.target_count


class ActGatewayTrain(ActBase):
    """Train at an idle Gateway, or warp from a ready Warp Gate near home."""

    def __init__(self, unit_type, target_count: int):
        super().__init__()
        self.unit_type = unit_type
        self.target_count = int(target_count)
        self.waiting_reason = None

    async def execute(self) -> bool:
        self.waiting_reason = None
        owned = int(self.ai.units(self.unit_type).amount)
        pending = float(self.ai.already_pending(self.unit_type))
        if owned + pending >= self.target_count:
            return True
        used = set(getattr(self.ai, "unit_tags_received_action", set()) or ())
        reserved = set(getattr(self.ai, "bench_warp_reserve_tags", set()) or ())
        gateways = [
            gate for gate in self.ai.structures(UnitTypeId.GATEWAY).ready.idle
            if gate.tag not in used and int(gate.tag) not in reserved
        ]
        powered = [gate for gate in gateways if getattr(gate, "is_powered", True)]
        if gateways and not powered:
            self.waiting_reason = "unpowered"
            return False
        if powered and self.ai.can_afford(self.unit_type):
            powered[0].train(self.unit_type)
            return owned + pending + 1 >= self.target_count
        warpgates = [
            gate for gate in self.ai.structures(UnitTypeId.WARPGATE).ready
            if gate.tag not in used
        ]
        if not warpgates:
            any_producer = bool(list(self.ai.structures(UnitTypeId.GATEWAY).ready) or list(self.ai.structures(UnitTypeId.WARPGATE).ready))
            if powered:
                self.waiting_reason = "resources"
            elif any_producer:
                self.waiting_reason = "producer_busy"
            return False
        ready = await _ready_warpgates(self.ai, warpgates)
        if not ready:
            self.waiting_reason = "warpgate_cooldown"
            return False
        point = home_warp_point(self.ai)
        if point is None:
            self.waiting_reason = "no_powered_warp_location"
            return False
        if not self.ai.can_afford(self.unit_type):
            self.waiting_reason = "resources"
            return False
        ready[0].warp_in(self.unit_type, point)
        return owned + pending + 1 >= self.target_count


class ActArchonMerge(ActBase):
    """Merge two free templar. A lost source fails the task."""

    def __init__(self, target_count: int):
        super().__init__()
        self.target_count = int(target_count)
        self.waiting_reason = None
        self.failure_reason = None
        self._locked: tuple = ()

    async def execute(self) -> bool:
        self.waiting_reason = None
        if self.failure_reason:
            return True
        done = int(self.ai.units(UnitTypeId.ARCHON).amount)
        if done >= self.target_count:
            return True
        templar = _templar(self.ai)
        if self._locked:
            alive = {unit.tag for unit in templar}
            if any(tag not in alive for tag in self._locked) and done < self.target_count:
                self.failure_reason = "source_unit_lost"
                return True
            return False
        free = [unit for unit in templar if unit_available_for_background(unit, self.ai, require_group0=True)]
        if len(free) < 2:
            reserved = [unit for unit in templar if unit.tag in set(getattr(self.ai, "bench_combat_tags", set()) or ())]
            self.waiting_reason = "source_unit_reserved" if reserved else "source_unit_unavailable"
            return False
        first, second = free[0], free[1]
        first(AbilityId.MORPH_ARCHON, second)
        self._locked = (first.tag, second.tag)
        tags = getattr(self.ai, "bench_group0_tags", None)
        if tags is not None:
            tags.difference_update(self._locked)
        return False


class PlanWarpGateStatus(ActBase):
    """Record warp-gate cooldown. This does not morph or warp."""

    async def execute(self) -> bool:
        gates = list(self.ai.structures(UnitTypeId.WARPGATE).ready)
        if not gates:
            self.ai.bench_warpgate_status = {"total": 0, "ready": 0, "cooling": 0}
            return True
        ready = await _ready_warpgates(self.ai, gates)
        ready_tags = {gate.tag for gate in ready}
        self.ai.bench_warpgate_status = {
            "total": len(gates),
            "ready": len(ready_tags),
            "cooling": len(gates) - len(ready_tags),
        }
        return True


def _templar(ai):
    found = list(ai.units(UnitTypeId.HIGHTEMPLAR).ready)
    found.extend(ai.units(UnitTypeId.DARKTEMPLAR).ready)
    return found


async def _ready_warpgates(ai, gates):
    named = await _named_abilities(ai, gates)
    if not named and gates:
        return [gate for gate in gates if not (getattr(gate, "orders", None) or [])]
    return [gate for gate, names in named if any("WARPGATETRAIN" in name for name in names)]


def _first_conversion_price() -> tuple:
    """Catalog price. The ability table repeats the Gateway's 150 minerals."""
    from sc2bench_env.catalog.registry import get_target

    spec = get_target("warpgate", race="protoss")
    if spec is None:
        return 0, 0
    return int(spec.minerals), int(spec.vespene)


async def _raw_ability_ids(ai, units) -> dict:
    """Ability ids without constructing AbilityId. 4115 is absent from that enum."""
    from s2clientprotocol import query_pb2 as query_pb

    units = list(units)
    client = getattr(ai, "client", None)
    if client is None or not units:
        return {}
    try:
        result = await client._execute(
            query=query_pb.RequestQuery(
                abilities=(
                    query_pb.RequestQueryAvailableAbilities(unit_tag=unit.tag) for unit in units
                ),
                ignore_resource_requirements=False,
            )
        )
    except Exception:
        return {}
    found = {}
    for row in result.query.abilities:
        found[int(row.unit_tag)] = {int(item.ability_id) for item in row.abilities}
    return found


async def _issue_raw_ability(ai, unit, ability_id: int) -> None:
    from s2clientprotocol import raw_pb2 as raw_pb
    from s2clientprotocol import sc2api_pb2 as sc_pb

    command = raw_pb.ActionRawUnitCommand(
        ability_id=int(ability_id),
        unit_tags=[int(unit.tag)],
        queue_command=False,
    )
    await ai.client._execute(
        action=sc_pb.RequestAction(
            actions=[sc_pb.Action(action_raw=raw_pb.ActionRaw(unit_command=command))]
        )
    )
    used = getattr(ai, "unit_tags_received_action", None)
    if used is not None:
        used.add(unit.tag)


async def _named_abilities(ai, gates):
    gates = list(gates)
    try:
        available = await ai.get_available_abilities(gates)
    except Exception:
        return []
    if isinstance(available, dict):
        rows = [available.get(gate.tag, []) for gate in gates]
    else:
        rows = list(available or [])
    named = []
    for gate, abilities in zip(gates, rows):
        names = {getattr(ability, "name", str(ability)) for ability in abilities or []}
        named.append((gate, names))
    return named


def first_conversion_is_explicit() -> bool:
    """5.0.16 has a separate free switch. 4.10 only has the explicit morph."""
    return snapshot_has_ability("MorphBuildingGatewayWarpGateFree")

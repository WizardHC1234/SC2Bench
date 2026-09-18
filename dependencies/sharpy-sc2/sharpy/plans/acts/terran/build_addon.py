from typing import Dict, TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from sharpy.plans.acts.act_base import ActBase
from sc2.unit import Unit
from sc2.unit_command import UnitCommand

if TYPE_CHECKING:
    from sharpy.knowledges import Knowledge


class BuildAddon(ActBase):
    """Act of starting to build new buildings up to specified count"""

    # Bridge the gap between locally queued commands and the next engine
    # observation. A rejected/lost command must not reserve a host forever.
    ACK_WAIT_SECONDS = 1.0

    def __init__(self, unit_type: UnitTypeId, unit_from_type: UnitTypeId, to_count: int):
        assert unit_type is not None and isinstance(unit_type, UnitTypeId)
        assert unit_from_type is not None and isinstance(unit_from_type, UnitTypeId)
        assert to_count is not None and isinstance(to_count, int)

        self.unit_from_type = unit_from_type
        self.unit_type = unit_type
        self.to_count = to_count

        self.tried_to_build_dict: Dict[int, float] = {}

        super().__init__()

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)

    async def execute(self) -> bool:
        count = self.get_quick_count(self.unit_type)
        if count >= self.to_count:
            return True  # Step is done

        unit = self.ai._game_data.units[self.unit_type.value]
        cost = self.ai._game_data.calculate_ability_cost(unit.creation_ability)

        if not self.knowledge.can_afford(self.unit_type):
            self.knowledge.reserve(cost.minerals, cost.vespene)
            return False

        builder: Unit
        for builder in self.cache.own(self.unit_from_type).ready.idle:
            if count >= self.to_count:
                break
            if (builder.add_on_tag == 0 and not builder.is_flying
                    and builder.tag not in self.ai.unit_tags_received_action
                    and builder.tag not in self._pending_issues()):
                # Recheck after every issue: build subtracts the bank immediately.
                if not self.knowledge.can_afford(self.unit_type):
                    self.knowledge.reserve(cost.minerals, cost.vespene)
                    break

                center: Point2 = builder.position.offset(Point2((2.5, -0.5)))

                if await self.ai.find_placement(UnitTypeId.SUPPLYDEPOT, center, 0, False):
                    issued = builder.build(self.unit_type)
                    if isinstance(issued, UnitCommand):
                        issued = self.ai.do(issued, subtract_cost=True)
                    if issued:
                        self._pending_issues()[builder.tag] = (self.unit_type, self.ai.time)
                        self.tried_to_build_dict[builder.tag] = self.ai.time
                        count += 1
                        self.print(f"{self.unit_type} to {center}")
                else:
                    self.print("no space")
        return False

    def get_quick_count(self, unit_type: UnitTypeId) -> int:
        """Count entities, unrepresented engine orders and unacknowledged issues once."""
        addons = self.cache.own(unit_type)
        count = addons.amount
        for parent in self.cache.own(self.unit_from_type):
            if self._orders_addon(parent, unit_type) and not self._entity_at_host(parent, unit_type):
                count += 1
        return count + sum(kind == unit_type for kind, _ in self._pending_issues().values())

    def _orders_addon(self, parent: Unit, unit_type: UnitTypeId) -> bool:
        ability = self.ai._game_data.units[unit_type.value].creation_ability
        return any(order.ability.id == ability.id for order in parent.orders)

    def _entity_at_host(self, parent: Unit, unit_type: UnitTypeId) -> bool:
        center = parent.position.offset(Point2((2.5, -0.5)))
        return any(addon.tag == parent.add_on_tag or addon.distance_to(center) <= 1
                   for addon in self.cache.own(unit_type))

    def _pending_issues(self):
        # AI-local so separate Tech Lab/Reactor acts share host ownership and
        # counts. This is NOT completed work or persistent strategic demand.
        issues = getattr(self.ai, "_sharpy_addon_issues", None)
        if issues is None:
            issues = self.ai._sharpy_addon_issues = {}
        for tag, (kind, issued_at) in list(issues.items()):
            parent = self.ai.structures.find_by_tag(tag)
            if (parent is None or parent.add_on_tag or self._orders_addon(parent, kind)
                    or self._entity_at_host(parent, kind) or parent.orders
                    or self.ai.time - issued_at >= self.ACK_WAIT_SECONDS):
                issues.pop(tag, None)
        return issues

"""Missing construction workers are reported without misclassifying structure morphs."""
from sc2.ids.unit_typeid import UnitTypeId

from sc2bench_env.backends.sharpy.races.protoss_execution import execution_blocker as protoss_blocker
from sc2bench_env.backends.sharpy.races.terran_execution import execution_blocker as terran_blocker
from sc2bench_env.backends.sharpy.races.zerg_execution import execution_blocker as zerg_blocker
from sc2bench_env.interface.observation_text import blocker_text


class _Units(list):
    @property
    def ready(self):
        return self


class _AiWithoutWorkers:
    def units(self, _unit_type: UnitTypeId):
        return _Units()


def test_each_race_reports_its_missing_builder():
    ai = _AiWithoutWorkers()
    assert terran_blocker(ai, {"action": "build", "target": "barracks"}) == "builder_unavailable:scv"
    assert protoss_blocker(ai, {"action": "build", "target": "pylon"}) == "builder_unavailable:probe"
    assert zerg_blocker(ai, {"action": "build", "target": "spawning_pool"}) == "builder_unavailable:drone"
    assert blocker_text("builder_unavailable:drone") == "no ready drone available to construct"


def test_structure_morphs_do_not_require_a_builder():
    ai = _AiWithoutWorkers()
    assert protoss_blocker(ai, {"action": "build", "target": "warpgate"}) is None
    assert zerg_blocker(ai, {"action": "build", "target": "greater_spire"}) is None

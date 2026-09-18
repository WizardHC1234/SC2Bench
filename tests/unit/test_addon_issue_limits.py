"""Real Sharpy add-on Act: bounded issuance, shared hosts and acknowledgement."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.position import Point2
from sc2.units import Units
from sharpy.plans.acts.terran import BuildAddon
from sc2bench_env.backends.sharpy.races.terran import ADDONS


def fixture(target="barracks_techlab", count=1, hosts=3, minerals=1000, vespene=1000):
    kind, parent_type = ADDONS[target]
    ai = NS(time=0, minerals=minerals, vespene=vespene, unit_tags_received_action=set())
    entities, commands = [], []
    abilities = {addon: NS(id=addon.value) for addon, _ in ADDONS.values()}
    ai._game_data = NS(units={addon.value: NS(creation_ability=ability)
                             for addon, ability in abilities.items()},
                       calculate_ability_cost=lambda ability: NS(minerals=50, vespene=25))
    for tag in range(1, hosts + 1):
        host = NS(tag=tag, type_id=parent_type, add_on_tag=0, orders=[],
                  is_ready=True, is_idle=True, is_flying=False, position=Point2((tag * 10, 10)))

        def build(addon, host=host):
            ai.minerals -= 50
            ai.vespene -= 25
            ai.unit_tags_received_action.add(host.tag)
            commands.append((host.tag, addon))
            return True
        host.build = Mock(side_effect=build)
        entities.append(host)
    ai.structures = Units(entities, ai)
    ai.find_placement = AsyncMock(return_value=Point2((1, 1)))
    cache = NS(own=lambda unit_type: Units([e for e in ai.structures if e.type_id == unit_type], ai))
    knowledge = NS(can_afford=lambda addon: ai.minerals >= 50 and ai.vespene >= 25, reserve=Mock())

    def act_for(name=target, to_count=count):
        addon, parent = ADDONS[name]
        act = BuildAddon(addon, parent, to_count)
        act.ai, act.cache, act.knowledge = ai, cache, knowledge
        act.print = Mock()
        return act
    return ai, commands, act_for


@pytest.mark.parametrize("target", tuple(ADDONS))
def test_one_request_never_builds_on_all_three_hosts(target):
    ai, commands, make = fixture(target)
    act = make()
    asyncio.run(act.execute())
    assert len(commands) == 1
    assert act.get_quick_count(act.unit_type) == 1
    assert asyncio.run(act.execute()) is True
    assert len(commands) == 1


def test_two_cumulative_requests_use_different_hosts_same_frame():
    ai, commands, make = fixture()
    asyncio.run(make(to_count=1).execute())
    asyncio.run(make(to_count=2).execute())
    assert len(commands) == 2 and len({tag for tag, _ in commands}) == 2


def test_equal_absolute_targets_do_not_duplicate_queued_work():
    ai, commands, make = fixture()
    asyncio.run(make().execute())
    asyncio.run(make().execute())
    assert len(commands) == 1


def test_techlab_and_reactor_do_not_overwrite_same_host():
    ai, commands, make = fixture()
    asyncio.run(make().execute())
    asyncio.run(make("barracks_reactor").execute())
    assert len(commands) == 2 and len({tag for tag, _ in commands}) == 2


def test_unacknowledged_issue_is_counted_across_short_observation_gap():
    ai, commands, make = fixture()
    act = make()
    asyncio.run(act.execute())
    ai.time = 0.2
    ai.unit_tags_received_action.clear()
    asyncio.run(make().execute())
    assert len(commands) == 1


def test_acknowledged_order_without_entity_is_counted_and_not_sticky():
    ai, commands, make = fixture()
    act = make()
    asyncio.run(act.execute())
    host = ai.structures.find_by_tag(commands[0][0])
    host.orders = [NS(ability=ai._game_data.units[act.unit_type.value].creation_ability)]
    host.is_idle = False
    ai.time = 2
    ai.unit_tags_received_action.clear()
    asyncio.run(make().execute())
    assert len(commands) == 1 and not ai._sharpy_addon_issues


def test_entity_order_and_issued_command_are_not_counted_three_times():
    ai, commands, make = fixture()
    act = make()
    asyncio.run(act.execute())
    host = ai.structures.find_by_tag(commands[0][0])
    center = host.position.offset(Point2((2.5, -0.5)))
    addon = NS(tag=100, type_id=act.unit_type, position=center, is_ready=False,
               distance_to=lambda point: center.distance_to(point))
    ai.structures.append(addon)
    host.orders = [NS(ability=ai._game_data.units[act.unit_type.value].creation_ability)]
    assert act.get_quick_count(act.unit_type) == 1
    asyncio.run(make(to_count=2).execute())
    assert len(commands) == 2 and not 3 <= act.get_quick_count(act.unit_type)


def test_rejected_command_does_not_claim_budget_or_host_forever():
    ai, commands, make = fixture(hosts=1)
    host = ai.structures.first
    accepted_build = host.build.side_effect
    host.build.side_effect = None
    host.build.return_value = False
    act = make()
    asyncio.run(act.execute())
    assert not commands and not ai._sharpy_addon_issues
    host.build.side_effect = accepted_build
    asyncio.run(act.execute())
    assert len(commands) == 1


def test_missing_acknowledgement_expires_and_retries():
    ai, commands, make = fixture(hosts=1)
    asyncio.run(make().execute())
    ai.unit_tags_received_action.clear()
    ai.time = BuildAddon.ACK_WAIT_SECONDS + 0.1
    asyncio.run(make().execute())
    assert len(commands) == 2  # first queued command never reached engine


def test_dead_host_releases_transient_issue_for_another_host():
    ai, commands, make = fixture()
    asyncio.run(make().execute())
    ai.structures.remove(ai.structures.find_by_tag(commands[0][0]))
    ai.unit_tags_received_action.clear()
    asyncio.run(make().execute())
    assert len(commands) == 2 and commands[0][0] != commands[1][0]


def test_insufficient_bank_rechecked_per_issue():
    ai, commands, make = fixture(count=3, minerals=50, vespene=25)
    act = make()
    asyncio.run(act.execute())
    assert len(commands) == 1 and ai.minerals == ai.vespene == 0
    act.knowledge.reserve.assert_called_once_with(50, 25)


def test_illegal_hosts_and_placement_are_skipped_without_claiming():
    ai, commands, make = fixture(hosts=4)
    ai.structures[0].is_flying = True
    ai.structures[1].add_on_tag = 10
    ai.structures[2].is_idle = False
    ai.find_placement.return_value = None
    asyncio.run(make().execute())
    assert not commands and not ai._sharpy_addon_issues
    ai.find_placement.return_value = Point2((1, 1))
    asyncio.run(make().execute())
    assert commands[0][0] == 4

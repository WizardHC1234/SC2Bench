"""Automatic one-pass expansion scouting is explicit, observable and bounded."""

import asyncio
import copy
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sc2.position import Point2

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.acts import ActScoutRoute
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.decision_rules import decision_json_schema, schema_accepts_entry
from sc2bench_env.interface.scouting import order_expansions
from sc2bench_env.interface.race_views.terran import build_view
from sc2bench_env.runtime.task import Demand


def wait(seconds=1):
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_prompt_distinguishes_selected_checks_and_broader_search_without_policy():
    from sc2bench_env.interface.action_catalog import render_system_prompt
    from sc2bench_env.interface.platform_rules import ACTION_RULES
    rule = ACTION_RULES["scout"]
    assert "a zone ID array checks selected zones in order" in rule
    assert 'route="all" performs a broader one-pass search of non-own expansion centers' in rule
    assert "Neither guarantees full-zone visibility or all enemies found" in rule
    assert "New scout replaces old work" in rule and "death fails without replacement" in rule
    assert "no army retargeting" in rule
    assert render_system_prompt().count(rule) == 1


@pytest.mark.parametrize("route", ["all", ["zone_1", "zone_3"]])
def test_parser_schema_and_round_trip(route):
    raw = {"action": "scout", "route": route}
    batch = parse_decision([raw, wait()])
    assert batch.actions[0].to_dict() == raw
    assert schema_accepts_entry(raw) is None
    scout_schema = next(branch for branch in decision_json_schema()["items"]["oneOf"]
                        if branch["properties"]["action"]["const"] == "scout")
    branches = scout_schema["properties"]["route"]["oneOf"]
    assert branches[0] == {"type": "string", "const": "all"}
    assert branches[1]["type"] == "array"


@pytest.mark.parametrize("route", ["auto", "ALL", "zone_1", ["all"], [], None])
def test_other_route_strings_and_empty_routes_still_rejected(route):
    raw = {"action": "scout", "route": route}
    assert schema_accepts_entry(raw) is not None
    with pytest.raises(ActionValidationError):
        parse_decision([raw, wait()])


def test_route_order_uses_visibility_and_geometry_only_without_mutating_candidates():
    candidates = [("zone_1", (2, 0), True), ("zone_2", (20, 0), False),
                  ("zone_3", (15, 0), False)]
    before = copy.deepcopy(candidates)
    assert order_expansions(candidates, (0, 0)) == ["zone_3", "zone_2", "zone_1"]
    assert candidates == before


def test_automatic_observation_uses_resolved_route_copy_and_preserves_pending_unknown():
    backend = FakeBackend()
    from sc2bench_env.interface.config import EpisodeConfig
    backend.start_episode(EpisodeConfig())
    snapshot = backend.snapshot()
    demand = Demand(action="scout", target="", route="all")
    assert build_view(snapshot=snapshot, scout_demand=demand).scouting["scv"]["route"] is None
    snapshot.info["scout_progress"] = {"route": ["zone_2", "zone_3"], "moving_to": "zone_2"}
    view = build_view(snapshot=snapshot, scout_demand=demand)
    view.scouting["scv"]["route"].append("zone_4")
    assert snapshot.info["scout_progress"]["route"] == ["zone_2", "zone_3"]


class Workers(list):
    @property
    def gathering(self):
        return self

    @property
    def idle(self):
        return Workers()

    @property
    def exists(self):
        return bool(self)

    def __or__(self, other):
        return Workers(list(self) + list(other))

    def find_by_tag(self, tag):
        return next((worker for worker in self if worker.tag == tag), None)


def scout_act(route="all", *, workers=True):
    worker = NS(tag=123, position=Point2((0, 0)), move=MagicMock())
    worker.distance_to = lambda target: worker.position.distance_to(target)
    zones = {f"zone_{i}": NS(center_location=Point2((10 * i, 0)), is_ours=i == 0)
             for i in range(4)}
    registry = NS(zone_ids=tuple(zones),
                  resolve_zone=lambda manager, zone_id: zones.get(zone_id),
                  center_for=lambda zone_id: None)
    act = ActScoutRoute(route)
    act.ai = NS(zone_registry=registry, start_location=Point2((0, 0)),
                is_visible=lambda point: point.x == 10,
                workers=Workers([worker] if workers else []))
    act.zone_manager = NS()
    return act, worker


def test_real_act_expands_once_visits_all_and_completes_without_repeat():
    act, worker = scout_act()
    assert asyncio.run(act.execute()) is False
    assert act.route == ["zone_2", "zone_3", "zone_1"]
    assert act.current_zone == "zone_2" and act._scout_tag == worker.tag
    act.ai.is_visible = lambda point: False  # Does not replan the active pass.
    for zone_id in act.route:
        worker.position = act.ai.zone_registry.resolve_zone(None, zone_id).center_location
        done = asyncio.run(act.execute())
    assert done is True and act._done and act.current_zone is None
    issued = worker.move.call_count
    assert asyncio.run(act.execute()) is True
    assert worker.move.call_count == issued
    assert "zone_0" not in act.route


def test_bound_scout_death_fails_without_rebinding_available_replacement():
    act, worker = scout_act()
    asyncio.run(act.execute())
    other = NS(tag=456)
    act.ai.workers = Workers([other])
    assert asyncio.run(act.execute()) is True
    assert act.failure_reason == "scout_died" and act._scout_tag == worker.tag


def test_no_worker_waits_without_claiming_completion():
    act, _ = scout_act(workers=False)
    assert asyncio.run(act.execute()) is False
    assert act._scout_tag is None and not act._done and not act.failure_reason


def test_no_nonown_expansions_completes_empty_pass():
    act, _ = scout_act()
    for zone_id in act.ai.zone_registry.zone_ids:
        act.ai.zone_registry.resolve_zone(None, zone_id).is_ours = True
    assert asyncio.run(act.execute()) is True
    assert act._done and not act.route and not act.failure_reason


def test_invalid_center_fails_instead_of_silently_skipping_expansion():
    act, _ = scout_act()
    act.ai.zone_registry.resolve_zone(None, "zone_3").center_location = None
    assert asyncio.run(act.execute()) is True
    assert act.failure_reason == "invalid_zone:zone_3"


def test_fake_observation_records_resolved_route_replacement_and_completion():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        expected = [row["zone_id"] for row in env.backend.snapshot().info["zone_state"]
                    if row["known_owner"] != "self"]
        obs, feedback, _, _ = env.step([{"action": "scout", "route": "all"}, wait()])
        assert feedback.receipts[0].result == "accepted"
        assert obs.scouting["scv"]["mode"] == "all_expansions"
        assert obs.scouting["scv"]["route"] == expected
        assert obs.scouting["scv"]["moving_to"] == expected[0]
        assert "all_expansions" in "\n".join(obs.section_lines())
        assert any(event.get("route") == "all" for event in feedback.events)
        obs, _, _, _ = env.step([{"action": "scout", "route": ["zone_2"]}, wait()])
        assert obs.scouting["scv"]["route"] == ["zone_2"]
        assert "mode" not in obs.scouting["scv"]
        obs, _, _, _ = env.step([wait(30)])
        assert obs.scouting == {}
        env.step([{"action": "scout", "route": "all"}, wait()])
        for _ in range(3):
            obs, _, _, _ = env.step([wait(60)])
        assert obs.scouting == {}
        assert not any(d.action == "scout" for d in env.task_manager.active_demands())
    finally:
        env.close()

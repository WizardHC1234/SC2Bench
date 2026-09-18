"""Probe analysis stays offline; the model/engine run is always explicit."""
from tests.helpers.cleanup_scout_probe import line_distance, summarize_probe


def test_fixture_prefers_lateral_expansion_not_normal_main_army_path():
    assert line_distance((5, 5), (0, 0), (10, 10)) == 0
    assert line_distance((0, 10), (0, 0), (10, 10)) > 7
    assert line_distance((0, 4), (0, 0), (0, 0)) == 4
    assert line_distance((20, 0), (0, 0), (10, 0)) == 10


def turn(index, actions, *, known=False, accepted=True):
    return {"index": index, "actions": actions,
            "feedback": {"receipts": [{"action": "scout", "result": "accepted" if accepted else "rejected"}]},
            "zone_state": [{"zone_id": "zone_7", "visible_enemy_contents": {"units": {}, "buildings": {}},
                "last_seen_enemy_contents": {"units": {}, "buildings": {"command_center": 1} if known else {}}}]}


def test_accepted_scout_history_and_later_retarget_are_distinct_evidence():
    evidence = {"hidden_zone": "zone_7", "scout_discoveries": [{"game_seconds": 302}],
        "turns": [turn(1, [{"action": "scout", "route": ["zone_14"]}]),
                  turn(2, [{"action": "wait"}], known=True),
                  turn(3, [{"action": "combat", "target": "zone_7", "group": "group_1"}])]}
    result = summarize_probe(evidence, {"status": "completed", "result": "Result.Victory", "end_reason": "game_ended"})
    assert result["first_accepted_scout_turn"] == 1
    assert result["first_hidden_enemy_in_obs_turn"] == 2
    assert result["first_retarget_after_observed_discovery_turn"] == 3
    assert result["scout_engine_discovery"] and not result["used_route_all"]
    assert result["result"] == "Result.Victory"


def test_rejected_scout_or_early_guess_does_not_prove_success():
    evidence = {"hidden_zone": "zone_7", "turns": [
        turn(1, [{"action": "scout", "route": "all"}], accepted=False),
        turn(2, [{"action": "combat", "target": "zone_7"}], known=True)]}
    result = summarize_probe(evidence)
    assert result["first_accepted_scout_turn"] is None
    assert result["scout_engine_discovery"] is False
    assert result["first_retarget_after_observed_discovery_turn"] is None
    assert result["result"] is None

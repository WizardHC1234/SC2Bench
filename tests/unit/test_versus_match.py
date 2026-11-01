"""Versus scheduling without SC2: each side waits on its own advance."""

from tests.helpers.drive import play
from types import SimpleNamespace

import pytest

from sc2bench_env import AgentTurn, EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from sc2bench_env.runtime.tool_turn import ToolTurnError
from sc2bench_env.versus import VersusMatch, run_versus
from sc2.data import Result

from sc2bench_env.backends.sharpy.bot import pin_lockstep_step, reconcile_versus_result
from sc2bench_env.backends.sharpy.versus import _disconnect_after_recorded_result


def _advance(seconds: float) -> list[dict]:
    return [{"name": "advance", "arguments": {"seconds": seconds}}]


def test_one_side_does_not_advance_the_clock_alone(tmp_path):
    match = VersusMatch(backend="fake", record_dir=tmp_path)
    match.reset(EpisodeConfig(game_time_limit_seconds=30))
    assert match.waiting() == [0, 1]
    assert match.wait_next() == 0
    play(match, 0, _advance(10))
    assert match._sides[0].backend.snapshot().game_time_seconds == 0
    assert match.waiting() == [1]
    match.close()


def test_each_side_wakes_on_its_own_advance(tmp_path):
    seen = [[], []]

    def agent(index, seconds):
        def decide(request):
            seen[index].append(request.observation.game.game_time_seconds)
            request.call_tool(ToolCall("advance", {"seconds": seconds}))
            return AgentTurn()
        return decide

    match = VersusMatch(backend="fake", record_dir=tmp_path)
    outcome = run_versus(
        match, agent(0, 5), agent(1, 8),
        EpisodeConfig(game_time_limit_seconds=100),
        max_decisions=3, verbose=False,
    )
    assert seen[0] == [0, 5, 10]
    assert seen[1] == [0, 8]
    assert {row["end_reason"] for row in outcome["players"]} == {"decision_limit"}
    assert (tmp_path.iterdir())
    record_dirs = [row["record_dir"] for row in outcome["players"]]
    assert record_dirs[0].endswith("player_0")
    assert record_dirs[1].endswith("player_1")


def test_same_wake_time_serves_both_before_time_moves_again(tmp_path):
    match = VersusMatch(backend="fake", record_dir=tmp_path, record_trajectory=False)
    match.reset(EpisodeConfig(game_time_limit_seconds=30))
    assert match.wait_next() == 0
    play(match, 0, _advance(4))
    assert match.wait_next() == 1
    play(match, 1, _advance(4))
    assert match.waiting() == [0, 1]
    first = match.wait_next()
    assert match.request(first).observation.game.game_time_seconds == 4
    second = match.wait_next()
    assert second != first
    assert match.request(second).observation.game.game_time_seconds == 4
    match.close()


def test_rejected_decision_keeps_that_side_paused(tmp_path):
    match = VersusMatch(backend="fake", record_dir=tmp_path, record_trajectory=False)
    match.reset(EpisodeConfig(game_time_limit_seconds=30))
    assert match.wait_next() == 0
    with pytest.raises(ToolTurnError) as error:
        play(match, 0, [{"name": "build", "arguments": {"target": "supply_depot"}}])
    assert error.value.code == "missing_advance"
    assert 0 in match.waiting()
    match.close()


def test_unsupported_own_race_and_blocking_decisions(tmp_path):
    match = VersusMatch(backend="fake", record_dir=tmp_path, record_trajectory=False)
    try:
        try:
            match.reset(EpisodeConfig(enemy_race="random"))
            raised = False
        except ValueError:
            raised = True
        assert raised
        try:
            match.reset(EpisodeConfig(blocking_decisions=False))
            raised = False
        except ValueError:
            raised = True
        assert raised
    finally:
        match.close()


def _player(end_reason, terminated=True):
    return SimpleNamespace(_bridge=SimpleNamespace(
        snapshot=SimpleNamespace(terminated=terminated, end_reason=end_reason),
    ))


def test_locked_step_ignores_a_later_one_frame_request():
    import asyncio

    from sc2bench_env.backends.sharpy.bot import lock_client_step

    seen = []

    class Client:
        def __init__(self):
            self.game_step = 8

        async def step(self, step_size=None):
            seen.append(step_size)

    client = Client()
    lock_client_step(client, 10)
    client.game_step = 1
    asyncio.run(client.step(1))
    assert seen == [10]


def test_repeated_game_loop_cannot_shrink_one_clients_step():
    client = SimpleNamespace(game_step=10)
    bot = SimpleNamespace(
        config={"general": {"game_step_size": "10"}},
        realtime=False,
        last_game_loop=0,
        state=SimpleNamespace(game_loop=0),
        client=client,
    )
    if not bot.realtime and bot.last_game_loop == bot.state.game_loop:
        bot.realtime = True
        bot.client.game_step = 1
    pin_lockstep_step(bot)
    assert bot.realtime is False
    assert bot.client.game_step == 10


def test_both_victories_with_living_bases_are_one_desync():
    import threading

    from sc2bench_env.backends.sharpy.backend import _Bridge

    left, right = _Bridge(), _Bridge()
    left.companions = [right]
    right.companions = [left]
    left.outcome_lock = right.outcome_lock = threading.Lock()
    left.snapshot.buildings = {"hatchery": 2}
    right.snapshot.buildings = {"command_center": 1}
    right.snapshot.info = {"under_construction": {"command_center": 1}}
    left.note_raw_result(Result.Victory, {1: Result.Victory})
    assert left.snapshot.terminated is False
    assert right.snapshot.terminated is False
    right.note_raw_result(Result.Victory, {2: Result.Victory})
    assert left.snapshot.result == "Result.Tie"
    assert right.snapshot.result == "Result.Tie"
    assert left.snapshot.end_reason == "desync"
    assert right.snapshot.end_reason == "desync"


def test_victory_without_a_base_is_a_loss_for_that_side():
    import threading

    from sc2bench_env.backends.sharpy.backend import _Bridge

    left, right = _Bridge(), _Bridge()
    left.companions = [right]
    right.companions = [left]
    left.outcome_lock = right.outcome_lock = threading.Lock()
    left.snapshot.buildings = {"hatchery": 1}
    right.snapshot.buildings = {}
    left.note_raw_result(Result.Victory, {1: Result.Victory})
    right.note_raw_result(Result.Victory, {2: Result.Victory})
    assert left.snapshot.result == "Result.Victory"
    assert right.snapshot.result == "Result.Defeat"
    assert right.snapshot.end_reason == "game_ended"


def test_protocol_defeat_is_kept_when_the_snapshot_still_shows_a_base():
    import threading

    from sc2bench_env.backends.sharpy.backend import _Bridge

    left, right = _Bridge(), _Bridge()
    left.companions = [right]
    right.companions = [left]
    left.outcome_lock = right.outcome_lock = threading.Lock()
    left.snapshot.buildings = {"hatchery": 1}
    right.snapshot.buildings = {"command_center": 1}
    left.note_raw_result(Result.Victory, {1: Result.Victory, 2: Result.Defeat})
    right.note_raw_result(Result.Defeat, {1: Result.Victory, 2: Result.Defeat})
    assert left.snapshot.result == "Result.Victory"
    assert right.snapshot.result == "Result.Defeat"


def test_both_victories_are_recorded_as_a_desync():
    assert reconcile_versus_result(
        Result.Victory, {1: Result.Victory, 2: Result.Victory},
    ) == ("Result.Tie", "desync")
    assert reconcile_versus_result(
        Result.Victory, {1: Result.Victory, 2: Result.Defeat},
    ) == ("Result.Victory", None)


def test_recorded_versus_result_ignores_the_second_client_disconnect():
    finished = (_player("game_ended"), _player("game_ended"))
    assert _disconnect_after_recorded_result(
        ConnectionResetError("Cannot write to closing transport"), finished,
    )
    assert not _disconnect_after_recorded_result(
        ConnectionResetError("Cannot write to closing transport"),
        (_player("game_ended"), _player(None, terminated=False)),
    )
    assert not _disconnect_after_recorded_result(RuntimeError("create failed"), finished)

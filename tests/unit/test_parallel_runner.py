"""Real spawn processes, no SC2 or model API, including cancellation cleanup."""

import builtins
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from sc2bench_env import EpisodeConfig
from tests.helpers.drive import scripted_advance
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.recording.reader import read_episode


def _wait():
    return [{"name": "advance", "arguments": {"seconds": 60}}]


def _runner(tmp_path, backend_factory=FakeBackend):
    return BenchmarkRunner(backend_factory=backend_factory, record_dir=tmp_path / "records")


def _until(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("Concurrent test workers did not rendezvous")
        time.sleep(0.02)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", None])
def test_invalid_parallel_limit_rejects_before_any_output(tmp_path, value):
    with pytest.raises(ValueError, match="max_parallel"):
        _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)],
                              lambda: lambda request: scripted_advance(request, seconds=60), max_parallel=value)
    assert not (tmp_path / "records").exists()


def test_missing_parallel_extra_rejects_before_any_output(tmp_path, monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "cloudpickle":
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ImportError, match=r"\[parallel\]"):
        _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)],
                              lambda: lambda request: scripted_advance(request, seconds=60), max_parallel=2)
    assert not (tmp_path / "records").exists()


def test_serial_mode_never_imports_parallel_dependencies(tmp_path, monkeypatch):
    original = builtins.__import__

    def forbidden(name, *args, **kwargs):
        if name in {"cloudpickle", "psutil", "sc2bench_env.benchmark.parallel"}:
            raise AssertionError("serial run loaded parallel dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbidden)
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)],
                                  lambda: lambda request: scripted_advance(request, seconds=60))
    assert batch["execution_policy"]["mode"] == "serial"
    assert batch["execution_policy"]["max_parallel"] == 1
    assert not batch["execution_policy"]["process_per_episode"]


def test_nonserializable_factory_rejects_without_secret_or_output(tmp_path):
    pytest.importorskip("cloudpickle")
    lock = threading.Lock()

    def factory():
        with lock:
            return lambda request: scripted_advance(request, seconds=60)

    with pytest.raises(ValueError, match="serializable"):
        _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)], factory, max_parallel=2)
    assert not (tmp_path / "records").exists()


def test_parallel_is_bounded_fresh_and_records_actual_context(tmp_path):
    pytest.importorskip("cloudpickle")
    markers = tmp_path / "starts"
    timings = tmp_path / "timings"
    markers.mkdir()
    timings.mkdir()

    def factory():
        calls = 0

        def act(request):
            nonlocal calls
            calls += 1
            started = time.time()
            (markers / str(os.getpid())).touch()
            _until(lambda: len(list(markers.iterdir())) >= 2)
            time.sleep(0.15)
            finished = time.time()
            (timings / f"{os.getpid()}.json").write_text(json.dumps({
                "pid": os.getpid(), "calls": calls, "started": started, "finished": finished,
            }), encoding="utf-8")
            return scripted_advance(request, seconds=60)
        return act

    configs = [EpisodeConfig(game_time_limit_seconds=1)] * 4
    batch = _runner(tmp_path).run(configs, factory, max_parallel=2)
    assert batch["aggregate"]["tie"] == 4 and batch["status"] == "completed"
    assert batch["execution_policy"]["max_parallel"] == 2
    assert batch["execution_policy"]["process_per_episode"] is True
    assert [row["index"] for row in batch["episodes"]] == [1, 2, 3, 4]
    pids = {row["worker_pid"] for row in batch["episodes"]}
    assert len(pids) == 4 and os.getpid() not in pids
    directories = [Path(row["record_directory"]).resolve() for row in batch["episodes"]]
    assert len(set(directories)) == 4
    assert len(list((tmp_path / "records").iterdir())) == 1
    assert len(list((next((tmp_path / "records").iterdir()) / "episodes").iterdir())) == 4
    points = []
    for row, directory in zip(batch["episodes"], directories):
        assert row["worker_exitcode"] == 0
        assert {path.name for path in directory.iterdir()} == {
            "episode.txt", "session.json", "metrics.json", "log.txt", "evaluation.jsonl.gz"}
        assert read_episode(directory)["messages"][0]["role"] == "system"
        metadata = json.loads((timings / f"{row['worker_pid']}.json").read_text(encoding="utf-8"))
        assert metadata["calls"] == 1 and metadata["pid"] == row["worker_pid"]
        points += [(metadata["started"], 1), (metadata["finished"], -1)]
    active = peak = 0
    for _, delta in sorted(points):
        active += delta
        peak = max(peak, active)
    assert peak == 2  # actual overlapping decisions, not a pretend parallel loop
    index = tmp_path / "batch.json"
    index.write_text(json.dumps(batch), encoding="utf-8")
    recomputed = Evaluator.evaluate_batch(index)
    assert recomputed["aggregate"] == batch["aggregate"]
    assert {row["worker_pid"] for row in recomputed["episodes"]} == pids


def test_free_slot_starts_next_game_without_waiting_for_slow_first_game(tmp_path):
    pytest.importorskip("cloudpickle")
    markers = tmp_path / "starts"
    markers.mkdir()

    def factory():
        def act(request):
            limit = int(request.observation.game.game_time_limit_seconds)
            (markers / str(limit)).touch()
            _until(lambda: (markers / "3").exists() if limit == 2 else (markers / "2").exists())
            return scripted_advance(request, seconds=60)
        return act

    batch = _runner(tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=seconds) for seconds in [2, 1, 3]],
        factory, max_parallel=2,
    )
    assert batch["aggregate"]["tie"] == 3
    assert [row["config"]["game_time_limit_seconds"] for row in batch["episodes"]] == [2, 1, 3]


def test_agent_backend_and_abrupt_worker_failures_do_not_stop_other_games(tmp_path):
    pytest.importorskip("cloudpickle")

    class SometimesBroken(FakeBackend):
        def start_episode(self, config):
            if config.game_time_limit_seconds == 3:
                raise RuntimeError("secret error text must not enter batch")
            return super().start_episode(config)

    def factory():
        def act(request):
            limit = request.observation.game.game_time_limit_seconds
            if limit == 2:
                raise RuntimeError("agent failed")
            if limit == 4:
                os._exit(17)
            return scripted_advance(request, seconds=60)
        return act

    batch = _runner(tmp_path, SometimesBroken).run(
        [EpisodeConfig(game_time_limit_seconds=seconds) for seconds in [2, 3, 4, 1]],
        factory, max_parallel=2,
    )
    first, second, third, fourth = batch["episodes"]
    assert first["status"] == "interrupted" and first["end_reason"] == "agent_error"
    assert second["status"] == "failed" and second["error_type"] == "RuntimeError"
    assert third["status"] == "incomplete" and third["worker_exitcode"] == 17
    assert third["record_directory"] is not None and third["result"] is None
    assert fourth["status"] == "completed" and fourth["outcome"] == "tie"
    assert batch["aggregate"]["defeat"] == 0
    assert "secret error text" not in json.dumps(batch)
    index = tmp_path / "batch.json"
    index.write_text(json.dumps(batch), encoding="utf-8")
    assert Evaluator.evaluate_batch(index)["aggregate"] == batch["aggregate"]


def test_parallel_suite_keeps_case_repetition_identity(tmp_path):
    pytest.importorskip("cloudpickle")
    data = BenchmarkSuite.load(Path(__file__).resolve().parents[2] / "benchmarks/terran_pilot.json").to_dict()
    data["episode_defaults"]["game_time_limit_seconds"] = 1
    data["repetitions"] = 2
    suite = BenchmarkSuite.from_dict(data)
    batch = _runner(tmp_path).run(suite, lambda: lambda request: scripted_advance(request, seconds=60), max_parallel=9)
    plan = suite.episode_plan()
    assert len(batch["episodes"]) == len(plan)
    assert [(row["case_id"], row["repetition"]) for row in batch["episodes"]] == [
        (item["case_id"], item["repetition"]) for item in plan]
    assert batch["suite"]["sha256"] == suite.sha256
    assert all(value["aggregate"]["tie"] == 2 for value in batch["case_results"].values())


def test_abrupt_exit_during_backend_startup_keeps_record_index(tmp_path):
    pytest.importorskip("cloudpickle")

    class AbruptStart(FakeBackend):
        def start_episode(self, config):
            if config.game_time_limit_seconds == 2:
                os._exit(19)
            return super().start_episode(config)

    batch = _runner(tmp_path, AbruptStart).run(
        [EpisodeConfig(game_time_limit_seconds=2), EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda request: scripted_advance(request, seconds=60), max_parallel=2,
    )
    first, second = batch["episodes"]
    assert first["status"] == "incomplete" and first["worker_exitcode"] == 19
    assert first["record_directory"] is not None
    assert second["outcome"] == "tie"
    index = tmp_path / "batch.json"
    index.write_text(json.dumps(batch), encoding="utf-8")
    assert Evaluator.evaluate_batch(index)["aggregate"] == batch["aggregate"]


def test_interruption_stops_owned_worker_tree_and_leaves_pending_games_unstarted(tmp_path, monkeypatch):
    pytest.importorskip("cloudpickle")
    psutil = pytest.importorskip("psutil")
    import sc2bench_env.benchmark.parallel as parallel
    monkeypatch.setattr(parallel, "CANCELLATION_GRACE_SECONDS", 0.2)
    markers = tmp_path / "starts"
    markers.mkdir()
    children_before = {child.pid for child in mp.active_children()}

    def factory():
        def act(request):
            limit = int(request.observation.game.game_time_limit_seconds)
            if limit == 2:
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                (markers / "child").write_text(str(child.pid), encoding="utf-8")
            (markers / str(limit)).write_text(str(os.getpid()), encoding="utf-8")
            _until(lambda: (markers / "2").exists() and (markers / "4").exists())
            if limit in {2, 4}:
                time.sleep(30)  # emulate an API call unresponsive to cancellation
            return scripted_advance(request, seconds=60)
        return act

    original = Evaluator.summarize
    seen = {"batches": 0}

    def interrupt_after_first_result(episodes):
        rows = list(episodes)
        if rows:
            seen["batches"] += 1
            if seen["batches"] == 1:
                raise KeyboardInterrupt()
        return original(rows)

    monkeypatch.setattr(Evaluator, "summarize", staticmethod(interrupt_after_first_result))
    with pytest.raises(KeyboardInterrupt):
        _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=n) for n in [2, 4, 1, 3]],
                              factory, max_parallel=3)
    assert not (markers / "3").exists()
    assert not (tmp_path / "records" / "runs").exists()
    assert {child.pid for child in mp.active_children()} == children_before
    for name in ["2", "4", "child"]:
        pid = int((markers / name).read_text(encoding="utf-8"))
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_parallel_example_previews_limit_without_api_or_records(tmp_path, capsys):
    from examples.run_llm_benchmark import main

    assert main(["--dry-run", "--max-parallel", "3", "--record-dir", str(tmp_path / "records")]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["episode_count"] >= 1
    assert preview["record_dir"] == str(tmp_path / "records")
    assert not (tmp_path / "records").exists()

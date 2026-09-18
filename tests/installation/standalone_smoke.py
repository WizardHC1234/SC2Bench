"""Run with the freshly wheel-installed interpreter, not the source test env.

No model calls. -I removes cwd/PYTHONPATH import assistance; the audit hook
rejects reads/imports from any explicitly forbidden old project directory.
"""

import argparse
import json
import os
import sys
import sysconfig
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--forbidden-root", type=Path, action="append", default=[])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--backend", choices=("fake", "sharpy"), default="sharpy",
                        help="fake checks a core-only install; --live requires sharpy")
    args = parser.parse_args()
    if args.live and args.backend != "sharpy":
        parser.error("--live requires --backend sharpy")
    forbidden = [path.resolve() for path in args.forbidden_root]
    before = Path.cwd()

    def audit(event, values):
        candidates = []
        if event in {"open", "os.listdir", "os.scandir"} and values:
            candidates.append(values[0])
        if event == "import" and len(values) > 1:
            candidates.append(values[1])
        for value in candidates:
            if not isinstance(value, (str, bytes, os.PathLike)):
                continue
            path = Path(os.fsdecode(value)).resolve()
            for root in forbidden:
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                raise RuntimeError("Old project access forbidden: " + str(path))

    sys.addaudithook(audit)
    from sc2bench_env import Environment
    from sc2bench_env.diagnostics import inspect_installation
    from sc2bench_env.interface.config import EpisodeConfig

    report = inspect_installation(backend=args.backend)
    assert report["status"] == "passed", report
    assert not report["legacy_dependencies"]
    import sc2bench_env
    modules = [sc2bench_env]
    if args.backend == "sharpy":
        from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
        _ensure_runtime_paths()
        import sharpy
        import sc2pathlib
        import jsonpickle
        modules.extend((sharpy, sc2pathlib, jsonpickle))
    origins = {module.__name__: str(Path(module.__file__).resolve())
               for module in modules}
    install_roots = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
    for origin in origins.values():
        assert any(Path(origin) == root or root in Path(origin).parents
                   for root in install_roots), (origin, install_roots)
    env = Environment(record_dir=args.output_root / "records")
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=1, decision_interval_seconds=1))
        assert "[Current Observation]" in env.get_context()[1]["content"]
        assert "game" in observation.to_dict()
        assert observation.section_lines()
        before_rejection = observation.game.game_time_seconds
        observation, feedback, terminated, info = env.step([{"action": "wait", "seconds": 1}])
        assert "error" in info and not terminated
        assert feedback.events[0]["type"] == "decision_rejected"
        assert observation.game.game_time_seconds == before_rejection
        messages = env.get_context()
        output = '[{"action": "wait"}]'
        _, _, terminated, _ = env.step([{"action": "wait"}], agent_context={
            "messages": messages, "assistant_content": output,
        })
        assert terminated
    finally:
        env.close()
    from sc2bench_env.recording.reader import read_episode
    episode = read_episode(env.record_path)
    assert episode["summary"]["decision_count"] == 2
    assert episode["summary"]["rejected_count"] == 1
    assert episode["interactions"][-1]["input"]["messages"] == messages
    assert episode["interactions"][-1]["output"]["assistant_content"] == output
    from sc2bench_env.benchmark import AgentInput, AgentTurn, BenchmarkRunner, BenchmarkSuite, Evaluator

    agent_calls = []
    def create_agent():
        calls = []
        agent_calls.append(calls)
        def decide(request):
            assert isinstance(request, AgentInput)
            if not calls:
                assert request.feedback is None
            else:
                assert request.feedback is not None
            assert [message["role"] for message in request.platform_messages] == ["system", "user"]
            assert "[Current Observation]" in request.platform_messages[1]["content"]
            calls.append(request.observation.game.game_time_seconds)
            return AgentTurn([{"action": "wait"}], {
                "messages": request.platform_messages, "assistant_content": output,
            })
        return decide

    fake_batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=args.output_root / "records").run(
        [EpisodeConfig(game_time_limit_seconds=1, decision_interval_seconds=1)] * 2,
        create_agent, max_decisions=2,
    )
    assert fake_batch["termination_counts"] == {"completed/time_limit": 2}
    assert Evaluator.evaluate_batch(fake_batch["summary_path"])["aggregate"] == fake_batch["aggregate"]
    directories = [(Path(fake_batch["summary_path"]).parent / row["record_directory"]).resolve()
                   for row in fake_batch["episodes"]]
    assert len(set(directories)) == 2
    assert all({path.name for path in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
               for directory in directories)
    assert len(agent_calls) == 2 and all(len(calls) == 1 for calls in agent_calls)
    suite = BenchmarkSuite.from_dict({
        "schema_version": "1", "suite_id": "installed_interface_smoke",
        "suite_version": "1", "purpose": "workflow_pilot",
        "repetitions": 2, "max_decisions": 3,
        "episode_defaults": {
            "race": "terran", "enemy_race": "random", "map_name": "KairosJunctionLE",
            "blocking_decisions": True, "decision_interval_seconds": 1,
            "game_time_limit_seconds": 2,
        },
        "cases": [
            {"case_id": "easy", "episode": {"opponent": "easy"}},
            {"case_id": "medium", "episode": {"opponent": "medium", "enemy_style": "macro"}},
        ],
    })
    suite_batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=args.output_root / "records").run(
        suite, create_agent, agent_metadata={"name": "installed_smoke"},
    )
    assert suite_batch["status"] == "completed"
    assert suite_batch["suite"]["sha256"] == suite.sha256
    assert [(row["case_id"], row["repetition"]) for row in suite_batch["episodes"]] == [
        ("easy", 1), ("easy", 2), ("medium", 1), ("medium", 2),
    ]
    assert suite_batch["termination_counts"] == {"completed/time_limit": 4}
    assert len(agent_calls) == 6 and all(len(calls) == 2 for calls in agent_calls[2:])
    evaluated_suite = Evaluator.evaluate_batch(suite_batch["summary_path"])
    assert evaluated_suite["aggregate"] == suite_batch["aggregate"]
    assert evaluated_suite["case_results"] == suite_batch["case_results"]
    suite_directories = [(Path(suite_batch["summary_path"]).parent / row["record_directory"]).resolve()
                         for row in suite_batch["episodes"]]
    assert len(set(directories + suite_directories + [env.record_path])) == 7
    for directory in suite_directories:
        assert {path.name for path in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
        saved = read_episode(directory)
        assert len(saved["interactions"]) == 2
        assert all(item["input"]["messages"] and item["output"]["assistant_content"] == output
                   for item in saved["interactions"])
    result = {"preflight": report["status"], "origins": origins, "fake_smoke": "passed",
              "rejection_recovery": "passed", "text_and_transcript": "passed",
              "fake_batch_offline": "passed", "suite_offline": "passed",
              "fresh_agent_per_episode": "passed"}
    if args.live:
        from sc2bench_env.benchmark import BenchmarkRunner, Evaluator
        batch = BenchmarkRunner(record_dir=args.output_root / "records").run(
            [EpisodeConfig(opponent="veryeasy", game_time_limit_seconds=1,
                           decision_interval_seconds=1)],
            lambda: lambda _: [{"action": "wait"}],
        )
        assert batch["termination_counts"] == {"completed/time_limit": 1}, batch
        evaluated = Evaluator.evaluate_batch(batch["summary_path"])
        assert evaluated["aggregate"]["tie"] == 1
        row = batch["episodes"][0]
        directory = (Path(batch["summary_path"]).parent / row["record_directory"]).resolve()
        assert {path.name for path in directory.iterdir()} == {
            "episode.txt", "interactions.jsonl", "replay.SC2Replay"}
        assert row["runtime_versions"]["game_version"]
        result.update(live_smoke="passed", summary_path=batch["summary_path"])
    assert Path.cwd() == before
    result["cwd_unchanged"] = True
    result["forbidden_old_roots"] = [str(path) for path in forbidden]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

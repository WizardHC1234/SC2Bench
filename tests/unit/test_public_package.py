"""Common entrypoints stay importable from the installed core package."""

import json
import subprocess
import sys

import sc2bench_env
from sc2bench_env.benchmark import AgentInput, AgentStopped, AgentTurn, BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig


def test_root_exports_reuse_existing_public_objects():
    expected = {item.__name__: item for item in (
        Environment, EpisodeConfig, AgentInput, AgentTurn, AgentStopped,
        BenchmarkRunner, BenchmarkSuite, Evaluator,
    )}
    assert set(sc2bench_env.__all__) == set(expected)
    assert all(getattr(sc2bench_env, name) is value for name, value in expected.items())


def test_root_import_does_not_load_optional_game_or_llm_libraries(tmp_path):
    result = subprocess.run([
        sys.executable, "-c",
        "import json,sys,sc2bench_env; "
        "print(json.dumps(sorted(set(sys.modules) & {'sc2','sharpy','sc2pathlib','json_repair'})))",
    ], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=20)
    assert json.loads(result.stdout) == []

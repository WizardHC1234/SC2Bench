"""SC2Bench: high-level StarCraft II Agent Benchmark environment."""

from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.benchmark import (
    AgentInput, AgentStopped, AgentTurn, BenchmarkRunner, BenchmarkSuite, Evaluator,
)
from sc2bench_env.versus import VersusMatch, run_versus

__all__ = [
    "Environment", "EpisodeConfig", "AgentInput", "AgentTurn", "AgentStopped",
    "BenchmarkRunner", "BenchmarkSuite", "Evaluator", "VersusMatch", "run_versus",
]
__version__ = "0.1.0"

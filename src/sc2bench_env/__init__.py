"""SC2Bench: high-level StarCraft II Agent Benchmark environment."""

from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.benchmark import (
    AgentInput, AgentStopped, AgentTurn, BenchmarkRunner, BenchmarkSuite, Evaluator,
)

__all__ = [
    "Environment", "EpisodeConfig", "AgentInput", "AgentTurn", "AgentStopped",
    "BenchmarkRunner", "BenchmarkSuite", "Evaluator",
]
__version__ = "0.1.0"

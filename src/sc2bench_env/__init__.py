"""SC2Bench: high-level StarCraft II Agent Benchmark environment."""

from sc2bench_env.env import Environment
from sc2bench_env.interface.agent import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall, ToolResult, ToolSpec

__all__ = [
    "Environment", "EpisodeConfig", "AgentInput", "AgentTurn", "AgentStopped",
    "ToolCall", "ToolResult", "ToolSpec",
]
__version__ = "0.1.0"

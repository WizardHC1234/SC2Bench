"""Research agents, separate from agents.llm_agent's platform test agent."""
from .agent import ExperimentAgent, create_agent
from .config import HARNESSES, HarnessConfig

__all__ = ["ExperimentAgent", "HarnessConfig", "HARNESSES", "create_agent"]

"""Explicit, serializable settings for the research agents."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

HARNESSES = ("act_only", "react", "plan_and_execute", "reflection")
PROVIDERS = ("deepseek", "deepseek_sglang", "qwen_vllm", "qwen_dashscope")


@dataclass(frozen=True)
class HarnessConfig:
    harness: str = "react"
    native_thinking: bool = False
    # No implicit call/output limit. Explicit caps include all modules/retries.
    max_calls_per_decision: int | None = None
    max_calls_per_episode: int | None = None
    max_output_tokens: int | None = None
    max_episode_output_tokens: int | None = None
    max_api_attempts: int = 3
    retry_delay_seconds: float = 1.0

    def __post_init__(self):
        if type(self.native_thinking) is not bool:
            raise ValueError("native_thinking must be a boolean")
        if self.harness not in HARNESSES:
            raise ValueError(f"harness must be one of {HARNESSES}")
        for field in ("max_api_attempts",):
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                raise ValueError(f"{field} must be a positive integer")
        for field in ("max_calls_per_decision", "max_output_tokens", "max_calls_per_episode", "max_episode_output_tokens"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{field} must be null or a positive integer")
        if (type(self.retry_delay_seconds) not in (int, float)
                or not math.isfinite(self.retry_delay_seconds) or self.retry_delay_seconds < 0):
            raise ValueError("retry_delay_seconds must be finite and nonnegative")

    def to_dict(self):
        return asdict(self)

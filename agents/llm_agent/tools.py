"""Example provider conversion from platform ToolSpec to OpenAI-style function tools."""
from __future__ import annotations


from typing import Any, Sequence

from sc2bench_env.interface.tools import ToolSpec, tool_specs


def tool_schemas(source: str | Sequence[ToolSpec] = "terran", *, race: str | None = None) -> list[dict[str, Any]]:
    """Convert the specs the platform offered. A race string is only a test convenience."""
    if race is not None:
        source = race
    specs = tool_specs(source) if isinstance(source, str) else source
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": dict(spec.parameters),
            },
        }
        for spec in specs
    ]

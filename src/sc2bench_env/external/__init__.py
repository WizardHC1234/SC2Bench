"""MCP entry so an external Agent can play one SC2Bench match."""

from sc2bench_env.external.runner import run_builtin_mcp, run_config_file, run_versus_mcp

__all__ = ["run_builtin_mcp", "run_config_file", "run_versus_mcp"]

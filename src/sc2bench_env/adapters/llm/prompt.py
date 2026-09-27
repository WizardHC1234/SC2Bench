"""Supplier-neutral system text for an LLM harness."""

from sc2bench_env.catalog.registry import render_system_prompt


def system_prompt(*, race: str = "terran") -> str:
    return render_system_prompt(race=race)

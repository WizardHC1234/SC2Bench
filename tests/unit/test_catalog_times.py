"""Visible per-item durations retain single-source data and task boundaries."""
import math

import pytest

from sc2bench_env.catalog.registry import (
    catalog_as_dicts, get_target, render_action_catalog, render_system_prompt, targets_for_action,
)
from sc2bench_env.interface.platform_rules import ACTION_RULES


@pytest.mark.parametrize("action,column", [
    ("build", "time"), ("train", "time"),
    ("research", "time"), ("morph_townhall", "time"),
])
def test_every_target_has_visible_positive_time_once(action, column):
    catalog = render_action_catalog()
    block = catalog.split(f"[{action}]\n", 1)[1].split("\n\n", 1)[0]
    headers = block.splitlines()[0].split(" | ")
    assert column in headers and "base_seconds" not in headers
    prompt = render_system_prompt()
    metadata = {row["name"]: row for row in catalog_as_dicts()}
    for spec in targets_for_action(action):
        assert math.isfinite(spec.base_time_seconds) and spec.base_time_seconds > 0
        line = next(line for line in block.splitlines() if line.startswith(spec.name + " |"))
        shown = line.split(" | ")[headers.index(column)]
        assert float(shown.removesuffix("s")) == metadata[spec.name]["base_time_seconds"]
        assert line not in prompt


def test_time_semantics_no_total_batch_deadline_or_fake_spell_time():
    prompt = render_system_prompt()
    assert "Times: approximate game seconds per item" not in prompt
    assert "Completes when construction or the structure morph starts" in ACTION_RULES["build"]
    assert "request completes on queue entry" in ACTION_RULES["research"]
    for action in ("scan", "call_mule", "supply_drop", "scout"):
        block = render_action_catalog().split(f"[{action}]\n", 1)[1].split("\n\n", 1)[0]
        assert "time (s)" not in block
    assert get_target("scout").base_time_seconds == 8  # Fake scheduling remains internal.

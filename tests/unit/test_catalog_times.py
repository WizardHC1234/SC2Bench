"""Visible per-item durations retain single-source data and task boundaries."""
import math

import pytest

from sc2bench_env.interface.action_catalog import (
    catalog_as_dicts, get_target, render_action_catalog, render_system_prompt, targets_for_action,
)
from sc2bench_env.interface.platform_rules import ACTION_RULES


@pytest.mark.parametrize("action,column", [
    ("build", "build time (s)"), ("train", "train time (s)"),
    ("research", "research time (s)"), ("upgrade", "morph time (s)"),
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
        assert float(line.split(" | ")[headers.index(column)]) == metadata[spec.name]["base_time_seconds"]
        assert prompt.splitlines().count(line) == 1


def test_time_semantics_no_total_batch_deadline_or_fake_spell_time():
    prompt = render_system_prompt()
    assert prompt.count("Times: approximate game seconds per item") == 1
    assert "from start to ready/finished" in prompt
    assert "exclude resource/queue/travel waits and interruptions" in prompt
    assert "not action completion" in prompt
    assert "Multiple producers/Reactor slots run in parallel" in prompt
    assert "unfinished entity appears, not when construction finishes" in ACTION_RULES["build"]
    assert "research enters the game queue" in ACTION_RULES["research"]
    for action in ("scan", "call_mule", "scout"):
        block = render_action_catalog().split(f"[{action}]\n", 1)[1].split("\n\n", 1)[0]
        assert "time (s)" not in block
    assert get_target("scout").base_time_seconds == 8  # Fake scheduling remains internal.

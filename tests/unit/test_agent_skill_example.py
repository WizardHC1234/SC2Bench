"""Explicit external Skill loading; no platform-default strategy or API calls."""

import copy
import json
from pathlib import Path

import pytest

from examples.agent_integration import LLMAgent, main
from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.benchmark import AgentInput, BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


@pytest.mark.parametrize("decision_summary", [False, True])
def test_skill_in_actual_context_and_records_not_platform_prompt(tmp_path, decision_summary):
    calls = []
    skill = "Use a Marine–Siege Tank army."

    def call(messages):
        calls.append(copy.deepcopy(messages))
        content = '[{"action":"wait"}]'
        if decision_summary:
            content = "No new work is needed in this short test.\n" + content
        return {"content": content, "raw_content": content, "model": "mock"}

    runner = BenchmarkRunner(backend_factory=FakeBackend, record_dir=tmp_path)
    result = runner.run([EpisodeConfig(game_time_limit_seconds=1)],
                        agent_factory=lambda: LLMAgent(call, skill_text=skill,
                            decision_summary=decision_summary, verbose=False))
    assert len(calls) == 1
    assert calls[0][0]["content"].count("[External Agent Skill]") == 1
    assert skill in calls[0][0]["content"]
    episode_dirs = [path.parent for path in tmp_path.rglob("interactions.jsonl")]
    assert len(episode_dirs) == 1
    row = read_episode(episode_dirs[0])["interactions"][0]
    assert row["input"]["messages"] == calls[0]
    assert result is not None


def test_no_skill_default_and_source_messages_unmodified():
    env = Environment(record_trajectory=False)
    try:
        obs = env.reset()
        source = env.get_context()
        before = copy.deepcopy(source)
        request = AgentInput(obs, None, source)
        default = LLMAgent(lambda messages: {}, verbose=False)
        assert "[External Agent Skill]" not in default.build_messages(request, "")[0]["content"]
        custom = LLMAgent(lambda messages: {}, skill_text="custom strategy", verbose=False)
        messages = custom.build_messages(request, "format correction")
        assert "custom strategy" in messages[0]["content"]
        assert messages[-1]["content"] == "format correction"
        assert source == before
        assert "custom strategy" not in env.get_system_prompt()
    finally:
        env.close()


def test_skill_dry_run_previews_without_starting_game(tmp_path, capsys):
    skill = Path(__file__).resolve().parents[2] / "examples" / "skills" / "tank.md"
    assert main(["--dry-run", "--skill", str(skill), "--record-dir", str(tmp_path / "records")]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["skill"] == str(skill) and preview["skill_chars"] > 0
    assert not (tmp_path / "records").exists()


def test_missing_skill_rejected_before_game(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--skill", str(tmp_path / "missing.md"), "--dry-run"])
    assert exc.value.code == 2

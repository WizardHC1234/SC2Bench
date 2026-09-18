"""One focused smoke check for the external rule-agent batch adapter."""

from tests.helpers.terran_baseline import create_rule_agent, pilot_configs
from sc2bench_env.benchmark import BenchmarkRunner
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def test_rule_agent_adapter_runs_as_fresh_external_agent_per_episode(tmp_path):
    configs = pilot_configs(2)
    assert [config.opponent for config in configs] == [
        "easy", "easy", "medium", "medium",
    ]
    assert all(config.blocking_decisions and config.game_time_limit_seconds == 1800
               for config in configs)
    assert all(config.seed is None for config in configs)
    short_configs = [EpisodeConfig(**{**config.to_dict(), "game_time_limit_seconds": 1})
                     for config in configs[:2]]
    batch = BenchmarkRunner(
        backend_factory=FakeBackend,
        record_dir=tmp_path / "records", results_dir=tmp_path / "results",
    ).run(short_configs, create_rule_agent, max_decisions=5)
    assert [row["status"] for row in batch["episodes"]] == ["completed", "completed"]
    assert batch["aggregate"]["episodes"] == 2
    assert batch["aggregate"]["total_rejected"] == 0
